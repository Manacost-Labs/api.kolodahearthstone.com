from __future__ import annotations

import asyncio
import html
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from bs4 import BeautifulSoup

from .config import (
    flaresolverr_hsguru_decks_wait_ms,
    hsguru_current_patch_period,
    hsguru_deck_catalog_concurrency,
    hsguru_deck_catalog_local_concurrency,
    hsguru_deck_catalog_local_first,
)
from .deck_decode import first_deck_code_from_text
from .firecrawl_backend import scrape_source_with_options
from .parser_control import load_resolved_public_dataset
from .scrapers.flaresolverr import fetch_via_flaresolverr
from .sources import Source
from .storage import dataset_path, read_json, write_json

HSGURU_DECKS_URL = "https://www.hsguru.com/decks"
HSGURU_REPLAYS_URL = "https://www.hsguru.com/replays"
_DECKS_PAGE_MARKER = "deck_stats_viewport"
_REPLAYS_PAGE_MARKER = 'id="replays_table"'
_CACHE_TTL_SECONDS = 6 * 60 * 60
_EMPTY_CACHE_TTL_SECONDS = 10 * 60
_CATALOG_MAX_AGE_SECONDS = 24 * 60 * 60
_ALL_CATALOG_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
# Covered archetypes are re-read once a day so their builds and statistics
# follow the growing window instead of waiting for the seven-day expiry.
_ALL_CATALOG_REFRESH_SECONDS = 20 * 60 * 60
_ALL_CATALOG_BATCH_SIZE = 20
# HSGuru lists no build below 50 games, whatever ``min_games`` asks for (also
# when logged in), and caps one page at 200 builds without pagination. An
# archetype below 50 games therefore never has a build to fetch.
DECK_MIN_GAMES = 50
_PAGE_LIMIT = 200
# A shared page that reaches its limit was crowded by prolific archetypes; each
# of its archetypes is then asked for on its own page with the full limit.
_CROWDED_SPLIT_LIMIT = _PAGE_LIMIT
# A 200-build page is about 7.5 MB; Scrape.do answered 502 at the former 25 s.
_CATALOG_PAGE_TIMEOUT_MS = 90_000
# Scrape.do (render, super_proxy) steps with their own timeout (None: the
# caller's). Deck pages are LiveView dead renders: a plain request (1 credit,
# 3-12 s) returned the same builds, games and winrates as a rendered one (5
# credits, 12-70 s) on 2026-10-25; plain occasionally answers 502 after ~30 s
# (not billed), so it gets 30 s and one rendered retry. Replay pages load
# their table after the LiveView connects and always need the browser.
# Super proxies, Decodo and the paid remote reserves are not used.
_DECKS_SCRAPE_DO_STEPS: tuple[tuple[tuple[bool, bool], int | None], ...] = (
    ((False, False), 30_000),
    ((True, False), None),
)
_REPLAYS_SCRAPE_DO_STEPS: tuple[tuple[tuple[bool, bool], int | None], ...] = (
    ((True, False), None),
)
# A page that every provider failed is asked again only after these pauses.
_PAGE_RETRY_DELAYS_SECONDS = (30.0, 120.0)
# Bumped whenever the collection rules change, so catalogs written under the
# old rules are re-read in full instead of being extended incrementally.
_CATALOG_COLLECTION_VERSION = 2
# Windows published as their own archetype catalog next to the current one
# (HSGuru's Bans window). Their builds live in period-suffixed catalogs.
CATALOG_PERIODS = ("black_empire_ban",)
# Quiet archetypes can have aggregate stats but no single build with a sample
# in the current window. Their builds come from these wider windows, tried in
# order, and every row keeps the window it was sampled from.
_FALLBACK_DECK_WINDOWS: tuple[tuple[str, int], ...] = (
    ("past_30_days", DECK_MIN_GAMES),
)
# Archetypes still without a build after every window get real deck codes from
# HSGuru's public replays of that archetype (no sample statistics).
_REPLAY_PAGE_ROWS = 20
_FALLBACK_REUSE_SECONDS = 20 * 60 * 60
# A replay is a past game, so its build stays valid; replay lookups, found or
# empty, are repeated every three days instead of every night.
_REPLAY_REUSE_SECONDS = 3 * 24 * 60 * 60
_ALLOWED_HSGURU_HOSTS = frozenset({"hsguru.com", "www.hsguru.com"})
_HSGURU_SEMANTIC_QUERY_KEYS = frozenset(
    {
        "format",
        "rank",
        "period",
        "min_games",
        "limit",
        "player_deck_archetype[]",
    }
)
# Consecutive failures before Scrape.do rests; concurrent pages fail together.
_SCRAPE_DO_CIRCUIT_THRESHOLD = 5
_SCRAPE_DO_CIRCUIT_SECONDS = 5 * 60
_DECK_CARD_MARKER = re.compile(r"id=[\"']deck_stats-")
_HSGURU_EMPTY_CATALOG_PATTERN = re.compile(
    r"\bno\s+decks\s+available\s+for\s+these\s+filters\b",
    flags=re.IGNORECASE,
)
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_inflight: dict[str, asyncio.Task[list[dict[str, Any]]]] = {}
_inflight_lock = asyncio.Lock()
_page_slots: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {}
_catalog_memory: dict[tuple[str, str, str], tuple[int, _CatalogSnapshot | None]] = {}
_matrix_period_memory: tuple[str, str, str] | None = None
_scrape_do_failures = 0
_scrape_do_open_until = 0.0

_CLASS_NAMES = {
    "deathknight": "DeathKnight",
    "demonhunter": "DemonHunter",
    "druid": "Druid",
    "hunter": "Hunter",
    "mage": "Mage",
    "paladin": "Paladin",
    "priest": "Priest",
    "rogue": "Rogue",
    "shaman": "Shaman",
    "warlock": "Warlock",
    "warrior": "Warrior",
}


@dataclass(frozen=True)
class _CatalogPage:
    html: str
    backend: str
    request_credits: int
    acquisition: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _CatalogSnapshot:
    rows: list[dict[str, Any]]
    period: str
    fetched_at: str
    backend: str
    provider_backends: tuple[str, ...]
    missing_archetypes: tuple[str, ...] = ()
    zero_sample_archetypes: tuple[str, ...] = ()
    collection_version: int = 0


class HSGuruCatalogPartial(RuntimeError):
    """A usable catalog was persisted but some targets remain unverified."""

    def __init__(
        self,
        format_name: str,
        rows: list[dict[str, Any]],
        *,
        missing_archetypes: list[str],
        zero_sample_archetypes: list[str],
    ) -> None:
        super().__init__(
            f"HSGuru {format_name} all-rank catalog is partial: "
            f"{len(missing_archetypes)} archetypes remain unverified"
        )
        self.rows = rows
        self.missing_archetypes = tuple(missing_archetypes)
        self.zero_sample_archetypes = tuple(zero_sample_archetypes)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(value).lower()).strip()


def _number(value: str) -> float | None:
    match = re.search(r"-?[\d.,]+", value)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _matrix_revision_key() -> str:
    try:
        mtime_ns = dataset_path("hsguru_meta_matrix").stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    try:
        from .parser_control import publication_cache_token

        control_token = publication_cache_token("hsguru_meta_matrix")
    except (OSError, RuntimeError, TypeError, ValueError):
        control_token = ""
    return f"{mtime_ns}:{control_token}"


def _current_matrix_metadata() -> tuple[str, str, str]:
    global _matrix_period_memory
    revision_key = _matrix_revision_key()
    cached = _matrix_period_memory
    if cached is not None and cached[0] == revision_key:
        return cached
    try:
        matrix = load_resolved_public_dataset("hsguru_meta_matrix") or {}
    except (OSError, RuntimeError, TypeError, ValueError):
        matrix = {}
    data = matrix.get("data") if isinstance(matrix, dict) else {}
    structured = data.get("structured") if isinstance(data, dict) else {}
    current_catalog = (
        structured.get("current_catalog") if isinstance(structured, dict) else {}
    )
    criteria = (
        current_catalog.get("criteria") if isinstance(current_catalog, dict) else {}
    )
    deck_catalog = (
        current_catalog.get("deck_catalog") if isinstance(current_catalog, dict) else {}
    )
    period = str(criteria.get("period") or "") if isinstance(criteria, dict) else ""
    joined_at = (
        str(deck_catalog.get("joined_at") or "")
        if isinstance(deck_catalog, dict)
        else ""
    )
    _matrix_period_memory = (revision_key, period, joined_at)
    return _matrix_period_memory


def _current_deck_period() -> str:
    configured = hsguru_current_patch_period()
    if configured and re.fullmatch(r"patch_\d+(?:\.\d+){1,3}", configured):
        return configured
    _, period, _ = _current_matrix_metadata()
    if re.fullmatch(r"(?:patch_\d+(?:\.\d+){1,3}|[a-z][a-z0-9_]{0,31})", period):
        return period
    return "past_30_days"


def current_hsguru_deck_period() -> str:
    return _current_deck_period()


def hsguru_matrix_cache_revision() -> str:
    revision_key, matrix_period, joined_at = _current_matrix_metadata()
    period = current_hsguru_deck_period() or matrix_period
    return f"{revision_key}:{period}:{joined_at}"


def _catalog_page(
    result: Any,
    *,
    expected_url: str | None = None,
    marker: str = _DECKS_PAGE_MARKER,
) -> _CatalogPage:
    html_text = str(getattr(result, "html", "") or "")
    final_url = str(getattr(result, "final_url", "") or "")
    try:
        parsed_final_url = urlsplit(final_url)
        final_port = parsed_final_url.port
    except ValueError:
        parsed_final_url = urlsplit("")
        final_port = None
    status = int(
        getattr(result, "status_code", None)
        or getattr(result, "http_status", None)
        or 0
    )
    final_url_ok = (
        parsed_final_url.scheme == "https"
        and parsed_final_url.hostname in _ALLOWED_HSGURU_HOSTS
        and parsed_final_url.username is None
        and parsed_final_url.password is None
        and final_port in {None, 443}
    )
    if expected_url:
        parsed_expected_url = urlsplit(expected_url)
        required_query = Counter(
            pair
            for pair in parse_qsl(
                parsed_expected_url.query,
                keep_blank_values=True,
            )
            if pair[0] in _HSGURU_SEMANTIC_QUERY_KEYS
        )
        actual_query = Counter(
            pair
            for pair in parse_qsl(
                parsed_final_url.query,
                keep_blank_values=True,
            )
            if pair[0] in _HSGURU_SEMANTIC_QUERY_KEYS
        )
        final_url_ok = (
            final_url_ok
            and parsed_final_url.path.rstrip("/")
            == parsed_expected_url.path.rstrip("/")
            and actual_query == required_query
        )
    if not 200 <= status <= 299 or not final_url_ok or marker not in html_text:
        raise RuntimeError("HSGuru deck catalog page is incomplete")
    return _CatalogPage(
        html=html_text,
        backend=str(getattr(result, "backend", "") or "unknown"),
        request_credits=int(getattr(result, "request_credits", 0) or 0),
    )


def _catalog_result_is_valid(
    result: Any,
    *,
    expected_url: str | None = None,
    marker: str = _DECKS_PAGE_MARKER,
) -> bool:
    try:
        _catalog_page(result, expected_url=expected_url, marker=marker)
    except (RuntimeError, TypeError, ValueError):
        return False
    return True


def _provider_attempt(result: Any, state: str) -> dict[str, Any]:
    status = int(
        getattr(result, "status_code", None)
        or getattr(result, "http_status", None)
        or 0
    )
    attempt = {
        "backend": str(getattr(result, "backend", "") or "unknown"),
        "state": state,
        "http_status": status,
        "request_credits": int(getattr(result, "request_credits", 0) or 0),
    }
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict) and attempt["backend"].startswith("scrape_do"):
        provider_attempt = metadata.get("scrapeDoAttempts")
        profile_attempt = metadata.get("scrapeDoProfileAttempt")
        if provider_attempt is not None:
            attempt["provider_attempt"] = int(provider_attempt)
        if profile_attempt is not None:
            attempt["profile_attempt"] = int(profile_attempt)
        if "scrapeDoSuperProxy" in metadata:
            attempt["super_proxy"] = bool(metadata["scrapeDoSuperProxy"])
    return attempt


def _failed_attempt(backend: str, exc: Exception) -> dict[str, Any]:
    return {
        "backend": backend,
        "state": "failed",
        "http_status": 0,
        "request_credits": 0,
        "error_type": type(exc).__name__,
    }


def _merge_failure_attempt(
    attempts: list[dict[str, Any]],
    event: dict[str, Any],
) -> None:
    def provider_family(backend: object) -> str:
        value = str(backend or "unknown")
        return "scrape_do" if value.startswith("scrape_do") else value

    if (
        attempts
        and provider_family(attempts[-1].get("backend"))
        == provider_family(event.get("backend"))
        and attempts[-1].get("state") == "rejected"
    ):
        for key in (
            "http_status",
            "error_type",
            "error_code",
            "profile_attempt",
            "provider_attempt",
            "super_proxy",
        ):
            value = event.get(key)
            if value is None or value == "":
                continue
            if key == "http_status" and not value:
                continue
            attempts[-1][key] = value
        return
    attempts.append(dict(event))


def _provider_usage(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    usage: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        state = str(attempt.get("state") or "")
        if state.startswith("skipped"):
            continue
        backend = str(attempt.get("backend") or "unknown")
        provider = (
            "scrape_do"
            if backend.startswith("scrape_do")
            else "brightdata"
            if backend == "brightdata_web_unlocker"
            else backend
        )
        unit = "billable_requests" if provider == "brightdata" else "credits"
        if provider == "flaresolverr":
            unit = "requests"
        bucket = usage.setdefault(
            provider,
            {
                "request_count": 0,
                "billable_units": 0,
                "unit": unit,
            },
        )
        bucket["request_count"] += 1
        bucket["billable_units"] += int(attempt.get("request_credits") or 0)
    return usage


def _scrape_do_credits(attempts: list[dict[str, Any]]) -> int:
    return sum(
        int(attempt.get("request_credits") or 0)
        for attempt in attempts
        if str(attempt.get("backend") or "").startswith("scrape_do")
    )


def _page_with_acquisition(
    page: _CatalogPage,
    attempts: list[dict[str, Any]],
) -> _CatalogPage:
    return _CatalogPage(
        html=page.html,
        backend=page.backend,
        request_credits=_scrape_do_credits(attempts),
        acquisition=tuple(attempts),
    )


def _slots(name: str, size: int) -> asyncio.Semaphore:
    """Per-event-loop page slots shared by every concurrent catalog fetch."""
    loop = asyncio.get_running_loop()
    current = _page_slots.get(name)
    if current is None or current[0] is not loop:
        current = (loop, asyncio.Semaphore(size))
        _page_slots[name] = current
    return current[1]


def reset_hsguru_catalog_provider_state() -> None:
    """Close the short-lived provider circuit at the start of a scheduled run."""
    global _scrape_do_failures, _scrape_do_open_until
    _scrape_do_failures = 0
    _scrape_do_open_until = 0.0
    _page_slots.clear()


def _scrape_do_circuit_open() -> bool:
    return _scrape_do_open_until > time.monotonic()


def _record_scrape_do_failure() -> None:
    global _scrape_do_failures, _scrape_do_open_until
    _scrape_do_failures += 1
    if _scrape_do_failures >= _SCRAPE_DO_CIRCUIT_THRESHOLD:
        _scrape_do_open_until = time.monotonic() + _SCRAPE_DO_CIRCUIT_SECONDS


def _record_scrape_do_success() -> None:
    global _scrape_do_failures, _scrape_do_open_until
    _scrape_do_failures = 0
    _scrape_do_open_until = 0.0


async def _fetch_catalog_page(
    source: Source,
    *,
    max_age_ms: int,
    wait_ms: int,
    timeout_ms: int,
    prefer_local_solver: bool = False,
    marker: str = _DECKS_PAGE_MARKER,
    scrape_do_steps: tuple[
        tuple[tuple[bool, bool], int | None], ...
    ] = _DECKS_SCRAPE_DO_STEPS,
) -> _CatalogPage:
    """Scrape.do ``scrape_do_steps`` in order, with the local solver as fallback."""
    errors: list[str] = []
    attempts: list[dict[str, Any]] = []

    def catalog_result_is_valid(result: Any) -> bool:
        return _catalog_result_is_valid(result, expected_url=source.url, marker=marker)

    async def fetch_from_local_solver() -> _CatalogPage | None:
        solver_result: Any | None = None
        try:
            async with _slots("local", hsguru_deck_catalog_local_concurrency()):
                solver_result = await fetch_via_flaresolverr(
                    source,
                    wait_ms=flaresolverr_hsguru_decks_wait_ms(),
                )
            page = _catalog_page(solver_result, expected_url=source.url, marker=marker)
            attempts.append(_provider_attempt(solver_result, "accepted"))
            return _page_with_acquisition(page, attempts)
        except Exception as exc:  # noqa: BLE001 - isolated provider boundary
            attempts.append(
                _provider_attempt(solver_result, "rejected")
                if solver_result is not None
                else _failed_attempt("flaresolverr", exc)
            )
            errors.append(f"flaresolverr:{type(exc).__name__}")
            return None

    if prefer_local_solver:
        local_page = await fetch_from_local_solver()
        if local_page is not None:
            return local_page

    if _scrape_do_circuit_open():
        attempts.append(
            {
                "backend": "scrape_do",
                "state": "skipped_circuit_open",
                "http_status": 0,
                "request_credits": 0,
            }
        )
    else:
        primary_attempts: list[dict[str, Any]] = []

        def observe_primary(result: Any, accepted: bool) -> None:
            primary_attempts.append(
                _provider_attempt(result, "accepted" if accepted else "rejected")
            )

        def observe_primary_failure(event: dict[str, Any]) -> None:
            _merge_failure_attempt(primary_attempts, event)

        for profile, step_timeout_ms in scrape_do_steps:
            primary_attempts.clear()
            try:
                primary = await scrape_source_with_options(
                    source,
                    formats=["html"],
                    only_main_content=True,
                    max_age_ms=max_age_ms,
                    wait_ms=wait_ms,
                    timeout_ms=min(step_timeout_ms or timeout_ms, timeout_ms),
                    skip_providers={"brightdata", "decodo", "firecrawl", "scrapfly"},
                    accept_result=catalog_result_is_valid,
                    attempt_observer=observe_primary,
                    failure_observer=observe_primary_failure,
                    scrape_do_profiles=(profile,),
                )
                attempts.extend(primary_attempts)
                _record_scrape_do_success()
                return _page_with_acquisition(
                    _catalog_page(primary, expected_url=source.url, marker=marker),
                    attempts,
                )
            except Exception as exc:  # noqa: BLE001 - isolated provider boundary
                attempts.extend(
                    list(primary_attempts) or [_failed_attempt("scrape_do", exc)]
                )
                errors.append(
                    f"scrape_do{'+render' if profile[0] else ''}:{type(exc).__name__}"
                )
        _record_scrape_do_failure()

    if not prefer_local_solver:
        local_page = await fetch_from_local_solver()
        if local_page is not None:
            return local_page
    raise RuntimeError("HSGuru deck catalog fetch failed: " + ", ".join(errors))


async def _fetch_catalog_page_with_backoff(
    source: Source,
    **options: Any,
) -> _CatalogPage:
    """Fetch within the shared page slots; a failed page waits, then retries.

    The slot is released during the pause, so other pages keep going.
    """
    for delay in (*_PAGE_RETRY_DELAYS_SECONDS, None):
        try:
            async with _slots("catalog", hsguru_deck_catalog_concurrency()):
                return await _fetch_catalog_page(source, **options)
        except Exception:
            if delay is None:
                raise
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def parse_hsguru_decks_html(
    page_html: str,
    *,
    archetype: str,
    format_name: str,
    fetched_at: str | None = None,
    trust_exact_filter: bool = False,
) -> list[dict[str, Any]]:
    """Parse only exact-archetype deck cards from a filtered HSGuru page."""
    expected_archetype = _key(archetype)
    expected_format = format_name.strip().lower()
    timestamp = fetched_at or datetime.now(UTC).isoformat()
    soup = BeautifulSoup(page_html, "lxml")
    rows: list[dict[str, Any]] = []

    for card in soup.select('[id^="deck_stats-"]'):
        copy_button = card.select_one("button[data-clipboard-text]")
        deck_text = (
            html.unescape(str(copy_button.get("data-clipboard-text") or ""))
            if copy_button
            else ""
        )
        title_match = re.search(r"^###\s+(.+?)\s*$", deck_text, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else ""
        if (
            expected_archetype
            and not trust_exact_filter
            and _key(title) != expected_archetype
        ):
            continue

        parsed_format = re.search(
            r"^#\s*Format:\s*(.+?)\s*$", deck_text, flags=re.MULTILINE
        )
        deck_format = (
            parsed_format.group(1).strip() if parsed_format else format_name.title()
        )
        if deck_format.lower() != expected_format:
            continue
        deck_code = first_deck_code_from_text(deck_text) or ""
        if not deck_code:
            continue

        deck_info = card.select_one(".decklist-info")
        class_token = (
            next(
                (
                    token
                    for token in (deck_info.get("class") or [])
                    if token in _CLASS_NAMES
                ),
                "",
            )
            if deck_info
            else ""
        )
        class_name = _CLASS_NAMES.get(class_token, "Neutral")
        card_text = card.get_text(" ", strip=True)
        games_match = re.search(r"Games:\s*([\d\s,]+)", card_text, flags=re.IGNORECASE)
        games = int(re.sub(r"\D", "", games_match.group(1))) if games_match else None
        winrate_node = card.select_one("span.tag.column span")
        winrate = (
            _number(winrate_node.get_text(" ", strip=True)) if winrate_node else None
        )
        url_match = re.search(r"https://www\.hsguru\.com/deck/\d+", deck_text)
        source_url = url_match.group(0) if url_match else ""

        rows.append(
            {
                "source_id": "hsguru_decks",
                "title": title,
                # HSGuru's exact archetype filter may return deck titles with
                # rune prefixes (for example FUU/BUU/UUB). Keep the requested
                # aggregate archetype as the API identity and the full build
                # title separately.
                "archetype": archetype if trust_exact_filter else title,
                "class": class_name,
                "format": deck_format,
                "deck_code": deck_code,
                "win_rate": winrate,
                "score": f"{games} games" if games is not None else None,
                "games": games,
                "url": source_url,
                "updated_at": timestamp,
            }
        )

    return sorted(
        rows,
        key=lambda row: (int(row.get("games") or 0), float(row.get("win_rate") or 0)),
        reverse=True,
    )


def _unrendered_deck_cards(page_html: str) -> bool:
    """Whether a deck card has no copy text yet: the page is not fully rendered."""
    if not _DECK_CARD_MARKER.search(page_html):
        return False
    cards = BeautifulSoup(page_html, "lxml").select('[id^="deck_stats-"]')
    return any(card.select_one("button[data-clipboard-text]") is None for card in cards)


def _validate_parsed_catalog_rows(
    page: _CatalogPage,
    rows: list[dict[str, Any]],
) -> None:
    if rows:
        return
    if _DECK_CARD_MARKER.search(page.html):
        if _unrendered_deck_cards(page.html):
            raise RuntimeError("HSGuru deck cards were present but could not be parsed")
        # Every card rendered but none is a usable deck (an invalid or one-card
        # deck code): the archetype has no builds. One such card used to fail
        # the whole wild catalog every night.
        return
    if not _HSGURU_EMPTY_CATALOG_PATTERN.search(page.html):
        raise RuntimeError("HSGuru empty deck catalog could not be verified")


def _catalog_source_id(
    format_name: str,
    rank: str = "legend",
    period: str | None = None,
) -> str:
    suffix = f"_{period}" if period in CATALOG_PERIODS else ""
    return f"hsguru_deck_catalog_{format_name}_{rank}{suffix}"


def _catalog_snapshot(
    format_name: str,
    rank: str = "legend",
    *,
    expected_period: str | None = None,
) -> _CatalogSnapshot | None:
    period = expected_period or _current_deck_period()
    path = dataset_path(_catalog_source_id(format_name, rank, period))
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    cache_key = (format_name, rank, period)
    cached = _catalog_memory.get(cache_key)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    try:
        payload = read_json(path) or {}
        criteria = payload.get("criteria")
        payload_period = str(
            payload.get("period")
            or (criteria.get("period") if isinstance(criteria, dict) else "")
            or ""
        )
        if payload_period != period:
            _catalog_memory[cache_key] = (mtime_ns, None)
            return None
        fetched_at_text = str(payload.get("fetched_at") or "")
        fetched_at = datetime.fromisoformat(fetched_at_text.replace("Z", "+00:00"))
        age_seconds = (datetime.now(UTC) - fetched_at.astimezone(UTC)).total_seconds()
        max_age_seconds = (
            _ALL_CATALOG_MAX_AGE_SECONDS
            if rank in {"all", "fallback"}
            else _CATALOG_MAX_AGE_SECONDS
        )
        rows = payload.get("data") if 0 <= age_seconds <= max_age_seconds else []
        valid_rows = (
            [row for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )
        if not valid_rows:
            snapshot = None
        else:
            provider_backends = payload.get("provider_backends")
            backends = (
                tuple(
                    str(backend)
                    for backend in provider_backends
                    if str(backend).strip()
                )
                if isinstance(provider_backends, list)
                else ()
            )
            backend = str(payload.get("backend") or "unknown")
            missing_archetypes = payload.get("missing_archetypes")
            zero_sample_archetypes = payload.get("zero_sample_archetypes")
            snapshot = _CatalogSnapshot(
                rows=valid_rows,
                period=payload_period,
                fetched_at=fetched_at_text,
                backend=backend,
                provider_backends=backends or (backend,),
                missing_archetypes=tuple(
                    str(name)
                    for name in (
                        missing_archetypes
                        if isinstance(missing_archetypes, list)
                        else []
                    )
                    if str(name).strip()
                ),
                zero_sample_archetypes=tuple(
                    str(name)
                    for name in (
                        zero_sample_archetypes
                        if isinstance(zero_sample_archetypes, list)
                        else []
                    )
                    if str(name).strip()
                ),
                collection_version=int(payload.get("collection_version") or 0),
            )
    except (OSError, ValueError, TypeError):
        return None
    _catalog_memory[cache_key] = (mtime_ns, snapshot)
    return snapshot


def _snapshot_needs_full_refresh(snapshot: _CatalogSnapshot) -> bool:
    if snapshot.collection_version != _CATALOG_COLLECTION_VERSION:
        return True
    try:
        fetched_at = datetime.fromisoformat(snapshot.fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if fetched_at.tzinfo is None:
        return True
    age_seconds = (datetime.now(UTC) - fetched_at.astimezone(UTC)).total_seconds()
    return age_seconds >= _ALL_CATALOG_REFRESH_SECONDS


def hsguru_catalog_periods() -> tuple[str, ...]:
    """Secondary windows whose archetype catalogs the matrix has published."""
    try:
        matrix = load_resolved_public_dataset("hsguru_meta_matrix") or {}
    except (OSError, RuntimeError, TypeError, ValueError):
        matrix = {}
    structured = (matrix.get("data") or {}).get("structured") or {}
    published = structured.get("period_catalogs") or {}
    if not isinstance(published, dict):
        return ()
    return tuple(period for period in CATALOG_PERIODS if period in published)


def _catalog_rows(
    format_name: str,
    rank: str = "legend",
    *,
    expected_period: str | None = None,
) -> list[dict[str, Any]]:
    snapshot = _catalog_snapshot(
        format_name,
        rank,
        expected_period=expected_period,
    )
    return snapshot.rows if snapshot is not None else []


def cached_hsguru_catalog_decks(
    archetype: str,
    format_name: str,
    rank: str,
    *,
    period: str | None = None,
) -> list[dict[str, Any]]:
    expected = _key(archetype)
    expected_period = period or _current_deck_period()

    def matching_rows(catalog_rank: str) -> list[dict[str, Any]]:
        return [
            row
            for row in _catalog_rows(
                format_name,
                catalog_rank,
                expected_period=expected_period,
            )
            if _key(str(row.get("archetype") or row.get("title") or "")) == expected
            and str(row.get("format") or "").strip().lower() == format_name
        ]

    primary_rank = rank if rank in {"legend", "all"} else ""
    rows = matching_rows(primary_rank) if primary_rank else []
    if not rows and rank != "all":
        # Deck composition is safe to reuse across rank filters, while its
        # sample statistics are not. Return the all-rank code immediately but
        # clear rank-specific metrics so consumers never display mismatched WR.
        rows = [
            {
                **row,
                "games": None,
                "score": None,
                "win_rate": None,
                "sample_rank": "all",
            }
            for row in matching_rows("all")
        ]
    if not rows:
        # Builds from a wider window for archetypes that have none in the
        # current one. Each row carries its own sample_period.
        rows = [
            row
            if rank == "all"
            else {**row, "games": None, "score": None, "win_rate": None}
            for row in matching_rows("fallback")
        ]
    return sorted(
        rows,
        key=lambda row: (int(row.get("games") or 0), float(row.get("win_rate") or 0)),
        reverse=True,
    )


def _meta_archetypes(
    format_name: str,
    *,
    period: str | None = None,
    min_games: int = DECK_MIN_GAMES,
) -> list[str]:
    target_period = period or _current_deck_period()
    try:
        matrix = load_resolved_public_dataset("hsguru_meta_matrix") or {}
    except (OSError, RuntimeError, TypeError, ValueError):
        matrix = {}
    structured = (matrix.get("data") or {}).get("structured") or {}
    current_catalog = (
        (structured.get("period_catalogs") or {}).get(target_period)
        if target_period in CATALOG_PERIODS
        else structured.get("current_catalog")
    ) or {}
    criteria = current_catalog.get("criteria") or {}
    current_rows = current_catalog.get("archetypes") or []
    if (
        isinstance(criteria, dict)
        and str(criteria.get("period") or "") == target_period
        and isinstance(current_rows, list)
    ):
        current_archetypes: dict[str, str] = {}
        for row in current_rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("format") or "").strip().lower() != format_name:
                continue
            # The catalog lists archetypes down to 10 games, but no build of
            # an archetype below HSGuru's 50-game deck floor can be listed.
            if int(row.get("games") or 0) < min_games:
                continue
            name = str(row.get("archetype") or "").strip()
            if name:
                current_archetypes.setdefault(_key(name), name)
        if current_archetypes:
            return list(current_archetypes.values())

    # Rolling catalogs predate the patch-scoped matrix. Keep that legacy route
    # only for an explicitly rolling request; it must never seed a patch file.
    if target_period != "past_30_days":
        return []
    archetypes: dict[str, str] = {}
    for rank in ("legend", "diamond_4to1", "top_5k", "top_legend"):
        try:
            payload = (
                load_resolved_public_dataset(f"hsguru_meta_{format_name}_{rank}") or {}
            )
        except (OSError, ValueError, TypeError):
            continue
        data = payload.get("data") if isinstance(payload, dict) else {}
        tables = data.get("tables") if isinstance(data, dict) else []
        for table in tables if isinstance(tables, list) else []:
            rows = table.get("rows") if isinstance(table, dict) else []
            for row in rows if isinstance(rows, list) else []:
                name = str(row[0] if isinstance(row, list) and row else "").strip()
                if name:
                    archetypes.setdefault(_key(name), name)
    return sorted(archetypes.values(), key=str.casefold)


def _all_rank_catalog_archetypes(
    format_name: str,
    catalog_rows: list[dict[str, Any]] | None = None,
    *,
    period: str | None = None,
    zero_sample_archetypes: tuple[str, ...] | list[str] = (),
    min_games: int = DECK_MIN_GAMES,
) -> list[str]:
    rows = (
        catalog_rows if catalog_rows is not None else _catalog_rows(format_name, "all")
    )
    catalog_keys = {
        _key(str(row.get("archetype") or row.get("title") or ""))
        for row in rows
        if isinstance(row, dict)
    }
    catalog_keys.update(_key(name) for name in zero_sample_archetypes)
    return [
        name
        for name in _meta_archetypes(format_name, period=period, min_games=min_games)
        if _key(name) not in catalog_keys
    ]


def _catalog_chunks(
    archetypes: list[str], size: int = _ALL_CATALOG_BATCH_SIZE
) -> list[list[str]]:
    return [
        archetypes[index : index + size] for index in range(0, len(archetypes), size)
    ]


def _merge_catalog_rows(*collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        (
            row
            for collection in collections
            for row in collection
            if isinstance(row, dict)
        ),
        key=lambda row: (int(row.get("games") or 0), float(row.get("win_rate") or 0)),
        reverse=True,
    )
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = (
            _key(str(row.get("archetype") or row.get("title") or "")),
            str(row.get("deck_code") or "").strip(),
        )
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        merged.append(row)
    return merged


# HSGuru's deck-info names Death Knight builds with their rune spread
# ("BUU Egg DK") where the meta table and its deck filter say "Egg DK".
_DK_RUNE_SPREAD = re.compile(r"\b[BFU]{3}\s+")


def _requested_archetype(name: str, requested: dict[str, str]) -> str | None:
    """Return the requested archetype ``name`` denotes, if any."""
    without_runes = _DK_RUNE_SPREAD.sub("", name)
    for candidate in (name, without_runes, re.sub(r"^XL\s+", "", without_runes)):
        match = requested.get(_key(candidate))
        if match:
            return match
    return None


async def _canonicalize_catalog_archetypes(
    rows: list[dict[str, Any]],
    requested: list[str] | None = None,
) -> None:
    """Name each row by HSGuru's archetype, preferring a requested name.

    A shared page lists only the archetypes it was filtered by, so a row whose
    canonical name is a more specific spelling of one of them (a DK rune
    spread) keeps the requested name and joins that archetype's catalog row.
    """
    requested_keys = {_key(name): name for name in requested or []}
    deck_codes = list(
        dict.fromkeys(
            str(row.get("deck_code") or "").strip()
            for row in rows
            if str(row.get("deck_code") or "").strip()
        )
    )
    if not deck_codes:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                "https://api.hsguru.com/api/deck-info",
                json={"decks": deck_codes},
                headers={"User-Agent": "HSDataAPI/1.0"},
            )
            response.raise_for_status()
            info_by_code = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        if not requested_keys:
            return
        # Without deck-info the page headings still name requested archetypes.
        info_by_code = {}
    for row in rows:
        info = (
            info_by_code.get(str(row.get("deck_code") or ""), {})
            if isinstance(info_by_code, dict)
            else {}
        )
        canonical = (
            str(info.get("archetype") or "").strip() if isinstance(info, dict) else ""
        )
        if requested_keys:
            match = _requested_archetype(canonical, requested_keys) or (
                _requested_archetype(str(row.get("archetype") or ""), requested_keys)
            )
            if match:
                row["archetype"] = match
                continue
        if canonical:
            row["archetype"] = canonical


async def _fetch_catalog_chunk(
    format_name: str,
    archetypes: list[str],
    *,
    batch_number: int,
    period: str,
    min_games: int = DECK_MIN_GAMES,
    limit: int = _PAGE_LIMIT,
) -> tuple[list[dict[str, Any]], int, str, list[dict[str, Any]]]:
    format_id = 2 if format_name == "standard" else 1
    params: list[tuple[str, object]] = [
        ("format", format_id),
        ("rank", "all"),
        ("period", period),
        ("min_games", min_games),
        ("limit", limit),
        *(("player_deck_archetype[]", archetype) for archetype in archetypes),
    ]
    source = Source(
        id=_catalog_source_id(format_name, "all", period),
        url=str(httpx.URL(HSGURU_DECKS_URL, params=params)),
        site="hsguru",
        category="deck_catalog",
    )
    page = await _fetch_catalog_page_with_backoff(
        source,
        max_age_ms=_CACHE_TTL_SECONDS * 1_000,
        wait_ms=3_000,
        timeout_ms=_CATALOG_PAGE_TIMEOUT_MS,
        # This scheduled fan-out spans hundreds of archetypes per window.
        # Scrape.do runs it in parallel; the single local browser is the
        # fallback (or goes first with HS_HSGURU_DECK_CATALOG_LOCAL_FIRST).
        prefer_local_solver=hsguru_deck_catalog_local_first(),
    )

    def parse(candidate: _CatalogPage) -> list[dict[str, Any]]:
        return parse_hsguru_decks_html(
            candidate.html,
            archetype=archetypes[0] if len(archetypes) == 1 else "",
            format_name=format_name,
            trust_exact_filter=len(archetypes) == 1,
        )

    rows = parse(page)
    credits = page.request_credits
    attempts = list(page.acquisition)
    if (
        not rows
        and page.backend == "flaresolverr"
        and _unrendered_deck_cards(page.html)
    ):
        # The local browser can hand over deck cards before their copy buttons
        # render. One unreadable page used to fail the whole catalog for days;
        # Scrape.do renders that page again instead.
        page = await _fetch_catalog_page_with_backoff(
            source,
            max_age_ms=0,
            wait_ms=3_000,
            timeout_ms=_CATALOG_PAGE_TIMEOUT_MS,
        )
        rows = parse(page)
        credits += page.request_credits
        attempts.extend(page.acquisition)
    _validate_parsed_catalog_rows(page, rows)
    if len(archetypes) > 1:
        await _canonicalize_catalog_archetypes(rows, archetypes)
    acquisition = [
        {
            **attempt,
            "batch": batch_number,
            "archetype_count": len(archetypes),
        }
        for attempt in attempts
    ]
    if len(archetypes) > 1 and len(rows) >= limit:
        singles = await asyncio.gather(
            *(
                _fetch_catalog_chunk(
                    format_name,
                    [name],
                    batch_number=batch_number,
                    period=period,
                    min_games=min_games,
                    limit=_CROWDED_SPLIT_LIMIT,
                )
                for name in archetypes
            )
        )
        for single_rows, single_credits, _, single_attempts in singles:
            rows.extend(single_rows)
            credits += single_credits
            acquisition.extend(single_attempts)
    return rows, credits, page.backend, acquisition


async def _fetch_catalog_chunks(
    format_name: str,
    archetypes: list[str],
    *,
    period: str,
    size: int = _ALL_CATALOG_BATCH_SIZE,
    min_games: int = DECK_MIN_GAMES,
    limit: int = _PAGE_LIMIT,
) -> tuple[list[dict[str, Any]], int, list[str], list[dict[str, Any]]]:
    # Every chunk starts at once; the shared page slots bound the parallelism.
    results = await asyncio.gather(
        *(
            _fetch_catalog_chunk(
                format_name,
                chunk,
                batch_number=index + 1,
                period=period,
                min_games=min_games,
                limit=limit,
            )
            for index, chunk in enumerate(_catalog_chunks(archetypes, size))
        )
    )
    rows: list[dict[str, Any]] = []
    credits = 0
    backends: list[str] = []
    attempts: list[dict[str, Any]] = []
    for batch_rows, batch_credits, batch_backend, batch_attempts in results:
        rows.extend(batch_rows)
        credits += batch_credits
        backends.append(batch_backend)
        attempts.extend(batch_attempts)
    return rows, credits, backends, attempts


def _catalog_sample_state(
    rows: list[dict[str, Any]],
    period: str,
    *,
    zero_sample_archetypes: list[str] | None = None,
) -> str:
    if period.startswith("patch_") and (len(rows) < 20 or bool(zero_sample_archetypes)):
        return "sparse_post_patch"
    return "complete"


def _minimum_catalog_rows(period: str) -> int:
    return 1 if period.startswith("patch_") else 20


def _write_catalog(
    format_name: str,
    rank: str,
    rows: list[dict[str, Any]],
    *,
    period: str,
    credits_used: int,
    backends: list[str],
    attempts: list[dict[str, Any]],
    missing_archetypes: list[str] | None = None,
    zero_sample_archetypes: list[str] | None = None,
    sample_state: str = "complete",
    retained_snapshot: _CatalogSnapshot | None = None,
    acquired_rows: int | None = None,
) -> None:
    fetched_at = datetime.now(UTC).isoformat()
    for row in rows:
        if not row.get("updated_at"):
            row["updated_at"] = fetched_at
        row["sample_period"] = period
        row["sample_rank"] = rank
    source_id = _catalog_source_id(format_name, rank, period)
    provider_backends = list(dict.fromkeys(backends))
    backend = (
        provider_backends[0]
        if len(provider_backends) == 1
        else "mixed"
        if provider_backends
        else "none"
    )
    retained = None
    if retained_snapshot is not None:
        retained = {
            "period": retained_snapshot.period,
            "fetched_at": retained_snapshot.fetched_at,
            "backend": retained_snapshot.backend,
            "provider_backends": list(retained_snapshot.provider_backends),
            "row_count": len(retained_snapshot.rows),
            "missing_count": len(retained_snapshot.missing_archetypes),
            "zero_sample_count": len(retained_snapshot.zero_sample_archetypes),
        }
    write_json(
        dataset_path(source_id),
        {
            "source_id": source_id,
            "collection_version": _CATALOG_COLLECTION_VERSION,
            "state": "ok" if not missing_archetypes else "partial",
            "sample_state": sample_state,
            "fetched_at": fetched_at,
            "http_status": 200,
            "final_url": HSGURU_DECKS_URL,
            "backend": backend,
            "provider_backends": provider_backends,
            "credits_used": credits_used,
            "credits_scope": "scrape_do",
            "period": period,
            "criteria": {
                "format": format_name,
                "rank": rank,
                "period": period,
            },
            "acquisition": {
                "period": period,
                "backends": provider_backends,
                "request_credits": credits_used,
                "request_credits_scope": "scrape_do",
                "provider_usage": _provider_usage(attempts),
                "attempts": attempts,
                "candidate_rows": len(rows) if acquired_rows is None else acquired_rows,
            },
            "retained_snapshot": retained,
            "missing_archetypes": missing_archetypes or [],
            "zero_sample_archetypes": zero_sample_archetypes or [],
            "data": rows,
        },
    )
    _catalog_memory.clear()


async def _refresh_all_rank_catalog(
    format_name: str,
    *,
    period: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch every builds page an archetype of ``period`` can have.

    Chunk pages and single-archetype pages ask for HSGuru's 50-game deck
    floor, so one pass answers every target: a target without a row after it
    has no build HSGuru lists and is recorded as zero-sample. A snapshot older
    than a day, or written under older collection rules, is re-read in full.
    """
    period = period or _current_deck_period()
    existing_rows = _catalog_rows(
        format_name,
        "all",
        expected_period=period,
    )
    snapshot = (
        _catalog_snapshot(format_name, "all", expected_period=period)
        if existing_rows
        else None
    )
    targets = _meta_archetypes(format_name, period=period)
    if not targets:
        if existing_rows:
            return existing_rows
        raise RuntimeError(
            f"HSGuru {format_name} current-patch archetype catalog is unavailable"
        )
    if snapshot is not None and _snapshot_needs_full_refresh(snapshot):
        # Re-read every archetype; rows of the stale snapshot would otherwise
        # keep their old statistics and hide newer builds for up to a week.
        existing_rows = []
    covered_keys = {
        _key(str(row.get("archetype") or row.get("title") or ""))
        for row in existing_rows
        if isinstance(row, dict)
    }
    missing = [name for name in targets if _key(name) not in covered_keys]
    if existing_rows and not missing:
        return existing_rows

    batch_rows, credits, backends, attempts = await _fetch_catalog_chunks(
        format_name,
        missing,
        period=period,
    )
    merged = _merge_catalog_rows(existing_rows, batch_rows)
    if not merged:
        raise RuntimeError(f"HSGuru {format_name} all-rank catalog is empty")
    merged_keys = {
        _key(str(row.get("archetype") or row.get("title") or ""))
        for row in merged
        if isinstance(row, dict)
    }
    zero_sample_archetypes = [name for name in targets if _key(name) not in merged_keys]
    _write_catalog(
        format_name,
        "all",
        merged,
        period=period,
        credits_used=credits,
        backends=backends,
        attempts=attempts,
        missing_archetypes=[],
        zero_sample_archetypes=zero_sample_archetypes,
        sample_state=_catalog_sample_state(
            merged,
            period,
            zero_sample_archetypes=zero_sample_archetypes,
        ),
        retained_snapshot=snapshot,
        acquired_rows=len(batch_rows),
    )
    return merged


def _fallback_windows(period: str) -> tuple[tuple[str, int], ...]:
    """Wider windows, narrowest first, whose builds stand in for ``period``.

    A secondary window (Bans) first borrows from the whole current release,
    then from the rolling month; the current window only from the month.
    """
    release = _current_deck_period() if period in CATALOG_PERIODS else ""
    windows = [(release, DECK_MIN_GAMES)] if release else []
    windows.extend(_FALLBACK_DECK_WINDOWS)
    return tuple(
        dict.fromkeys(window for window in windows if window[0] and window[0] != period)
    )


def _replay_windows(period: str) -> tuple[str, ...]:
    """Replay windows for ``period``: itself, then the release for Bans."""
    release = _current_deck_period() if period in CATALOG_PERIODS else ""
    return tuple(dict.fromkeys(window for window in (period, release) if window))


def _seconds_since(value: object) -> float | None:
    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return (datetime.now(UTC) - moment.astimezone(UTC)).total_seconds()


def parse_hsguru_replays_html(
    page_html: str,
    *,
    archetype: str,
    format_name: str,
) -> list[dict[str, Any]]:
    """Distinct deck codes of an archetype's public replays, newest first."""
    soup = BeautifulSoup(page_html, "lxml")
    table = soup.find(id="replays_table") or soup.find("table")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tr in table.select("tbody tr") if table else []:
        cell = tr.find("td")
        button = cell.find(attrs={"data-clipboard-text": True}) if cell else None
        text = str(button.get("data-clipboard-text") or "") if button else ""
        deck_code = first_deck_code_from_text(text)
        if not deck_code or deck_code in seen:
            continue
        seen.add(deck_code)
        info = cell.find(class_="decklist-info")
        class_token = next(
            (
                token
                for token in (info.get("class") or [] if info else [])
                if token in _CLASS_NAMES
            ),
            "",
        )
        url_match = re.search(r"https://www\.hsguru\.com/deck/\d+", text)
        cells = tr.find_all("td")
        rows.append(
            {
                "source_id": "hsguru_replays",
                "title": archetype,
                "archetype": archetype,
                "class": _CLASS_NAMES.get(class_token, "Neutral"),
                "format": format_name.capitalize(),
                "deck_code": deck_code,
                "win_rate": None,
                "score": None,
                "games": None,
                "url": url_match.group(0) if url_match else "",
                "played": cells[-1].get_text(" ", strip=True) if cells else "",
                "sample_source": "replays",
            }
        )
    return rows


async def _fetch_replay_decks(
    format_name: str,
    archetype: str,
    *,
    period: str,
    windows: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int, list[str], list[dict[str, Any]], str | None]:
    """Deck codes from the first window whose replays list the archetype."""
    credits = 0
    backends: list[str] = []
    attempts: list[dict[str, Any]] = []
    for window in windows:
        params: list[tuple[str, object]] = [
            ("format", 2 if format_name == "standard" else 1),
            ("period", window),
            ("player_deck_archetype[]", archetype),
        ]
        source = Source(
            id=_catalog_source_id(format_name, "fallback", period),
            url=str(httpx.URL(HSGURU_REPLAYS_URL, params=params)),
            site="hsguru",
            category="deck_replays",
        )
        try:
            page = await _fetch_catalog_page_with_backoff(
                source,
                max_age_ms=_CACHE_TTL_SECONDS * 1_000,
                wait_ms=3_000,
                timeout_ms=_CATALOG_PAGE_TIMEOUT_MS,
                prefer_local_solver=hsguru_deck_catalog_local_first(),
                marker=_REPLAYS_PAGE_MARKER,
                scrape_do_steps=_REPLAYS_SCRAPE_DO_STEPS,
            )
        except Exception as exc:  # noqa: BLE001 - one archetype must not stop others
            attempts.append({**_failed_attempt("hsguru", exc), "window": window})
            return [], credits, backends, attempts, type(exc).__name__
        credits += page.request_credits
        backends.append(page.backend)
        attempts.extend(
            {**attempt, "window": window, "page": "replays"}
            for attempt in page.acquisition
        )
        rows = parse_hsguru_replays_html(
            page.html, archetype=archetype, format_name=format_name
        )[:_REPLAY_PAGE_ROWS]
        if rows:
            fetched_at = datetime.now(UTC).isoformat()
            return (
                [
                    {
                        **row,
                        "updated_at": fetched_at,
                        "sample_period": window,
                        "sample_rank": "all",
                        "sample_fallback_for": period,
                    }
                    for row in rows
                ],
                credits,
                backends,
                attempts,
                None,
            )
    return [], credits, backends, attempts, None


def _row_age_seconds(row: dict[str, Any]) -> float | None:
    try:
        updated_at = datetime.fromisoformat(
            str(row.get("updated_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if updated_at.tzinfo is None:
        return None
    return (datetime.now(UTC) - updated_at.astimezone(UTC)).total_seconds()


async def _fetch_fallback_decks(
    format_name: str,
    archetype: str,
    *,
    batch_number: int,
    period: str,
    windows: tuple[tuple[str, int], ...],
) -> tuple[list[dict[str, Any]], int, list[str], list[dict[str, Any]], str | None]:
    credits = 0
    backends: list[str] = []
    attempts: list[dict[str, Any]] = []
    for window, min_games in windows:
        try:
            rows, page_credits, backend, acquisition = await _fetch_catalog_chunk(
                format_name,
                [archetype],
                batch_number=batch_number,
                period=window,
                min_games=min_games,
                limit=_PAGE_LIMIT,
            )
        except Exception as exc:  # noqa: BLE001 - one archetype must not stop others
            attempts.append(
                {
                    **_failed_attempt("hsguru", exc),
                    "window": window,
                    "min_games": min_games,
                }
            )
            return [], credits, backends, attempts, type(exc).__name__
        credits += page_credits
        backends.append(backend)
        attempts.extend(
            {**attempt, "window": window, "min_games": min_games}
            for attempt in acquisition
        )
        if rows:
            return (
                [
                    {
                        **row,
                        "sample_period": window,
                        "sample_rank": "all",
                        "sample_min_games": min_games,
                        "sample_fallback_for": period,
                    }
                    for row in rows
                ],
                credits,
                backends,
                attempts,
                None,
            )
    return [], credits, backends, attempts, None


async def refresh_hsguru_fallback_deck_catalog(
    format_name: str,
    *,
    period: str | None = None,
) -> dict[str, Any]:
    """Give every archetype without a ``period`` build one from elsewhere.

    Runs after the all-rank refresh and only for archetypes it left without
    rows. Archetypes with a 50-game sample get one exact page per wider window
    (``_fallback_windows``); every archetype still without a build, down to the
    catalog's 10-game floor, then gets deck codes from its public replays in
    ``period`` and those windows. Rows keep the window and source they came
    from. Builds fetched within ``_FALLBACK_REUSE_SECONDS`` are reused instead
    of paid for again. Once ``period`` gains a build, the all-rank catalog wins.
    """
    if format_name not in {"standard", "wild"}:
        raise ValueError("Unsupported HSGuru catalog format")
    period = period or _current_deck_period()
    windows = _fallback_windows(period)
    all_rows = _catalog_rows(format_name, "all", expected_period=period)
    targets = _all_rank_catalog_archetypes(format_name, all_rows, period=period)
    replay_targets = _all_rank_catalog_archetypes(
        format_name, all_rows, period=period, min_games=1
    )
    summary: dict[str, Any] = {
        "state": "ok",
        "period": period,
        "targets": len(targets),
        "reused": 0,
        "fetched": 0,
        "with_decks": 0,
        "decks": 0,
        "zero_sample_archetypes": [],
        "failed_archetypes": [],
        "credits_used": 0,
    }
    if not replay_targets:
        return summary

    target_keys = {_key(name) for name in replay_targets}
    previous = _catalog_snapshot(format_name, "fallback", expected_period=period)
    reusable = (
        previous.rows
        if previous is not None
        and previous.collection_version == _CATALOG_COLLECTION_VERSION
        else []
    )
    reused_rows = [
        row
        for row in reusable
        if _key(str(row.get("archetype") or "")) in target_keys
        and str(row.get("sample_fallback_for") or "") == period
        and (age := _row_age_seconds(row)) is not None
        and 0
        <= age
        <= (
            _REPLAY_REUSE_SECONDS
            if row.get("sample_source") == "replays"
            else _FALLBACK_REUSE_SECONDS
        )
    ]
    previous_payload = (
        read_json(dataset_path(_catalog_source_id(format_name, "fallback", period)))
        if previous is not None
        and previous.collection_version == _CATALOG_COLLECTION_VERSION
        else None
    ) or {}
    previous_checks = previous_payload.get("replay_checks")
    recent_replay_checks = {
        _key(str(name)): str(checked_at)
        for name, checked_at in (
            previous_checks.items() if isinstance(previous_checks, dict) else []
        )
        if (age := _seconds_since(checked_at)) is not None
        and 0 <= age <= _REPLAY_REUSE_SECONDS
    }
    reused_keys = {_key(str(row.get("archetype") or "")) for row in reused_rows}
    pending = (
        [name for name in targets if _key(name) not in reused_keys] if windows else []
    )

    rows: list[dict[str, Any]] = []
    credits = 0
    backends: list[str] = []
    attempts: list[dict[str, Any]] = []
    failed: list[str] = []

    def collect(names: list[str], results: list[Any]) -> None:
        nonlocal credits
        for archetype, result in zip(names, results, strict=True):
            item_rows, item_credits, item_backends, item_attempts, error = result
            rows.extend(item_rows)
            credits += item_credits
            backends.extend(item_backends)
            attempts.extend(item_attempts)
            if error is not None:
                failed.append(archetype)

    collect(
        pending,
        await asyncio.gather(
            *(
                _fetch_fallback_decks(
                    format_name,
                    archetype,
                    batch_number=index + 1,
                    period=period,
                    windows=windows,
                )
                for index, archetype in enumerate(pending)
            )
        ),
    )
    covered = reused_keys | {_key(str(row.get("archetype") or "")) for row in rows}
    failed_keys = {_key(name) for name in failed}
    replay_pending = [
        name
        for name in replay_targets
        if (key := _key(name)) not in covered
        and key not in failed_keys
        and key not in recent_replay_checks
    ]
    replay_windows = _replay_windows(period)
    collect(
        replay_pending,
        await asyncio.gather(
            *(
                _fetch_replay_decks(
                    format_name,
                    archetype,
                    period=period,
                    windows=replay_windows,
                )
                for archetype in replay_pending
            )
        ),
    )

    merged = _merge_catalog_rows(reused_rows, rows)
    covered_keys = {_key(str(row.get("archetype") or "")) for row in merged}
    failed_keys = {_key(name) for name in failed}
    zero_sample = [
        name
        for name in replay_targets
        if (key := _key(name)) not in covered_keys and key not in failed_keys
    ]
    checked_at = datetime.now(UTC).isoformat()
    asked_keys = {_key(name) for name in replay_pending}
    replay_checks = {
        name: recent_replay_checks.get(_key(name), checked_at)
        for name in zero_sample
        if _key(name) in recent_replay_checks or _key(name) in asked_keys
    }
    source_id = _catalog_source_id(format_name, "fallback", period)
    provider_backends = list(dict.fromkeys(backends))
    write_json(
        dataset_path(source_id),
        {
            "source_id": source_id,
            "collection_version": _CATALOG_COLLECTION_VERSION,
            "state": "ok" if not failed else "partial",
            "fetched_at": datetime.now(UTC).isoformat(),
            "backend": (
                provider_backends[0]
                if len(provider_backends) == 1
                else "mixed"
                if provider_backends
                else "none"
            ),
            "provider_backends": provider_backends,
            "credits_used": credits,
            "credits_scope": "scrape_do",
            "period": period,
            "criteria": {
                "format": format_name,
                "rank": "all",
                "period": period,
                "windows": [
                    {"period": window, "min_games": min_games}
                    for window, min_games in windows
                ],
                "replay_windows": list(replay_windows),
            },
            "acquisition": {
                "period": period,
                "backends": provider_backends,
                "request_credits": credits,
                "request_credits_scope": "scrape_do",
                "provider_usage": _provider_usage(attempts),
                "attempts": attempts,
                "candidate_rows": len(rows),
            },
            "missing_archetypes": failed,
            "zero_sample_archetypes": zero_sample,
            # Archetypes whose replays listed no build, and when they were
            # last asked; they are asked again after _REPLAY_REUSE_SECONDS.
            "replay_checks": replay_checks,
            "data": merged,
        },
    )
    _catalog_memory.clear()
    summary.update(
        state="ok" if not failed else "partial",
        targets=len(replay_targets),
        reused=len(reused_keys),
        fetched=len(pending) + len(replay_pending),
        replay_fetched=len(replay_pending),
        replay_decks=sum(1 for row in merged if row.get("sample_source") == "replays"),
        with_decks=len(covered_keys),
        decks=len(merged),
        zero_sample_archetypes=zero_sample,
        failed_archetypes=failed,
        credits_used=credits,
    )
    return summary


async def refresh_hsguru_deck_catalog(
    format_name: str,
    rank: str = "legend",
    *,
    period: str | None = None,
) -> list[dict[str, Any]]:
    if format_name not in {"standard", "wild"}:
        raise ValueError("Unsupported HSGuru catalog format")
    if rank not in {"legend", "all"}:
        raise ValueError("Unsupported HSGuru catalog rank")
    run_period = period or _current_deck_period()
    if rank == "all":
        return await _refresh_all_rank_catalog(format_name, period=run_period)
    format_id = 2 if format_name == "standard" else 1
    params: list[tuple[str, object]] = [
        ("format", format_id),
        ("rank", rank),
        ("period", run_period),
        ("min_games", 10),
        ("limit", 200),
    ]
    url = str(httpx.URL(HSGURU_DECKS_URL, params=params))
    source = Source(
        id=_catalog_source_id(format_name, rank),
        url=url,
        site="hsguru",
        category="deck_catalog",
    )
    page = await _fetch_catalog_page_with_backoff(
        source,
        max_age_ms=_CACHE_TTL_SECONDS * 1_000,
        wait_ms=3_000,
        timeout_ms=_CATALOG_PAGE_TIMEOUT_MS,
    )
    rows = parse_hsguru_decks_html(
        page.html,
        archetype="",
        format_name=format_name,
    )
    _validate_parsed_catalog_rows(page, rows)
    if len(rows) < _minimum_catalog_rows(run_period):
        raise RuntimeError(f"HSGuru deck catalog is unexpectedly small: {len(rows)}")
    await _canonicalize_catalog_archetypes(rows)
    _write_catalog(
        format_name,
        rank,
        rows,
        period=run_period,
        credits_used=page.request_credits,
        backends=[page.backend],
        attempts=list(page.acquisition),
        sample_state=_catalog_sample_state(rows, run_period),
        acquired_rows=len(rows),
    )
    return rows


async def _fetch_attempt(
    archetype: str, format_name: str, params: list[tuple[str, object]]
) -> list[dict[str, Any]]:
    format_id = 2 if format_name == "standard" else 1
    url = str(
        httpx.URL(
            HSGURU_DECKS_URL,
            params=[
                ("format", format_id),
                ("player_deck_archetype[]", archetype),
                *params,
            ],
        )
    )
    source = Source(
        id=_catalog_source_id(format_name, "all"),
        url=url,
        site="hsguru",
        category="exact_deck",
    )
    page = await _fetch_catalog_page(
        source,
        # Firecrawl can reuse a recent identical lookup across API restarts. The
        # in-process result cache below still controls the public response TTL.
        max_age_ms=_CACHE_TTL_SECONDS * 1_000,
        wait_ms=3_000,
        timeout_ms=25_000,
    )
    rows = parse_hsguru_decks_html(
        page.html,
        archetype=archetype,
        format_name=format_name,
        trust_exact_filter=True,
    )
    _validate_parsed_catalog_rows(page, rows)
    return rows


async def _fetch_exact(
    archetype: str,
    format_name: str,
    rank: str,
    *,
    period: str | None = None,
) -> list[dict[str, Any]]:
    target_period = period or _current_deck_period()
    attempts = [(rank, [("rank", rank), ("period", target_period), ("min_games", 10)])]
    if rank != "all":
        attempts.append(
            (
                "all",
                [("rank", "all"), ("period", target_period), ("min_games", 10)],
            )
        )
    last_error: Exception | None = None
    for sample_rank, params in attempts:
        try:
            rows = await _fetch_attempt(archetype, format_name, params)
        except Exception as exc:
            last_error = exc
            continue
        if rows:
            if rank != "all" and sample_rank == "all":
                return [
                    {
                        **row,
                        "games": None,
                        "score": None,
                        "win_rate": None,
                        "sample_rank": "all",
                        "sample_period": target_period,
                    }
                    for row in rows
                ]
            return [
                {
                    **row,
                    "sample_rank": sample_rank,
                    "sample_period": target_period,
                }
                for row in rows
            ]
    if last_error is not None:
        raise last_error
    return []


async def exact_hsguru_decks(
    archetype: str, format_name: str, rank: str
) -> list[dict[str, Any]]:
    period = _current_deck_period()
    catalog_rows = cached_hsguru_catalog_decks(
        archetype,
        format_name,
        rank,
        period=period,
    )
    if catalog_rows:
        return catalog_rows
    cache_key = f"{period}:{format_name}:{rank}:{_key(archetype)}"
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    async with _inflight_lock:
        task = _inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(
                _fetch_exact(
                    archetype,
                    format_name,
                    rank,
                    period=period,
                )
            )
            _inflight[cache_key] = task
    try:
        rows = await task
        ttl = _CACHE_TTL_SECONDS if rows else _EMPTY_CACHE_TTL_SECONDS
        _cache[cache_key] = (time.monotonic() + ttl, rows)
        return rows
    finally:
        async with _inflight_lock:
            if _inflight.get(cache_key) is task:
                _inflight.pop(cache_key, None)
