from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from bs4 import BeautifulSoup, Tag

from .firecrawl_backend import scrape_source_with_options
from .publish_gate import validate_candidate_for_publish
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import SOURCE_BY_ID, Source
from .storage import (
    load_baseline,
    load_dataset,
    save_baseline,
    save_dataset,
    save_status,
)

SOURCE_ID = "hsguru_archetype_analysis"
SCHEMA_VERSION = 2
CHECKPOINT_SCHEMA_VERSION = 3
ANALYSIS_RANK = "legend"
ANALYSIS_PERIOD = "past_week"
CARD_STATS_MIN_MULL_COUNT = 25
CARD_STATS_MIN_DRAWN_COUNT = 25
FORMAT_IDS = {"standard": 2, "wild": 1}
CARD_STATS_UNAVAILABLE_RETRY = timedelta(hours=1)
CARD_STATS_NEGATIVE_CACHE_VERSION = 2
ANALYSIS_WAIT_MS = 0
ANALYSIS_TIMEOUT_MS = 45_000
PROVIDER_CIRCUIT_FAILURE_THRESHOLD = 3
CHECKPOINT_INTERVAL_TARGETS = 5
CHECKPOINT_TTL = timedelta(hours=2)
CHECKPOINT_LABEL = "refresh_checkpoint_v1"
CHECKPOINT_RECOVERY_TTL = timedelta(hours=12)
CHECKPOINT_RECOVERY_ABSOLUTE_TTL = timedelta(hours=36)
CHECKPOINT_RECOVERY_COOLDOWN = timedelta(minutes=30)
CHECKPOINT_RECOVERY_MAX_TARGETS = 20
CHECKPOINT_RECOVERY_PROVIDER_FAILURE_BUDGET = 4

CLASS_KEYS = {
    "death knight": "deathknight",
    "demon hunter": "demonhunter",
    "druid": "druid",
    "hunter": "hunter",
    "mage": "mage",
    "paladin": "paladin",
    "priest": "priest",
    "rogue": "rogue",
    "shaman": "shaman",
    "warlock": "warlock",
    "warrior": "warrior",
}


def _header(value: str) -> str:
    return re.sub(r"[^a-z]+", " ", value.casefold()).strip()


def _number(value: Any) -> float | None:
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _count(value: Any) -> int | None:
    text = str(value or "").split("(", 1)[0]
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _table_with_headers(
    html: str,
    required: set[str],
) -> tuple[Tag | None, dict[str, int]]:
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        header_row = table.find("thead") or table.find("tr")
        if header_row is None:
            continue
        cells = header_row.find_all(["th", "td"])
        headers = [_header(cell.get_text(" ", strip=True)) for cell in cells]
        indexes = {name: headers.index(name) for name in required if name in headers}
        if len(indexes) == len(required):
            return table, indexes
    return None, {}


def parse_class_matchups_html(html: str) -> list[dict[str, Any]]:
    required = {"class", "winrate", "total games"}
    table, indexes = _table_with_headers(html, required)
    if table is None:
        return []

    rows: list[dict[str, Any]] = []
    table_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for tr in table_rows:
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(indexes.values()):
            continue
        class_label = cells[indexes["class"]].get_text(" ", strip=True)
        class_key = CLASS_KEYS.get(_header(class_label))
        if not class_key:
            continue
        games_text = cells[indexes["total games"]].get_text(" ", strip=True)
        winrate = _number(cells[indexes["winrate"]].get_text(" ", strip=True))
        games = _count(games_text)
        share_match = re.search(r"\(([-+]?\d+(?:[.,]\d+)?)\s*%\)", games_text)
        share = _number(share_match.group(1)) if share_match else None
        if winrate is None or not 0 <= winrate <= 100 or games is None:
            continue
        rows.append(
            {
                "class_key": class_key,
                "class_label": class_label,
                "winrate": winrate,
                "games": games,
                "share_pct": share,
            }
        )
    return rows


def _attribute_in_tree(cell: Tag, *names: str) -> str | None:
    for node in (cell, *cell.find_all(True)):
        for name in names:
            value = node.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _card_identity(cell: Tag) -> tuple[str | None, int | None, str]:
    name_node = cell.select_one(".card-name")
    if name_node is not None:
        name = name_node.get_text(" ", strip=True)
        for hidden in name_node.select('[style*="font-size: 0"]'):
            name = name.replace(hidden.get_text(" ", strip=True), "")
    else:
        name = (
            _attribute_in_tree(cell, "data-card-name", "aria-label", "alt")
            or cell.get_text(" ", strip=True)
        )
    card_id = _attribute_in_tree(cell, "data-card-id", "data-cardid")
    dbf_raw = _attribute_in_tree(cell, "data-dbf-id", "data-dbfid")
    dbf_id = int(dbf_raw) if dbf_raw and dbf_raw.isdigit() else None

    link = cell.find("a", href=True)
    if link is not None:
        parsed = urlparse(str(link.get("href") or ""))
        query = parse_qs(parsed.query)
        card_id = card_id or next(iter(query.get("card_id", [])), None)
        dbf_query = next(iter(query.get("dbf_id", [])), None)
        if dbf_id is None and dbf_query and dbf_query.isdigit():
            dbf_id = int(dbf_query)
        path_match = re.search(r"/card/([^/?#]+)(?:/([^/?#]+))?", parsed.path)
        if path_match:
            first, second = path_match.groups()
            if first.isdigit():
                dbf_id = dbf_id or int(first)
                card_id = card_id or second
            else:
                card_id = card_id or first

    image = cell.find("img", src=True)
    if card_id is None and image is not None:
        image_match = re.search(
            r"/(?:tiles|render/[^/]+/[^/]+)/([^/.?]+)",
            str(image.get("src") or ""),
        )
        if image_match:
            card_id = image_match.group(1)

    if dbf_id is not None:
        try:
            from .cards_index import cards_by_dbfid

            metadata = cards_by_dbfid().get(dbf_id) or {}
            card_id = card_id or metadata.get("id")
            name = name or str(metadata.get("name") or "")
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    return card_id, dbf_id, re.sub(r"\s+", " ", name).strip()


def parse_card_stats_html(html: str) -> list[dict[str, Any]]:
    required = {
        "card",
        "mulligan impact",
        "mulligan count",
        "drawn impact",
        "drawn count",
        "kept impact",
        "kept count",
    }
    table, indexes = _table_with_headers(html, required)
    if table is None:
        return []

    rows: list[dict[str, Any]] = []
    table_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for tr in table_rows:
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(indexes.values()):
            continue
        card_id, dbf_id, card_name = _card_identity(cells[indexes["card"]])
        mulligan_count = _count(cells[indexes["mulligan count"]].get_text(" ", strip=True))
        if not card_name or mulligan_count is None:
            continue
        rows.append(
            {
                "card_id": card_id,
                "dbf_id": dbf_id,
                "card_name": card_name,
                "mulligan_impact": _number(
                    cells[indexes["mulligan impact"]].get_text(" ", strip=True)
                ),
                "mulligan_count": mulligan_count,
                "drawn_impact": _number(
                    cells[indexes["drawn impact"]].get_text(" ", strip=True)
                ),
                "drawn_count": _count(
                    cells[indexes["drawn count"]].get_text(" ", strip=True)
                ),
                "kept_impact": _number(
                    cells[indexes["kept impact"]].get_text(" ", strip=True)
                ),
                "kept_count": _count(
                    cells[indexes["kept count"]].get_text(" ", strip=True)
                ),
            }
        )
    return rows


def parse_card_stats_games(html: str) -> int | None:
    """Return the HSGuru sample size without treating a sparse table as broken."""
    soup = BeautifulSoup(html, "lxml")
    marker = soup.find(
        string=re.compile(r"^\s*Games\s*:\s*[\d\s,.]+\s*$", re.IGNORECASE)
    )
    if marker is None:
        return None
    match = re.search(r"Games\s*:\s*([\d\s,.]+)", str(marker), re.IGNORECASE)
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def _qualified_card_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if int(row.get("mulligan_count") or 0) >= CARD_STATS_MIN_MULL_COUNT
        and int(row.get("drawn_count") or 0) >= CARD_STATS_MIN_DRAWN_COUNT
    ]


def analysis_urls(archetype: str, format_name: str) -> dict[str, str]:
    format_id = FORMAT_IDS[format_name]
    filters = {
        "format": format_id,
        "rank": ANALYSIS_RANK,
        "period": ANALYSIS_PERIOD,
    }
    return {
        "matchups": (
            f"https://www.hsguru.com/archetype/{quote(archetype)}?"
            f"{urlencode(filters)}"
        ),
        "cards": (
            "https://www.hsguru.com/card-stats?"
            + urlencode(
                {
                    "archetype": archetype,
                    **filters,
                    # Fetch the unfiltered aggregate so post-patch sparse data can
                    # be distinguished from a missing upstream aggregate. The
                    # public 25/25 threshold is applied locally.
                    "min_mull_count": 0,
                    "min_drawn_count": 0,
                    "show_counts": "yes",
                }
            )
        ),
    }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _utc_now().isoformat()


def _active_archetypes() -> list[dict[str, str]]:
    dataset = load_dataset("hsguru_meta_matrix") or {}
    structured = ((dataset.get("data") or {}).get("structured") or {})
    active: list[dict[str, str]] = []
    expected_keys = {
        f"{format_name}|{ANALYSIS_RANK}|{ANALYSIS_PERIOD}|any_player": format_name
        for format_name in FORMAT_IDS
    }
    for item in structured.get("slices") or []:
        if not isinstance(item, dict):
            continue
        format_name = expected_keys.get(str(item.get("key") or ""))
        if format_name is None:
            continue
        for row in item.get("rows") or []:
            if not isinstance(row, dict):
                continue
            archetype = str(row.get("archetype") or "").strip()
            if archetype:
                active.append({"format": format_name, "archetype": archetype})
    return active


def _previous_analysis() -> dict[tuple[str, str], dict[str, Any]]:
    dataset = load_dataset(SOURCE_ID) or {}
    rows = ((dataset.get("data") or {}).get("structured") or {}).get("archetypes") or []
    return {
        (str(row.get("format") or ""), str(row.get("archetype") or "").casefold()): dict(row)
        for row in rows
        if isinstance(row, dict)
    }


def _previous_negative_cache() -> dict[tuple[str, str, str], dict[str, Any]]:
    dataset = load_dataset(SOURCE_ID) or {}
    entries = (
        ((dataset.get("data") or {}).get("structured") or {}).get("negative_cache")
        or []
    )
    return {
        (
            str(entry.get("format") or ""),
            str(entry.get("archetype") or "").casefold(),
            str(entry.get("kind") or ""),
        ): dict(entry)
        for entry in entries
        if isinstance(entry, dict)
    }


def _target_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("format") or ""),
        str(row.get("archetype") or "").casefold(),
    )


def _target_signature(targets: list[dict[str, str]]) -> str:
    normalized = sorted(
        f"{format_name}\0{archetype}"
        for format_name, archetype in (_target_key(target) for target in targets)
    )
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def _validated_checkpoint_targets(
    checkpoint: dict[str, Any],
) -> list[dict[str, str]] | None:
    raw_targets = checkpoint.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        return None

    targets: list[dict[str, str]] = []
    target_keys: set[tuple[str, str]] = set()
    for raw_target in raw_targets:
        if not isinstance(raw_target, dict) or set(raw_target) != {
            "format",
            "archetype",
        }:
            return None
        format_name = raw_target.get("format")
        archetype = raw_target.get("archetype")
        if (
            not isinstance(format_name, str)
            or format_name not in FORMAT_IDS
            or not isinstance(archetype, str)
            or not archetype
            or archetype != archetype.strip()
            or len(archetype) > 200
            or any(ord(character) < 32 for character in archetype)
        ):
            return None
        target = {"format": format_name, "archetype": archetype}
        target_key = _target_key(target)
        if target_key in target_keys:
            return None
        target_keys.add(target_key)
        targets.append(target)

    expected_order = sorted(
        targets,
        key=lambda target: (target["format"], target["archetype"].casefold()),
    )
    targets_total = checkpoint.get("targets_total")
    if (
        targets != expected_order
        or not isinstance(targets_total, int)
        or isinstance(targets_total, bool)
        or targets_total != len(targets)
        or checkpoint.get("target_signature") != _target_signature(targets)
    ):
        return None
    return targets


def _load_refresh_checkpoint(
    *,
    target_signature: str | None,
    now: datetime,
    max_age: timedelta = CHECKPOINT_TTL,
    absolute_max_age: timedelta | None = None,
) -> dict[str, Any] | None:
    checkpoint = load_baseline(SOURCE_ID, CHECKPOINT_LABEL) or {}
    if (
        checkpoint.get("state") != "in_progress"
        or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        or checkpoint.get("rank") != ANALYSIS_RANK
        or checkpoint.get("period") != ANALYSIS_PERIOD
        or _validated_checkpoint_targets(checkpoint) is None
        or (
            target_signature is not None
            and checkpoint.get("target_signature") != target_signature
        )
    ):
        return None
    try:
        started_at = datetime.fromisoformat(str(checkpoint.get("started_at") or ""))
    except ValueError:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    saved_at = _checkpoint_saved_at(checkpoint)
    if saved_at is None:
        return None
    inactivity_age = now - saved_at
    absolute_age = now - started_at
    if (
        inactivity_age < -timedelta(minutes=5)
        or inactivity_age > max_age
        or absolute_age < -timedelta(minutes=5)
        or (absolute_max_age is not None and absolute_age > absolute_max_age)
    ):
        return None
    return checkpoint


def _checkpoint_saved_at(checkpoint: dict[str, Any]) -> datetime | None:
    for field in ("saved_at", "started_at"):
        try:
            value = datetime.fromisoformat(str(checkpoint.get(field) or ""))
        except ValueError:
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value
    return None


def _refresh_window_id(started_at: str) -> str:
    digest = hashlib.sha256(f"{SOURCE_ID}\0{started_at}".encode()).hexdigest()[:24]
    return f"{SOURCE_ID}:{digest}"


def _pipeline_failure_reason_code(
    errors: list[dict[str, str]],
    provider_circuit_reason: str | None,
) -> str:
    signal = (
        f"{provider_circuit_reason or ''} "
        f"{json.dumps(errors, ensure_ascii=True, sort_keys=True)}"
    ).casefold()
    if any(marker in signal for marker in ("http 502", "http 503", "http 504")):
        return "upstream_5xx"
    if "429" in signal or "rate limit" in signal:
        return "rate_limited"
    if "timeout" in signal or "timed out" in signal:
        return "timeout"
    if any(marker in signal for marker in ("401", "unauthorized", "authentication")):
        return "authentication"
    if any(marker in signal for marker in ("403", "challenge", "blocked")):
        return "access_blocked"
    if any(marker in signal for marker in ("content validation", "parse", "schema")):
        return "contract"
    if any(marker in signal for marker in ("connect", "dns", "tls", "transport")):
        return "transport"
    return "unknown"


def _retry_is_pending(entry: dict[str, Any] | None, now: datetime) -> bool:
    if not entry or entry.get("state") not in {
        "source_no_data",
        "upstream_card_tallies_missing",
        "upstream_unavailable",
    }:
        return False
    if (
        entry.get("cache_version") != CARD_STATS_NEGATIVE_CACHE_VERSION
        or entry.get("min_mull_count") != CARD_STATS_MIN_MULL_COUNT
        or entry.get("min_drawn_count") != CARD_STATS_MIN_DRAWN_COUNT
    ):
        return False
    try:
        retry_after = datetime.fromisoformat(str(entry.get("retry_after") or ""))
    except ValueError:
        return False
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=UTC)
    return retry_after > now


def _component_was_refreshed_in_checkpoint(
    entry: dict[str, Any],
    *,
    kind: str,
    started_at: str,
) -> bool:
    timestamp_field = (
        "matchups_updated_at" if kind == "matchups" else "card_stats_updated_at"
    )
    rows_field = "class_matchups" if kind == "matchups" else "card_stats"
    state_field = "matchups_state" if kind == "matchups" else "card_stats_state"
    try:
        refreshed_at = datetime.fromisoformat(str(entry.get(timestamp_field) or ""))
        refresh_started_at = datetime.fromisoformat(started_at)
    except ValueError:
        return False
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=UTC)
    if refresh_started_at.tzinfo is None:
        refresh_started_at = refresh_started_at.replace(tzinfo=UTC)
    if refreshed_at < refresh_started_at:
        return False
    if entry.get(rows_field):
        return True
    accepted_empty_states = {"source_no_data"}
    if kind == "card_stats":
        accepted_empty_states.add("upstream_card_tallies_missing")
    return str(entry.get(state_field) or "") in accepted_empty_states


def _negative_cache_entry(
    *,
    format_name: str,
    archetype: str,
    state: str,
    reason: str,
    checked_at: datetime,
) -> dict[str, Any]:
    return {
        "format": format_name,
        "archetype": archetype,
        "kind": "card_stats",
        "state": state,
        "cache_version": CARD_STATS_NEGATIVE_CACHE_VERSION,
        "min_mull_count": CARD_STATS_MIN_MULL_COUNT,
        "min_drawn_count": CARD_STATS_MIN_DRAWN_COUNT,
        "checked_at": checked_at.isoformat(),
        "retry_after": (checked_at + CARD_STATS_UNAVAILABLE_RETRY).isoformat(),
        "reason": reason,
    }


def _firecrawl_headers() -> dict[str, str] | None:
    try:
        from .hsguru_auth import hsguru_firecrawl_headers

        return hsguru_firecrawl_headers()
    except (ImportError, RuntimeError):
        return None


def _analysis_html_is_valid(url: str, html: str) -> bool:
    if "/card-stats" in url:
        table, _ = _table_with_headers(
            html,
            {
                "card",
                "mulligan impact",
                "mulligan count",
                "drawn impact",
                "drawn count",
                "kept impact",
                "kept count",
            },
        )
        return table is not None and parse_card_stats_games(html) is not None
    table, _ = _table_with_headers(html, {"class", "winrate", "total games"})
    return table is not None


async def _fetch_html(url: str) -> tuple[str, dict[str, Any]]:
    source = Source(
        id=f"{SOURCE_ID}:page",
        url=url,
        site="hsguru",
        category="archetype_analysis",
        kind="pipeline",
    )
    result = await scrape_source_with_options(
        source,
        formats=["html"],
        only_main_content=True,
        headers=_firecrawl_headers(),
        max_age_ms=0,
        wait_ms=ANALYSIS_WAIT_MS,
        timeout_ms=ANALYSIS_TIMEOUT_MS,
        skip_providers={"scrapfly"},
        brightdata_accept_html=lambda html: _analysis_html_is_valid(url, html),
        brightdata_render=False,
        brightdata_anonymous_fallback=True,
        accept_result=lambda scraped: _analysis_html_is_valid(url, scraped.html),
    )
    return result.html, {
        "backend": result.backend,
        "request_credits": result.request_credits,
        "final_url": result.final_url,
    }


class HSGuruProviderCircuitOpen(RuntimeError):
    """The shared provider chain failed repeatedly during one refresh."""


async def _refresh_hsguru_archetype_analysis_unlocked(
    *,
    concurrency: int = 3,
    limit: int | None = None,
    archetypes: list[dict[str, str]] | None = None,
    checkpoint_recovery: bool = False,
    recovery_max_targets: int = CHECKPOINT_RECOVERY_MAX_TARGETS,
    recovery_provider_failure_budget: int = (
        CHECKPOINT_RECOVERY_PROVIDER_FAILURE_BUDGET
    ),
    fetch_html=_fetch_html,
) -> dict[str, Any]:
    started = _utc_now()
    started_at = started.isoformat()
    checkpoint_enabled = archetypes is None and limit is None
    checkpoint: dict[str, Any] | None = None
    if checkpoint_recovery:
        if checkpoint_enabled:
            checkpoint = _load_refresh_checkpoint(
                target_signature=None,
                now=started,
                max_age=CHECKPOINT_RECOVERY_TTL,
                absolute_max_age=CHECKPOINT_RECOVERY_ABSOLUTE_TTL,
            )
        targets = (
            _validated_checkpoint_targets(checkpoint) if checkpoint is not None else None
        )
        if targets is None:
            return {
                "ok": True,
                "published": False,
                "skipped": True,
                "reason": "checkpoint_not_available",
                "state": SourceState.OK,
                "source_id": SOURCE_ID,
                "targets": 0,
                "recovery": True,
            }
        signature = _target_signature(targets)
        checkpoint_saved_at = _checkpoint_saved_at(checkpoint)
        if (
            checkpoint_saved_at is not None
            and started - checkpoint_saved_at < CHECKPOINT_RECOVERY_COOLDOWN
        ):
            return {
                "ok": True,
                "published": False,
                "skipped": True,
                "reason": "checkpoint_recovery_cooldown",
                "state": SourceState.OK,
                "source_id": SOURCE_ID,
                "targets": len(targets),
                "targets_completed": len(checkpoint.get("completed") or []),
                "next_retry_at": (
                    checkpoint_saved_at + CHECKPOINT_RECOVERY_COOLDOWN
                ).isoformat(),
                "recovery": True,
            }
    else:
        targets = list(archetypes if archetypes is not None else _active_archetypes())
        targets = list({_target_key(target): target for target in targets}.values())
        targets.sort(key=lambda row: (row["format"], row["archetype"].casefold()))
        if limit is not None:
            targets = targets[: max(0, limit)]
        if not targets:
            raise RuntimeError(
                "No HSGuru archetypes in the Legend/past-week matrix slices"
            )
        signature = _target_signature(targets)
        if checkpoint_enabled:
            checkpoint = _load_refresh_checkpoint(
                target_signature=signature,
                now=started,
                max_age=CHECKPOINT_TTL,
            )

    target_descriptors = [
        {"format": target["format"], "archetype": target["archetype"]}
        for target in targets
    ]

    previous = _previous_analysis()
    negative_cache = _previous_negative_cache()
    checkpoint_rows = {
        _target_key(row): dict(row)
        for row in (checkpoint or {}).get("rows") or []
        if isinstance(row, dict)
    }
    target_keys = {_target_key(target) for target in targets}
    resumed_keys = {
        _target_key(row)
        for row in (checkpoint or {}).get("completed") or []
        if isinstance(row, dict)
    } & target_keys & checkpoint_rows.keys()
    if checkpoint:
        started_at = str(checkpoint.get("started_at") or started_at)
        cached_negative = {
            (
                str(entry.get("format") or ""),
                str(entry.get("archetype") or "").casefold(),
                str(entry.get("kind") or ""),
            ): dict(entry)
            for entry in checkpoint.get("negative_cache") or []
            if isinstance(entry, dict)
        }
        negative_cache = cached_negative

    refresh_window_id = _refresh_window_id(started_at)

    resumed_keys = {
        key
        for key in resumed_keys
        if (
            (gap := negative_cache.get((*key, "card_stats"))) is None
            or _retry_is_pending(gap, started)
        )
    }

    working_previous = {**previous, **checkpoint_rows}
    worker_count = max(1, min(concurrency, 10, len(targets)))
    semaphore = asyncio.Semaphore(worker_count)
    acquisitions: list[dict[str, Any]] = [
        dict(item)
        for item in (checkpoint or {}).get("acquisitions") or []
        if isinstance(item, dict)
    ]
    errors: list[dict[str, str]] = []
    unavailable: list[dict[str, str]] = [
        dict(item)
        for item in (checkpoint or {}).get("unavailable") or []
        if isinstance(item, dict)
    ]
    card_stats_requests_skipped = int(
        (checkpoint or {}).get("card_stats_requests_skipped") or 0
    )
    provider_failure_streaks = {"matchups": 0, "card_stats": 0}
    provider_failures_total = int(
        (checkpoint or {}).get("provider_failures_total") or 0
    )
    provider_failures_before_run = provider_failures_total
    provider_failure_budget = (
        max(1, recovery_provider_failure_budget) if checkpoint_recovery else None
    )
    provider_circuit_reason: str | None = None
    provider_circuit_kind: str | None = None
    provider_circuit = asyncio.Event()
    provider_failure_lock = asyncio.Lock()
    # Recovery is deliberately serialized at the provider boundary. Without
    # this guard, concurrent requests can all fail after the configured budget
    # has been reached, which defeats the cost/circuit protection. The regular
    # daily refresh keeps its configured concurrency.
    recovery_provider_request_lock = asyncio.Lock()

    async def fetch_and_parse(
        url: str,
        parser,
        *,
        format_name: str,
        archetype: str,
        kind: str,
    ) -> tuple[list[dict[str, Any]], str, str]:
        nonlocal provider_circuit_kind
        nonlocal provider_circuit_reason
        nonlocal provider_failures_total
        async def fetch_guarded() -> tuple[str, dict[str, Any]]:
            nonlocal provider_circuit_kind
            nonlocal provider_circuit_reason
            nonlocal provider_failures_total
            async with semaphore:
                if provider_circuit.is_set():
                    raise HSGuruProviderCircuitOpen(
                        provider_circuit_reason or "HSGuru provider circuit is open"
                    )
                try:
                    fetched_html, fetched_acquisition = await fetch_html(url)
                except Exception as exc:
                    async with provider_failure_lock:
                        provider_failures_total += 1
                        provider_failure_streaks[kind] += 1
                        if provider_failure_budget is not None and (
                            provider_failures_total - provider_failures_before_run
                            >= provider_failure_budget
                        ):
                            provider_circuit_kind = kind
                            provider_circuit_reason = (
                                "checkpoint recovery provider failure budget exhausted "
                                f"({provider_failure_budget})"
                            )
                            provider_circuit.set()
                        elif (
                            provider_failure_streaks[kind]
                            >= PROVIDER_CIRCUIT_FAILURE_THRESHOLD
                        ):
                            provider_circuit_kind = kind
                            provider_circuit_reason = (
                                f"{type(exc).__name__}: {str(exc)[:400]}"
                            )
                            provider_circuit.set()
                    raise
                else:
                    async with provider_failure_lock:
                        provider_failure_streaks[kind] = 0
                return fetched_html, fetched_acquisition

        if checkpoint_recovery:
            async with recovery_provider_request_lock:
                html, acquisition = await fetch_guarded()
        else:
            html, acquisition = await fetch_guarded()

        raw_rows = parser(html)
        fetched_at = _now()
        rows = raw_rows
        semantic_state = "complete"
        sample_games: int | None = None
        if kind == "card_stats":
            sample_games = parse_card_stats_games(html)
            rows = _qualified_card_stats(raw_rows)
            if raw_rows and not rows:
                semantic_state = "sparse_valid"
            elif not raw_rows:
                semantic_state = (
                    "source_no_data"
                    if sample_games == 0
                    else "upstream_card_tallies_missing"
                )
        elif not rows:
            semantic_state = "source_no_data"
        acquisitions.append(
            {
                **acquisition,
                "kind": kind,
                "format": format_name,
                "archetype": archetype,
                "rows": len(rows),
                "raw_rows": len(raw_rows),
                "sample_games": sample_games,
                "semantic_state": semantic_state,
            }
        )
        return rows, fetched_at, semantic_state

    async def enrich(target: dict[str, str]) -> tuple[dict[str, Any], bool]:
        nonlocal card_stats_requests_skipped
        format_name = target["format"]
        archetype = target["archetype"]
        urls = analysis_urls(archetype, format_name)
        cached = working_previous.get((format_name, archetype.casefold()), {})
        card_stats_cache_key = (
            format_name,
            archetype.casefold(),
            "card_stats",
        )
        cached_gap = negative_cache.get(card_stats_cache_key)
        entry = {
            "format": format_name,
            "archetype": archetype,
            "rank": ANALYSIS_RANK,
            "period": ANALYSIS_PERIOD,
            "source_urls": urls,
            "class_matchups": list(cached.get("class_matchups") or []),
            "card_stats": list(cached.get("card_stats") or []),
            "matchups_updated_at": cached.get("matchups_updated_at"),
            "card_stats_updated_at": cached.get("card_stats_updated_at"),
            "matchups_checked_at": cached.get("matchups_checked_at"),
            "card_stats_checked_at": cached.get("card_stats_checked_at"),
            "matchups_state": cached.get("matchups_state") or "cached",
            "card_stats_state": cached.get("card_stats_state") or "cached",
        }
        tasks = {
            "matchups": asyncio.create_task(
                fetch_and_parse(
                    urls["matchups"],
                    parse_class_matchups_html,
                    format_name=format_name,
                    archetype=archetype,
                    kind="matchups",
                )
            ),
        }
        if _retry_is_pending(cached_gap, _utc_now()):
            card_stats_requests_skipped += 1
            entry["card_stats_state"] = str(cached_gap.get("state") or "cached")
            entry["card_stats_checked_at"] = cached_gap.get("checked_at")
        else:
            tasks["card_stats"] = asyncio.create_task(
                fetch_and_parse(
                    urls["cards"],
                    parse_card_stats_html,
                    format_name=format_name,
                    archetype=archetype,
                    kind="card_stats",
                )
            )
        reusable = True
        for kind, task in tasks.items():
            try:
                rows, fetched_at, semantic_state = await task
                unavailable[:] = [
                    item
                    for item in unavailable
                    if not (
                        item.get("format") == format_name
                        and str(item.get("archetype") or "").casefold()
                        == archetype.casefold()
                        and item.get("kind") == kind
                    )
                ]
                if kind == "matchups":
                    entry["class_matchups"] = rows
                    entry["matchups_updated_at"] = fetched_at
                    entry["matchups_checked_at"] = fetched_at
                    entry["matchups_state"] = semantic_state
                else:
                    entry["card_stats_checked_at"] = fetched_at
                    entry["card_stats_state"] = semantic_state
                    if semantic_state in {"complete", "sparse_valid"}:
                        entry["card_stats"] = rows
                        entry["card_stats_updated_at"] = fetched_at
                        negative_cache.pop(card_stats_cache_key, None)
                    else:
                        reason = (
                            "HSGuru sample currently has no games"
                            if semantic_state == "source_no_data"
                            else "HSGuru aggregate has games but no card tallies"
                        )
                        if semantic_state == "source_no_data":
                            entry["card_stats"] = []
                            entry["card_stats_updated_at"] = fetched_at
                        negative_cache[card_stats_cache_key] = _negative_cache_entry(
                            format_name=format_name,
                            archetype=archetype,
                            state=semantic_state,
                            reason=reason,
                            checked_at=datetime.fromisoformat(fetched_at),
                        )
                        unavailable.append(
                            {
                                "format": format_name,
                                "archetype": archetype,
                                "kind": kind,
                                "state": semantic_state,
                                "reason": reason,
                            }
                        )
                if kind == "matchups" and semantic_state == "source_no_data":
                    unavailable.append(
                        {
                            "format": format_name,
                            "archetype": archetype,
                            "kind": kind,
                            "state": semantic_state,
                            "reason": "HSGuru sample currently has no matchup rows",
                        }
                    )
            except HSGuruProviderCircuitOpen:
                reusable = False
                entry[f"{kind}_state"] = "provider_circuit_open"
            except Exception as exc:  # noqa: BLE001 - isolate each remote page
                if not _component_was_refreshed_in_checkpoint(
                    entry,
                    kind=kind,
                    started_at=started_at,
                ):
                    reusable = False
                    entry[f"{kind}_state"] = "transport_error"
                    errors.append(
                        {
                            "format": format_name,
                            "archetype": archetype,
                            "kind": kind,
                            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                        }
                    )
        matchups_state = str(entry.get("matchups_state") or "missing")
        card_stats_state = str(entry.get("card_stats_state") or "missing")
        entry["state"] = (
            "ok"
            if matchups_state == "complete"
            and card_stats_state in {"complete", "sparse_valid"}
            else "partial"
            if (
                entry["class_matchups"]
                or entry["card_stats"]
                or matchups_state == "source_no_data"
                or card_stats_state
                in {"source_no_data", "upstream_card_tallies_missing"}
            )
            else "error"
        )
        component_updates = [
            str(entry.get(field) or "")
            for field in ("matchups_updated_at", "card_stats_updated_at")
            if entry.get(field)
        ]
        component_checks = [
            str(entry.get(field) or "")
            for field in ("matchups_checked_at", "card_stats_checked_at")
            if entry.get(field)
        ]
        entry["updated_at"] = min(component_updates) if component_updates else None
        entry["checked_at"] = (
            min(component_checks) if len(component_checks) == 2 else None
        )
        entry["components"] = {
            "matchups": {
                "state": matchups_state,
                "checked_at": entry.get("matchups_checked_at"),
                "updated_at": entry.get("matchups_updated_at"),
            },
            "card_stats": {
                "state": card_stats_state,
                "checked_at": entry.get("card_stats_checked_at"),
                "updated_at": entry.get("card_stats_updated_at"),
            },
        }
        return entry, reusable

    refreshed_by_key = {
        key: checkpoint_rows[key]
        for key in resumed_keys
    }
    completed_keys = set(resumed_keys)
    pending_targets = [
        target for target in targets if _target_key(target) not in resumed_keys
    ]
    recovery_targets_deferred = 0
    if checkpoint_recovery:
        recovery_target_budget = max(1, recovery_max_targets)
        recovery_targets_deferred = max(
            0,
            len(pending_targets) - recovery_target_budget,
        )
        pending_targets = pending_targets[:recovery_target_budget]
    completed_this_run = 0

    def negative_cache_rows() -> list[dict[str, Any]]:
        return sorted(
            negative_cache.values(),
            key=lambda row: (
                str(row.get("format")),
                str(row.get("archetype")).casefold(),
                str(row.get("kind")),
            ),
        )

    def save_progress_checkpoint() -> None:
        if not checkpoint_enabled:
            return
        save_baseline(
            SOURCE_ID,
            CHECKPOINT_LABEL,
            {
                "state": "in_progress",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "rank": ANALYSIS_RANK,
                "period": ANALYSIS_PERIOD,
                "started_at": started_at,
                "saved_at": _now(),
                "target_signature": signature,
                "targets_total": len(targets),
                "targets": target_descriptors,
                "completed": [
                    {"format": format_name, "archetype": archetype}
                    for format_name, archetype in sorted(completed_keys)
                ],
                "rows": sorted(
                    refreshed_by_key.values(),
                    key=lambda row: _target_key(row),
                ),
                "negative_cache": negative_cache_rows(),
                "unavailable": unavailable,
                "acquisitions": acquisitions,
                "provider_failures_total": provider_failures_total,
                "card_stats_requests_skipped": card_stats_requests_skipped,
            },
        )

    save_progress_checkpoint()
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    for target in pending_targets:
        queue.put_nowait(target)

    async def worker() -> None:
        nonlocal completed_this_run
        while not provider_circuit.is_set():
            try:
                target = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            row, reusable = await enrich(target)
            key = _target_key(row)
            refreshed_by_key[key] = row
            if reusable:
                completed_keys.add(key)
            completed_this_run += 1
            if completed_this_run % CHECKPOINT_INTERVAL_TARGETS == 0:
                save_progress_checkpoint()
            queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        save_progress_checkpoint()
        raise

    refresh_complete = (
        not errors
        and not provider_circuit.is_set()
        and completed_keys == target_keys
    )
    recovery_batch_complete = (
        checkpoint_recovery
        and not errors
        and not provider_circuit.is_set()
        and completed_this_run == len(pending_targets)
        and completed_keys != target_keys
    )
    if not refresh_complete:
        save_progress_checkpoint()

    refreshed = list(refreshed_by_key.values())
    refreshed_keys = set(refreshed_by_key)
    retain_unselected = archetypes is not None or limit is not None
    retained = (
        [row for key, row in previous.items() if key not in refreshed_keys]
        if retain_unselected
        else []
    )
    rows = [*refreshed, *retained]
    rows.sort(
        key=lambda row: (
            str(row.get("format")),
            str(row.get("archetype")).casefold(),
        )
    )
    cached_rows = sorted(
        previous.values(),
        key=lambda row: (
            str(row.get("format")),
            str(row.get("archetype")).casefold(),
        ),
    )

    def coverage_for(selected_rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            format_name: {
                "archetypes": sum(
                    1 for row in selected_rows if row.get("format") == format_name
                ),
                "with_matchups": sum(
                    1
                    for row in selected_rows
                    if row.get("format") == format_name
                    and row.get("class_matchups")
                ),
                "with_card_stats": sum(
                    1
                    for row in selected_rows
                    if row.get("format") == format_name and row.get("card_stats")
                ),
                "complete": sum(
                    1
                    for row in selected_rows
                    if row.get("format") == format_name and row.get("state") == "ok"
                ),
            }
            for format_name in FORMAT_IDS
        }

    coverage = coverage_for(rows if refresh_complete else cached_rows)
    firecrawl_credits = sum(
        int(item.get("request_credits") or 0)
        for item in acquisitions
        if item.get("backend") == "firecrawl"
    )
    scrape_do_credits = sum(
        int(item.get("request_credits") or 0)
        for item in acquisitions
        if str(item.get("backend") or "").startswith("scrape_do")
    )
    backend = "+".join(
        sorted({str(item.get("backend")) for item in acquisitions})
    ) or "cache"
    if not refresh_complete:
        failure_state = (
            SourceState.PARTIAL if cached_rows else SourceState.FETCH_ERROR
        )
        failure_reason_code = _pipeline_failure_reason_code(
            errors,
            provider_circuit_reason,
        )
        status = {
            "source_id": SOURCE_ID,
            "site": "hsguru",
            "category": "archetype_analysis",
            "state": failure_state,
            "failure_reason_code": failure_reason_code,
            "refresh_window_id": refresh_window_id,
            "fetched_at": started_at,
            "http_status": None,
            "backend": backend,
            "rows_total": len(cached_rows),
            "coverage": coverage,
            "published": False,
            "serving_cached_dataset": bool(cached_rows),
            "last_refresh_state": (
                SourceState.PARTIAL
                if recovery_batch_complete
                else SourceState.FETCH_ERROR
            ),
            "last_refresh_at": _now(),
            "errors": errors[:50],
            "errors_total": len(errors),
            "unavailable": unavailable[:500],
            "unavailable_total": len(unavailable),
            "provider_failures_total": provider_failures_total,
            "provider_failures_this_run": (
                provider_failures_total - provider_failures_before_run
            ),
            "provider_circuit_open": provider_circuit.is_set(),
            "provider_circuit_kind": provider_circuit_kind,
            "provider_circuit_reason": provider_circuit_reason,
            "targets_total": len(targets),
            "targets_completed": len(completed_keys),
            "targets_remaining": len(target_keys - completed_keys),
            "resumed_targets": len(resumed_keys),
            "recovery": checkpoint_recovery,
            "recovery_batch_complete": recovery_batch_complete,
            "recovery_targets_deferred": recovery_targets_deferred,
            "card_stats_requests_skipped": card_stats_requests_skipped,
            "firecrawl_credits_used": firecrawl_credits,
            "scrape_do_credits_used": scrape_do_credits,
        }
        save_status(SOURCE_ID, status)
        return {
            "ok": False,
            "published": False,
            "retryable": True,
            "state": failure_state,
            "failure_reason_code": failure_reason_code,
            "refresh_window_id": refresh_window_id,
            "serving_cached_dataset": bool(cached_rows),
            "source_id": SOURCE_ID,
            "targets": len(targets),
            "targets_completed": len(completed_keys),
            "targets_remaining": len(target_keys - completed_keys),
            "resumed_targets": len(resumed_keys),
            "archetypes": len(cached_rows),
            "coverage": coverage,
            "errors": errors,
            "errors_total": len(errors),
            "unavailable": unavailable,
            "unavailable_total": len(unavailable),
            "provider_failures_total": provider_failures_total,
            "provider_failures_this_run": (
                provider_failures_total - provider_failures_before_run
            ),
            "provider_circuit_open": provider_circuit.is_set(),
            "provider_circuit_kind": provider_circuit_kind,
            "provider_circuit_reason": provider_circuit_reason,
            "recovery": checkpoint_recovery,
            "recovery_batch_complete": recovery_batch_complete,
            "recovery_targets_deferred": recovery_targets_deferred,
            "negative_cache_entries": len(negative_cache_rows()),
            "card_stats_requests_skipped": card_stats_requests_skipped,
            "firecrawl_credits_used": firecrawl_credits,
            "scrape_do_credits_used": scrape_do_credits,
        }

    negative_rows = negative_cache_rows()
    dataset_state = (
        "ok" if all(row.get("state") == "ok" for row in rows) else "partial"
    )
    structured = {
        "type": SOURCE_ID,
        "schema_version": SCHEMA_VERSION,
        "criteria": {
            "rank": ANALYSIS_RANK,
            "period": ANALYSIS_PERIOD,
            "card_stats_min_mull_count": CARD_STATS_MIN_MULL_COUNT,
            "card_stats_min_drawn_count": CARD_STATS_MIN_DRAWN_COUNT,
            "formats": list(FORMAT_IDS),
            "requires_decks": False,
            "target_source": "hsguru_meta_matrix:legend:past_week",
            "upstream_card_stats_min_mull_count": 0,
            "upstream_card_stats_min_drawn_count": 0,
        },
        "coverage": coverage,
        "expected_targets": target_descriptors,
        "expected_targets_total": len(target_descriptors),
        "negative_cache": negative_rows,
        "archetypes": rows,
    }
    payload = {
        "source_id": SOURCE_ID,
        "state": dataset_state,
        "fetched_at": started_at,
        "http_status": 200,
        "final_url": "https://www.hsguru.com/archetype",
        "content_length": len(json.dumps(structured, ensure_ascii=False).encode("utf-8")),
        "backend": backend,
        "data": {
            "structured": structured,
            "acquisition": {
                "pages": acquisitions,
                "firecrawl_credits_used": firecrawl_credits,
                "scrape_do_credits_used": scrape_do_credits,
                "card_stats_requests_skipped": card_stats_requests_skipped,
            },
        },
    }
    gate = validate_candidate_for_publish(
        SOURCE_BY_ID[SOURCE_ID],
        payload["data"],
        backend=backend,
    )
    if not gate.ok:
        failure = {
            "ok": False,
            "published": False,
            "retryable": True,
            "state": SourceState.QUALITY_ERROR,
            "failure_reason_code": "contract",
            "refresh_window_id": refresh_window_id,
            "serving_cached_dataset": bool(cached_rows),
            "source_id": SOURCE_ID,
            "targets": len(targets),
            "targets_completed": len(completed_keys),
            "targets_remaining": 0,
            "resumed_targets": len(resumed_keys),
            "archetypes": len(cached_rows),
            "coverage": coverage_for(cached_rows),
            "errors": [{"kind": "publication_gate", "error": gate.reason}],
            "errors_total": 1,
            "recovery": checkpoint_recovery,
            "negative_cache_entries": len(negative_rows),
            "card_stats_requests_skipped": card_stats_requests_skipped,
            "firecrawl_credits_used": firecrawl_credits,
            "scrape_do_credits_used": scrape_do_credits,
        }
        save_status(
            SOURCE_ID,
            {
                **failure,
                "site": "hsguru",
                "category": "archetype_analysis",
                "fetched_at": started_at,
                "backend": backend,
                "rows_total": len(cached_rows),
                "last_refresh_state": SourceState.QUALITY_ERROR,
                "last_refresh_at": _now(),
            },
        )
        return failure
    save_dataset(SOURCE_ID, payload)
    if checkpoint_enabled:
        save_baseline(
            SOURCE_ID,
            CHECKPOINT_LABEL,
            {
                "state": "complete",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "rank": ANALYSIS_RANK,
                "period": ANALYSIS_PERIOD,
                "completed_at": _now(),
                "target_signature": signature,
                "targets_total": len(targets),
                "targets": target_descriptors,
            },
        )
    status = {
        "source_id": SOURCE_ID,
        "site": "hsguru",
        "category": "archetype_analysis",
        "state": payload["state"],
        "refresh_window_id": refresh_window_id,
        "fetched_at": started_at,
        "http_status": 200,
        "backend": payload["backend"],
        "published": True,
        "serving_cached_dataset": False,
        "rows_total": len(rows),
        "coverage": coverage,
        "errors": errors[:50],
        "errors_total": len(errors),
        "unavailable": unavailable[:500],
        "unavailable_total": len(unavailable),
        "provider_failures_total": provider_failures_total,
        "provider_failures_this_run": (
            provider_failures_total - provider_failures_before_run
        ),
        "provider_circuit_open": False,
        "targets_total": len(targets),
        "targets_completed": len(completed_keys),
        "targets_remaining": 0,
        "resumed_targets": len(resumed_keys),
        "recovery": checkpoint_recovery,
        "negative_cache_entries": len(negative_rows),
        "card_stats_requests_skipped": card_stats_requests_skipped,
        "firecrawl_credits_used": firecrawl_credits,
        "scrape_do_credits_used": scrape_do_credits,
    }
    save_status(SOURCE_ID, status)
    return {
        "ok": True,
        "published": True,
        "serving_cached_dataset": False,
        "state": payload["state"],
        "refresh_window_id": refresh_window_id,
        "source_id": SOURCE_ID,
        "targets": len(targets),
        "targets_completed": len(completed_keys),
        "targets_remaining": 0,
        "resumed_targets": len(resumed_keys),
        "archetypes": len(rows),
        "coverage": coverage,
        "errors": errors,
        "errors_total": len(errors),
        "unavailable": unavailable,
        "unavailable_total": len(unavailable),
        "provider_failures_total": provider_failures_total,
        "provider_failures_this_run": (
            provider_failures_total - provider_failures_before_run
        ),
        "provider_circuit_open": False,
        "recovery": checkpoint_recovery,
        "negative_cache_entries": len(negative_rows),
        "card_stats_requests_skipped": card_stats_requests_skipped,
        "firecrawl_credits_used": firecrawl_credits,
        "scrape_do_credits_used": scrape_do_credits,
    }


async def refresh_hsguru_archetype_analysis(
    *,
    concurrency: int = 3,
    limit: int | None = None,
    archetypes: list[dict[str, str]] | None = None,
    checkpoint_recovery: bool = False,
    recovery_max_targets: int = CHECKPOINT_RECOVERY_MAX_TARGETS,
    recovery_provider_failure_budget: int = (
        CHECKPOINT_RECOVERY_PROVIDER_FAILURE_BUDGET
    ),
    fetch_html=_fetch_html,
) -> dict[str, Any]:
    locks = ResourceLockSet(
        [SOURCE_ID],
        metadata={"operation": "refresh_hsguru_archetype_analysis"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE_ID,
            "archetypes": len(_previous_analysis()),
            **exc.as_outcome(),
        }
    try:
        return await _refresh_hsguru_archetype_analysis_unlocked(
            concurrency=concurrency,
            limit=limit,
            archetypes=archetypes,
            checkpoint_recovery=checkpoint_recovery,
            recovery_max_targets=recovery_max_targets,
            recovery_provider_failure_budget=recovery_provider_failure_budget,
            fetch_html=fetch_html,
        )
    finally:
        locks.release()
