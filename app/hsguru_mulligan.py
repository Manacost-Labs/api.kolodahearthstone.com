"""HSGuru mulligan and class-matchup statistics for archetypes and decks.

HSGuru exposes the mulligan guide on ``/card-stats`` (per card: mulligan,
drawn, not drawn and kept impact with their counts) and class matchups on
``/archetype/<name>`` and ``/deck/<id>``. Both accept the same slices as the
meta table: format, rank, period (including the ``*_ban`` Bans window) and
``player_has_coin``; card stats also split by ``opponent_class``.

The universe of pages is far larger than one refresh can read, so every page
is a *target* with incremental state (see :mod:`app.hsguru_mulligan_store`):

* targets come from data we already have: archetype games per slice from the
  ``hsguru_meta_matrix`` dataset and deck games from the HSGuru deck catalogs,
  so a slice is only read when it holds enough games to be meaningful;
* a target is due when it was never read, its period TTL expired, or a
  cumulative window (patch, expansion, Bans) gained enough games;
* each run reads due targets in priority order up to a page budget and a
  deadline, and the rest stay due for the next run.

Pages are read through Scrape.do first, with many pages in parallel, so the
shared server is not loaded; the local FlareSolverr (direct for HSGuru in the
proxy policy) is the fallback. Optional per-run and per-UTC-day credit caps
and the shared HSGuru Meta-Lab budget bound the spending.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from bs4 import Tag

from . import hsguru_mulligan_store as store
from .db import get_db_connection, init_db
from .hsguru_archetype_analysis import (
    CLASS_KEYS,
    _card_identity,
    _count,
    _header,
    _number,
    _table_with_headers,
    parse_card_stats_games,
)
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import Source
from .storage import load_dataset, save_dataset, save_status

logger = logging.getLogger(__name__)

SOURCE_ID = "hsguru_mulligan"
PAGE_SOURCE_ID = f"{SOURCE_ID}:page"
MATRIX_SOURCE_ID = "hsguru_meta_matrix"
HSGURU = "https://www.hsguru.com"
FORMAT_IDS = {"standard": 2, "wild": 1}
# Ranks offered by both the meta matrix (which gives the games) and card-stats.
RANKS = ("all", "diamond_4to1", "diamond_to_legend", "legend", "top_5k", "top_legend")
COINS = ("any_player", "on_coin", "going_first")
COIN_QUERY = {"on_coin": "yes", "going_first": "no"}
ANY_OPPONENT = "any"
OPPONENT_CLASSES = (
    "DEATHKNIGHT",
    "DEMONHUNTER",
    "DRUID",
    "HUNTER",
    "MAGE",
    "PALADIN",
    "PRIEST",
    "ROGUE",
    "SHAMAN",
    "WARLOCK",
    "WARRIOR",
)
# 3/6-hour windows rarely reach a usable per-card sample and churn constantly.
SKIPPED_PERIODS = frozenset({"past_3_hours", "past_6_hours"})
# The default plan needs about 1,200 pages a day by TTL (see
# ``estimate_plan``): about 1,300 Scrape.do credits when the plain 1-credit
# profile serves the page. Shorter TTLs cost credits proportionally.
ROLLING_TTL_HOURS = {
    "past_day": 24,
    "past_3_days": 72,
    "past_week": 72,
    "past_2_weeks": 168,
    "past_30_days": 336,
}
# Coin split and class matchups cover only these rolling windows plus every
# cumulative window; the other windows keep the plain mulligan.
CORE_ROLLING_PERIODS = frozenset({"past_week"})
# Patch, expansion and Bans windows only grow: re-read on growth or after TTL.
CUMULATIVE_TTL_HOURS = 168
# A young window grows fast: at 50 % and two days a page of a 5-day-old
# window is re-read every ~2.5 days instead of daily.
CUMULATIVE_GROWTH = 0.5
CUMULATIVE_MIN_AGE_HOURS = 48


# Read order inside a tier: the expansion window, the Bans window, then the
# rolling windows people open most; older patch windows come late.
def period_priority(period: str) -> int:
    if period.endswith("_ban"):
        return 1
    if period.startswith("patch_"):
        return 4
    return {
        "past_week": 2,
        "past_day": 3,
        "past_3_days": 5,
        "past_2_weeks": 6,
        "past_30_days": 7,
    }.get(period, 0)


# Lower-priority targets refresh half as often.
TIER_TTL_FACTOR = {1: 1, 2: 1, 3: 2}
CARD_TABLE_HEADERS = {
    "card",
    "mulligan impact",
    "mulligan count",
    "drawn impact",
    "drawn count",
    "kept impact",
    "kept count",
}
MATCHUP_TABLE_HEADERS = {"class", "winrate", "total games"}
PAYWALL_MARKER = "you do not have access to these filters"
DECK_CATALOG_SUFFIXES = ("legend", "all", "fallback")
DECK_URL_RE = re.compile(r"/deck/(\d+)")
FREE_CIRCUIT_THRESHOLD = 4
# Consecutive Scrape.do failures (after its own retries and profile ladder).
PAID_CIRCUIT_THRESHOLD = 10
RUN_CIRCUIT_THRESHOLD = 8
MAX_CONCURRENCY = 50
SCRAPE_DO_RESERVE_CREDITS = 25
FLARESOLVERR_WAIT_MS = 0
SCRAPE_DO_TIMEOUT_MS = 60_000
# HSGuru serves these pages as LiveView dead renders, so a plain request has
# the full tables (2026-10-25: 8 s and 1 credit vs 54 s and 5 credits with
# render, same 32 cards). A rejected page climbs to the browser profiles,
# which also carry the session cookie for premium filters.
SCRAPE_DO_PROFILES = ((False, False), (True, False), (False, True), (True, True))


def _env_int(
    name: str, default: int, *, minimum: int = 0, maximum: int = 1_000_000
) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    archetype_min_games: int = 2000
    coin_min_games: int = 3000
    opponent_min_games: int = 10000
    deck_min_games: int = 100
    deck_coin_min_games: int = 600
    # One run can load the whole plan: ~15,000 plain Scrape.do pages an hour.
    max_pages: int = 10_000
    deadline_seconds: int = 150 * 60
    # Scrape.do allows 50 concurrent requests per account; the meta matrix (5)
    # and the deck catalogs share them.
    concurrency: int = 40
    # 0 means no cap. Positive values are hard safety limits.
    scrape_do_run_credits: int = 0
    scrape_do_daily_credits: int = 0
    snapshot_days: int = 30
    transport: str = "scrape_do"

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        values: dict[str, Any] = {
            name: _env_int(f"HS_HSGURU_MULLIGAN_{name.upper()}", getattr(base, name))
            for name in cls.__dataclass_fields__
            if name != "transport"
        }
        values["concurrency"] = max(1, min(values["concurrency"], MAX_CONCURRENCY))
        values["max_pages"] = max(1, values["max_pages"])
        values["snapshot_days"] = max(1, values["snapshot_days"])
        transport = os.environ.get("HS_HSGURU_MULLIGAN_TRANSPORT", base.transport)
        values["transport"] = (
            transport.strip() if transport.strip() in TRANSPORTS else base.transport
        )
        return cls(**values)


# --- parsing --------------------------------------------------------------------


class PageRejected(RuntimeError):
    """The page is not a usable HSGuru statistics page."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _card_name(cell: Tag, fallback: str) -> str:
    """The visible card name: HSGuru prefixes it with screen-reader-only text."""

    node = cell.select_one(".card-name")
    if node is None:
        return fallback
    parts = [
        str(text)
        for text in node.find_all(string=True)
        if not any(
            "tw-sr-only" in (parent.get("class") or [])
            for parent in text.parents
            if parent is not node and isinstance(parent, Tag)
        )
    ]
    name = re.sub(r"\s+", " ", "".join(parts)).strip()
    return name or fallback


def parse_mulligan_html(html: str) -> dict[str, Any]:
    """Parse a ``/card-stats`` page: the sample size and every card row."""

    if PAYWALL_MARKER in html.casefold():
        raise PageRejected("paywalled")
    table, _ = _table_with_headers(html, CARD_TABLE_HEADERS)
    games = parse_card_stats_games(html)
    if table is None or games is None:
        raise PageRejected("content_validation")
    header_row = table.find("thead") or table.find("tr")
    headers = [
        _header(cell.get_text(" ", strip=True))
        for cell in header_row.find_all(["th", "td"])
    ]
    index = {name: position for position, name in enumerate(headers)}
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr") or table.find_all("tr")[1:]:
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(index[name] for name in CARD_TABLE_HEADERS):
            continue
        card_id, dbf_id, card_name = _card_identity(cells[index["card"]])
        card_name = _card_name(cells[index["card"]], card_name)
        if not card_name:
            continue

        def cell(name: str, cells: list[Tag] = cells) -> str | None:
            position = index.get(name)
            if position is None or position >= len(cells):
                return None
            return cells[position].get_text(" ", strip=True)

        mulligan_count = _count(cell("mulligan count"))
        if mulligan_count is None:
            continue
        rows.append(
            {
                "card_id": card_id,
                "dbf_id": dbf_id,
                "card_name": card_name,
                "mulligan_impact": _number(cell("mulligan impact")),
                "mulligan_count": mulligan_count,
                "drawn_impact": _number(cell("drawn impact")),
                "drawn_count": _count(cell("drawn count")),
                "not_drawn_impact": _number(cell("not drawn impact")),
                "not_drawn_count": _count(cell("not drawn count")),
                "kept_impact": _number(cell("kept impact")),
                "kept_count": _count(cell("kept count")),
            }
        )
    return {"games": games, "winrate": None, "rows": rows}


def parse_class_matchups_page(html: str) -> dict[str, Any]:
    """Parse an archetype or deck page: per-class winrate and the Total row."""

    if PAYWALL_MARKER in html.casefold():
        raise PageRejected("paywalled")
    table, index = _table_with_headers(html, MATCHUP_TABLE_HEADERS)
    if table is None:
        raise PageRejected("content_validation")
    rows: list[dict[str, Any]] = []
    total_games: int | None = None
    total_winrate: float | None = None
    for tr in table.select("tbody tr") or table.find_all("tr")[1:]:
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(index.values()):
            continue
        label = cells[index["class"]].get_text(" ", strip=True)
        games_text = cells[index["total games"]].get_text(" ", strip=True)
        winrate = _number(cells[index["winrate"]].get_text(" ", strip=True))
        games = _count(games_text)
        if _header(label) == "total":
            total_games, total_winrate = games, winrate
            continue
        class_key = CLASS_KEYS.get(_header(label))
        if class_key is None or games is None:
            continue
        if winrate is not None and not 0 <= winrate <= 100:
            continue
        share = re.search(r"\(([-+]?\d+(?:[.,]\d+)?)\s*%\)", games_text)
        rows.append(
            {
                "class_key": class_key,
                "winrate": winrate if games else None,
                "games": games,
                "share_pct": _number(share.group(1)) if share else None,
            }
        )
    if total_games is None:
        total_games = sum(int(row["games"]) for row in rows)
    return {
        "games": total_games,
        "winrate": total_winrate if total_games else None,
        "rows": sorted(rows, key=lambda row: row["class_key"]),
    }


def parse_page(kind: str, html: str) -> dict[str, Any]:
    return (
        parse_mulligan_html(html)
        if kind == "cards"
        else parse_class_matchups_page(html)
    )


def page_is_valid(kind: str, html: str) -> bool:
    try:
        parse_page(kind, html)
    except PageRejected:
        return False
    return True


# --- planning -------------------------------------------------------------------


def target_key(target: Mapping[str, Any]) -> str:
    subject = (
        f"deck:{target['deck_id']}"
        if target["subject_type"] == "deck"
        else f"archetype:{str(target['archetype']).casefold()}"
    )
    return "|".join(
        (
            str(target["kind"]),
            subject,
            str(target["format"]),
            str(target["rank"]),
            str(target["period"]),
            str(target["coin"]),
            str(target["opponent_class"]),
        )
    )


def target_url(target: Mapping[str, Any]) -> str:
    coin = COIN_QUERY.get(str(target["coin"]))
    if target["kind"] == "cards":
        query: dict[str, Any] = {}
        if target["subject_type"] == "deck":
            query["deck_id"] = target["deck_id"]
        else:
            query["archetype"] = target["archetype"]
        query.update(
            format=FORMAT_IDS[str(target["format"])],
            rank=target["rank"],
            period=target["period"],
        )
        if coin:
            query["player_has_coin"] = coin
        if target["opponent_class"] != ANY_OPPONENT:
            query["opponent_class"] = target["opponent_class"]
        # The unfiltered table; consumers apply their own sample threshold.
        query.update(min_mull_count=0, min_drawn_count=0, show_counts="yes")
        return f"{HSGURU}/card-stats?{urlencode(query)}"
    query = {"rank": target["rank"], "period": target["period"]}
    if target["subject_type"] == "deck":
        path = f"/deck/{int(target['deck_id'])}"
    else:
        path = f"/archetype/{quote(str(target['archetype']))}"
        query = {"format": FORMAT_IDS[str(target["format"])], **query}
    if coin:
        query["player_has_coin"] = coin
    return f"{HSGURU}{path}?{urlencode(query)}"


def _target(
    *,
    kind: str,
    subject_type: str,
    format_name: str,
    archetype: str,
    rank: str,
    period: str,
    coin: str,
    tier: int,
    expected_games: int,
    deck_id: int | None = None,
    opponent_class: str = ANY_OPPONENT,
) -> dict[str, Any]:
    target = {
        "kind": kind,
        "subject_type": subject_type,
        "format": format_name,
        "archetype": archetype,
        "deck_id": deck_id,
        "rank": rank,
        "period": period,
        "coin": coin,
        "opponent_class": opponent_class,
        "tier": tier,
        "expected_games": expected_games,
    }
    target["target_key"] = target_key(target)
    return target


def _matrix_current_period(structured: Mapping[str, Any]) -> str | None:
    criteria = (structured.get("current_catalog") or {}).get("criteria") or {}
    period = criteria.get("period")
    return str(period) if period else None


def plan_archetype_targets(
    matrix: Mapping[str, Any] | None, settings: Settings
) -> list[dict[str, Any]]:
    structured = ((matrix or {}).get("data") or {}).get("structured") or {}
    current_period = _matrix_current_period(structured)
    targets: list[dict[str, Any]] = []
    for item in structured.get("slices") or []:
        if not isinstance(item, dict):
            continue
        format_name = str(item.get("format") or "")
        rank = str(item.get("rank") or "")
        period = str(item.get("period") or "")
        coin = str(item.get("coin") or "")
        if (
            format_name not in FORMAT_IDS
            or rank not in RANKS
            or coin not in COINS
            or not period
            or period in SKIPPED_PERIODS
        ):
            continue
        for row in item.get("rows") or []:
            if not isinstance(row, dict):
                continue
            archetype = str(row.get("archetype") or "").strip()
            try:
                games = int(row.get("games") or 0)
            except (TypeError, ValueError):
                continue
            if not archetype or len(archetype) > 200:
                continue
            common = {
                "subject_type": "archetype",
                "format_name": format_name,
                "archetype": archetype,
                "rank": rank,
                "period": period,
                "coin": coin,
                "expected_games": games,
            }
            core = period in CORE_ROLLING_PERIODS or _is_cumulative(period)
            if coin == "any_player":
                if games < settings.archetype_min_games:
                    continue
                targets.append(_target(kind="cards", tier=1, **common))
                if core:
                    targets.append(_target(kind="matchups", tier=3, **common))
                if (
                    games >= settings.opponent_min_games
                    and rank in {"all", "legend"}
                    and period == current_period
                ):
                    targets.extend(
                        _target(kind="cards", tier=3, opponent_class=opponent, **common)
                        for opponent in OPPONENT_CLASSES
                    )
            elif core and games >= settings.coin_min_games:
                targets.append(_target(kind="cards", tier=2, **common))
    return targets


def _deck_rows(catalogs: Iterable[Any]) -> list[dict[str, Any]]:
    best: dict[tuple[int, str, str], dict[str, Any]] = {}
    for catalog in catalogs:
        rows = catalog if isinstance(catalog, list) else []
        if isinstance(catalog, dict):
            data = catalog.get("data")
            rows = (
                data if isinstance(data, list) else (data or {}).get("structured") or []
            )
            if isinstance(rows, dict):
                rows = rows.get("decks") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            match = DECK_URL_RE.search(str(row.get("url") or ""))
            format_name = str(row.get("format") or "").casefold()
            rank = str(row.get("sample_rank") or "")
            period = str(row.get("sample_period") or "")
            archetype = str(row.get("archetype") or row.get("title") or "").strip()
            if (
                match is None
                or format_name not in FORMAT_IDS
                or rank not in RANKS
                or not period
                or not archetype
            ):
                continue
            try:
                games = int(row.get("games") or 0)
            except (TypeError, ValueError):
                continue
            deck = {
                "deck_id": int(match.group(1)),
                "format": format_name,
                "archetype": archetype[:200],
                "rank": rank,
                "period": period,
                "games": games,
            }
            key = (deck["deck_id"], rank, period)
            if key not in best or games > best[key]["games"]:
                best[key] = deck
    return list(best.values())


def plan_deck_targets(
    catalogs: Iterable[Any], settings: Settings
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for deck in _deck_rows(catalogs):
        games = int(deck["games"])
        if games < settings.deck_min_games:
            continue
        common = {
            "subject_type": "deck",
            "format_name": deck["format"],
            "archetype": deck["archetype"],
            "deck_id": deck["deck_id"],
            "rank": deck["rank"],
            "period": deck["period"],
            "expected_games": games,
        }
        targets.append(_target(kind="cards", coin="any_player", tier=2, **common))
        targets.append(_target(kind="matchups", coin="any_player", tier=3, **common))
        if games >= settings.deck_coin_min_games:
            targets.extend(
                _target(kind="cards", coin=coin, tier=3, **common)
                for coin in ("on_coin", "going_first")
            )
    return targets


def load_deck_catalogs() -> list[Any]:
    catalogs = []
    for format_name in FORMAT_IDS:
        for suffix in DECK_CATALOG_SUFFIXES:
            dataset = load_dataset(f"hsguru_deck_catalog_{format_name}_{suffix}")
            if dataset:
                catalogs.append(dataset)
    return catalogs


def plan_targets(
    matrix: Mapping[str, Any] | None,
    catalogs: Iterable[Any],
    settings: Settings,
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for target in (
        *plan_archetype_targets(matrix, settings),
        *plan_deck_targets(catalogs, settings),
    ):
        unique.setdefault(target["target_key"], target)
    return list(unique.values())


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_cumulative(period: str) -> bool:
    return period not in ROLLING_TTL_HOURS


def ttl_hours(target: Mapping[str, Any]) -> float:
    period = str(target["period"])
    base = ROLLING_TTL_HOURS.get(period, CUMULATIVE_TTL_HOURS)
    return float(base * TIER_TTL_FACTOR.get(int(target["tier"]), 2))


def due_score(target: Mapping[str, Any], now: datetime) -> float | None:
    """How overdue a target is (>= 1 means due); ``None`` when not due."""

    retry_after = _parse_time(target.get("next_attempt_after"))
    if retry_after is not None and retry_after > now:
        return None
    success = _parse_time(target.get("last_success_at"))
    if success is None:
        return float("inf")
    age_hours = (now - success).total_seconds() / 3600
    score = age_hours / ttl_hours(target)
    if score >= 1:
        return score
    if _is_cumulative(str(target["period"])) and age_hours >= CUMULATIVE_MIN_AGE_HOURS:
        previous = int(target.get("success_expected_games") or 0)
        expected = int(target.get("expected_games") or 0)
        if previous > 0 and expected >= previous * (1 + CUMULATIVE_GROWTH):
            return 1.0
    return None


def due_targets(
    targets: Iterable[Mapping[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    scored = []
    for target in targets:
        score = due_score(target, now)
        if score is not None:
            scored.append((score, dict(target)))
    scored.sort(
        key=lambda item: (
            int(item[1]["tier"]),
            item[1].get("last_success_at") is not None,
            -min(item[0], 1e9),
            period_priority(str(item[1]["period"])),
            -int(item[1].get("expected_games") or 0),
        )
    )
    return [target for _, target in scored]


def _retry_after(failures: int, now: datetime) -> datetime:
    minutes = min(30 * (2 ** max(0, failures)), 24 * 60)
    return now + timedelta(minutes=minutes)


# --- acquisition ----------------------------------------------------------------

PageFetcher = Callable[[str, str], Awaitable[tuple[str, dict[str, Any]]]]


TRANSPORTS = {
    # Scrape.do first so the shared server is not loaded; the local solver
    # only picks up what Scrape.do could not deliver.
    "scrape_do": ("paid", "free"),
    "scrape_do_only": ("paid",),
    "flaresolverr": ("free", "paid"),
}


@dataclass
class Acquirer:
    """Reads one page through the configured route order with per-route circuits.

    ``scrape_do_credit_cap`` of ``None`` means no credit cap; otherwise a paid
    call is made only while the spent credits plus the worst-case price of one
    page stay inside the cap.
    """

    scrape_do_credit_cap: int | None
    routes: tuple[str, ...] = TRANSPORTS["scrape_do"]
    pages_free: int = 0
    pages_scrape_do: int = 0
    scrape_do_credits: int = 0
    free_failures_in_row: int = 0
    paid_failures_in_row: int = 0
    stop_reason: str | None = None
    fetch_free: Callable[[Source], Awaitable[Any]] | None = None
    fetch_paid: Callable[..., Awaitable[Any]] | None = None
    executor: Executor | None = None

    def _source(self, url: str) -> Source:
        return Source(
            id=PAGE_SOURCE_ID,
            url=url,
            site="hsguru",
            category="archetype_analysis",
            kind="pipeline",
        )

    async def _free(self, url: str, kind: str) -> tuple[str, dict[str, Any]]:
        if self.fetch_free is None:
            from .scrapers.flaresolverr import fetch_via_flaresolverr

            result = await fetch_via_flaresolverr(
                self._source(url), wait_ms=FLARESOLVERR_WAIT_MS
            )
        else:
            result = await self.fetch_free(self._source(url))
        html = str(getattr(result, "html", "") or "")
        status = int(getattr(result, "http_status", 200) or 0)
        if not 200 <= status <= 299:
            raise RuntimeError(f"http_status_{status}")
        if PAYWALL_MARKER in html.casefold():
            raise PageRejected("paywalled")
        if not page_is_valid(kind, html):
            raise RuntimeError("content_validation")
        return html, {"backend": "flaresolverr", "request_credits": 0}

    async def _call_paid(self, source: Source, **options: Any) -> Any:
        if self.fetch_paid is not None:
            return await self.fetch_paid(source, **options)
        from .firecrawl_backend import _scrape_sync

        # The provider cascade is blocking. A dedicated pool keeps dozens of
        # parallel pages off the default executor that API handlers share;
        # the copied context carries the Meta-Lab acquisition scope.
        call = functools.partial(
            contextvars.copy_context().run, _scrape_sync, source, **options
        )
        if self.executor is None:
            return await asyncio.to_thread(call)
        return await asyncio.get_running_loop().run_in_executor(self.executor, call)

    async def _paid(self, url: str, kind: str) -> tuple[str, dict[str, Any]]:
        from .meta_intelligence.budget import acquisition_scope

        headers = None
        if self.fetch_paid is None:
            from .hsguru_auth import hsguru_firecrawl_headers

            headers = hsguru_firecrawl_headers()
        # "detail" jobs stop at the Meta-Lab throttle threshold of the shared
        # monthly HSGuru budget, before critical meta refreshes are affected.
        with acquisition_scope(SOURCE_ID, job_class="detail"):
            result = await self._call_paid(
                self._source(url),
                formats=["html"],
                only_main_content=True,
                headers=headers,
                max_age_ms=0,
                timeout_ms=SCRAPE_DO_TIMEOUT_MS,
                skip_providers={"decodo", "firecrawl", "brightdata", "scrapfly"},
                accept_result=lambda scraped: page_is_valid(kind, scraped.html),
                scrape_do_profiles=SCRAPE_DO_PROFILES,
            )
        credits = int(getattr(result, "request_credits", 0) or 0)
        self.scrape_do_credits += credits
        self.pages_scrape_do += 1
        return str(result.html or ""), {
            "backend": str(getattr(result, "backend", "scrape_do") or "scrape_do"),
            "request_credits": credits,
        }

    def paid_allowed(self) -> bool:
        return self.scrape_do_credit_cap is None or (
            self.scrape_do_credits + SCRAPE_DO_RESERVE_CREDITS
            <= self.scrape_do_credit_cap
        )

    def _route_open(self, route: str) -> bool:
        if route == "free":
            return self.free_failures_in_row < FREE_CIRCUIT_THRESHOLD
        return (
            self.paid_failures_in_row < PAID_CIRCUIT_THRESHOLD and self.paid_allowed()
        )

    def _note_dead_end(self) -> None:
        if self.stop_reason is not None or any(
            self._route_open(route) for route in self.routes
        ):
            return
        self.stop_reason = (
            "scrape_do_budget_exhausted"
            if "paid" in self.routes and not self.paid_allowed()
            else "provider_circuit_open"
        )

    async def fetch(self, url: str, kind: str) -> tuple[str, dict[str, Any]]:
        errors: list[str] = []
        for route in self.routes:
            if not self._route_open(route):
                reason = (
                    "budget"
                    if route == "paid" and not self.paid_allowed()
                    else "circuit_open"
                )
                errors.append(f"{route}={reason}")
                continue
            try:
                if route == "free":
                    html, meta = await self._free(url, kind)
                    self.free_failures_in_row = 0
                    self.pages_free += 1
                else:
                    html, meta = await self._paid(url, kind)
                    self.paid_failures_in_row = 0
                return html, meta
            except PageRejected as exc:
                # A premium filter: only the logged-in browser profile reads it.
                errors.append(f"{route}={exc.reason}")
            except Exception as exc:  # noqa: BLE001 - provider boundary
                errors.append(f"{route}={str(exc)[:80] or type(exc).__name__}")
                if route == "free":
                    self.free_failures_in_row += 1
                else:
                    self.paid_failures_in_row += 1
        self._note_dead_end()
        raise RuntimeError("; ".join(errors))


# --- refresh --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _content_hash(parsed: Mapping[str, Any]) -> str:
    payload = {
        "games": parsed.get("games"),
        "winrate": parsed.get("winrate"),
        "rows": parsed["rows"],
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _utc_day_start(now: datetime) -> str:
    return (
        now.astimezone(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


async def _refresh_unlocked(
    *,
    settings: Settings,
    fetch_page: PageFetcher | None,
    now: datetime | None,
    max_pages: int | None,
) -> dict[str, Any]:
    started = now or _now()
    started_at = started.isoformat()

    def clock() -> datetime:
        return now or _now()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.deadline_seconds
    matrix = load_dataset(MATRIX_SOURCE_ID)
    planned = plan_targets(matrix, load_deck_catalogs(), settings)
    init_db()
    conn = get_db_connection()
    try:
        run_id = store.start_run(conn, started_at=started_at)
        if planned:
            store.sync_targets(conn, planned, planned_at=started_at)
        targets = store.active_targets(conn)
        due = due_targets(targets, started)
        budget_pages = max(1, max_pages or settings.max_pages)
        spent_today = store.scrape_do_credits_since(conn, _utc_day_start(started))
        caps = [
            cap
            for cap in (
                settings.scrape_do_run_credits or None,
                settings.scrape_do_daily_credits - spent_today
                if settings.scrape_do_daily_credits
                else None,
            )
            if cap is not None
        ]
        credit_cap = max(0, min(caps)) if caps else None
        executor = ThreadPoolExecutor(
            max_workers=settings.concurrency, thread_name_prefix="hsguru-mulligan"
        )
        acquirer = Acquirer(
            scrape_do_credit_cap=credit_cap,
            routes=TRANSPORTS[settings.transport],
            executor=executor,
        )
        fetch = fetch_page or acquirer.fetch
        run_circuit = max(RUN_CIRCUIT_THRESHOLD, 2 * settings.concurrency)
        queue = list(due[:budget_pages])
        counts = {
            "attempted": 0,
            "changed": 0,
            "unchanged": 0,
            "no_data": 0,
            "failed": 0,
        }
        failures_in_row = 0
        stop_reason: str | None = None
        lock = asyncio.Lock()
        by_kind: dict[str, int] = {}

        async def worker() -> None:
            nonlocal failures_in_row, stop_reason
            while True:
                async with lock:
                    if not queue or stop_reason is not None:
                        return
                    if loop.time() >= deadline:
                        stop_reason = "deadline"
                        return
                    if acquirer.stop_reason is not None:
                        stop_reason = acquirer.stop_reason
                        return
                    target = queue.pop(0)
                url = target_url(target)
                attempted_at = clock().isoformat()
                counts["attempted"] += 1
                try:
                    html, meta = await fetch(url, str(target["kind"]))
                    parsed = parse_page(str(target["kind"]), html)
                except Exception as exc:  # noqa: BLE001 - one target's failure is isolated
                    reason = (
                        exc.reason
                        if isinstance(exc, PageRejected)
                        else str(exc) or type(exc).__name__
                    )
                    counts["failed"] += 1
                    failures = int(target.get("consecutive_failures") or 0)
                    store.record_failure(
                        conn,
                        target,
                        attempted_at=attempted_at,
                        state="paywalled" if reason == "paywalled" else "failed",
                        error=reason,
                        retry_after=_retry_after(failures, clock()).isoformat(),
                    )
                    async with lock:
                        failures_in_row += 1
                        if failures_in_row >= run_circuit and stop_reason is None:
                            stop_reason = "provider_circuit_open"
                    continue
                async with lock:
                    failures_in_row = 0
                games = parsed.get("games")
                state = "ok" if parsed["rows"] else "no_data"
                _, changed = store.record_success(
                    conn,
                    target,
                    fetched_at=attempted_at,
                    games=int(games) if games is not None else None,
                    winrate=parsed.get("winrate"),
                    rows=parsed["rows"],
                    content_hash=_content_hash(parsed),
                    source_url=url,
                    backend=str(meta.get("backend") or "unknown"),
                    request_credits=int(meta.get("request_credits") or 0),
                    state=state,
                )
                counts["changed" if changed else "unchanged"] += 1
                if state == "no_data":
                    counts["no_data"] += 1
                kind_key = f"{target['subject_type']}_{target['kind']}"
                by_kind[kind_key] = by_kind.get(kind_key, 0) + 1

        try:
            await asyncio.gather(*(worker() for _ in range(settings.concurrency)))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if stop_reason is None and len(due) > budget_pages:
            stop_reason = "page_budget"
        pruned = store.prune(conn, now=started, snapshot_days=settings.snapshot_days)
        coverage = store.coverage(conn)
        succeeded = counts["changed"] + counts["unchanged"]
        if not planned or (counts["failed"] and not succeeded):
            state = SourceState.FETCH_ERROR
        elif counts["failed"]:
            state = SourceState.PARTIAL
        else:
            state = SourceState.OK
        finished_at = clock().isoformat()
        summary = {
            "finished_at": finished_at,
            "state": str(state),
            "targets_planned": len(planned),
            "targets_due": len(due),
            **counts,
            "pages_free": acquirer.pages_free,
            "pages_scrape_do": acquirer.pages_scrape_do,
            "scrape_do_credits": acquirer.scrape_do_credits,
            "stop_reason": stop_reason,
            "detail": {
                "by_kind": by_kind,
                "scrape_do_credit_cap": credit_cap,
                "scrape_do_spent_today_before_run": spent_today,
                "pruned": pruned,
            },
        }
        store.finish_run(conn, run_id, summary)
    finally:
        conn.close()

    with_data = sum(int(row["with_data"] or 0) for row in coverage if row["active"])
    active_total = sum(int(row["targets"] or 0) for row in coverage if row["active"])
    remaining_due = max(0, len(due) - counts["attempted"])
    published = bool(planned) and (succeeded > 0 or not counts["failed"])
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": finished_at,
                "http_status": 200,
                "final_url": f"{HSGURU}/card-stats",
                "backend": "hsguru_mulligan_store",
                "data": {
                    "structured": {
                        "type": SOURCE_ID,
                        "fetched_at": finished_at,
                        "targets_active": active_total,
                        "targets_with_data": with_data,
                        "targets_due_after_run": remaining_due,
                        "coverage": coverage,
                        "last_run": {**summary, "started_at": started_at},
                    }
                },
            },
        )
    previous = load_dataset(SOURCE_ID) or {}
    status = {
        "source_id": SOURCE_ID,
        "site": "hsguru",
        "category": "archetype_analysis",
        "url": f"{HSGURU}/card-stats",
        "state": str(SourceState.OK if published else state),
        "fetched_at": finished_at
        if published
        else previous.get("fetched_at") or finished_at,
        "http_status": 200 if succeeded else None,
        "backend": "+".join(
            {"paid": "scrape_do", "free": "flaresolverr"}[route]
            for route in TRANSPORTS[settings.transport]
        ),
        "rows_total": with_data,
        "detail": (
            f"HSGuru mulligan: {succeeded}/{counts['attempted']} pages ok, "
            f"{remaining_due} still due, stop={stop_reason or 'done'}, "
            f"scrape.do credits {acquirer.scrape_do_credits}/"
            f"{'unlimited' if credit_cap is None else credit_cap}"
        ),
        "serving_cached_dataset": not published and bool(previous),
        "last_refresh_state": str(state),
        "last_refresh_at": finished_at,
    }
    if counts["failed"]:
        status["last_refresh_failure_class"] = str(SourceState.FETCH_ERROR)
    save_status(SOURCE_ID, status)
    return {
        "ok": state == SourceState.OK,
        "state": str(state),
        "published": published,
        "serving_cached_dataset": status["serving_cached_dataset"],
        "source_id": SOURCE_ID,
        "fetched_at": finished_at,
        "targets": active_total,
        "targets_with_data": with_data,
        "targets_due": len(due),
        "targets_due_after_run": remaining_due,
        **counts,
        "pages_free": acquirer.pages_free,
        "pages_scrape_do": acquirer.pages_scrape_do,
        "scrape_do_credits": acquirer.scrape_do_credits,
        "scrape_do_credit_cap": credit_cap,
        "stop_reason": stop_reason,
        "pruned": pruned,
    }


async def refresh_hsguru_mulligan(
    *,
    settings: Settings | None = None,
    fetch_page: PageFetcher | None = None,
    now: datetime | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Read the most overdue HSGuru statistics pages unless another run owns them."""

    locks = ResourceLockSet(
        [SOURCE_ID], metadata={"operation": "refresh_hsguru_mulligan"}
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE_ID,
            **exc.as_outcome(),
        }
    try:
        return await _refresh_unlocked(
            settings=settings or Settings.from_env(),
            fetch_page=fetch_page,
            now=now,
            max_pages=max_pages,
        )
    finally:
        locks.release()


def estimate_plan(
    matrix: Mapping[str, Any] | None,
    catalogs: Iterable[Any],
    settings: Settings,
) -> dict[str, Any]:
    """Pages per day the current plan needs by TTL (growth re-reads come on top)."""

    targets = plan_targets(matrix, catalogs, settings)
    by_group: dict[str, dict[str, float]] = {}
    total = 0.0
    for target in targets:
        group = f"{target['subject_type']}_{target['kind']}_tier{target['tier']}"
        per_day = 24 / ttl_hours(target)
        entry = by_group.setdefault(group, {"targets": 0, "pages_per_day": 0.0})
        entry["targets"] += 1
        entry["pages_per_day"] += per_day
        total += per_day
    return {
        "targets": len(targets),
        "pages_per_day_by_ttl": round(total, 1),
        "groups": {
            name: {
                "targets": int(value["targets"]),
                "pages_per_day": round(value["pages_per_day"], 1),
            }
            for name, value in sorted(by_group.items())
        },
    }


# --- reads ----------------------------------------------------------------------


def _read(callback: Callable[[Any], Any]) -> Any:
    init_db()
    conn = get_db_connection()
    try:
        return callback(conn)
    finally:
        conn.close()


def _public_slice(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": target["kind"],
        "subject_type": target["subject_type"],
        "format": target["format"],
        "archetype": target["archetype"],
        "deck_id": target.get("deck_id"),
        "rank": target["rank"],
        "period": target["period"],
        "coin": target["coin"],
        "opponent_class": target["opponent_class"],
    }


@dataclass(frozen=True)
class Selector:
    """One stored page: subject plus slice, as the read API names it."""

    kind: str
    subject_type: str
    format_name: str | None
    archetype: str | None
    deck_id: int | None
    rank: str
    period: str
    coin: str = "any_player"
    opponent_class: str = ANY_OPPONENT


def current_period() -> str | None:
    """HSGuru's current window (the expansion) as the meta matrix resolved it."""

    matrix = load_dataset(MATRIX_SOURCE_ID) or {}
    return _matrix_current_period(((matrix.get("data") or {}).get("structured")) or {})


def target_is_stale(target: Mapping[str, Any], now: datetime | None = None) -> bool:
    checked = _parse_time(target.get("last_success_at"))
    if checked is None or not target.get("active"):
        return True
    age_hours = ((now or _now()) - checked).total_seconds() / 3600
    return age_hours > 2 * ttl_hours(target)


def _find(conn: Any, selector: Selector) -> dict[str, Any] | None:
    return store.find_target(
        conn,
        kind=selector.kind,
        subject_type=selector.subject_type,
        format_name=selector.format_name,
        archetype=selector.archetype,
        deck_id=selector.deck_id,
        rank=selector.rank,
        period=selector.period,
        coin=selector.coin,
        opponent_class=selector.opponent_class,
    )


def get_statistics(
    selector: Selector,
    *,
    min_mull_count: int = 0,
    min_drawn_count: int = 0,
) -> dict[str, Any] | None:
    """The latest stored page of one slice, or ``None`` when it was never read."""

    def read(conn):
        target = _find(conn, selector)
        if target is None:
            return None
        snapshot_row = store.snapshot(conn, int(target["latest_snapshot_id"]))
        if snapshot_row is None:
            return None
        return target, snapshot_row, store.snapshot_rows(conn, snapshot_row)

    found = _read(read)
    if found is None:
        return None
    target, snapshot_row, rows = found
    return assemble_statistics(
        target,
        snapshot_row,
        rows,
        min_mull_count=min_mull_count,
        min_drawn_count=min_drawn_count,
    )


def assemble_statistics(
    target: Mapping[str, Any],
    snapshot_row: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    min_mull_count: int = 0,
    min_drawn_count: int = 0,
) -> dict[str, Any]:
    """REST and GraphQL response for a stored target, its snapshot and rows."""

    items = [
        {key: value for key, value in row.items() if key not in {"id", "snapshot_id"}}
        for row in rows
    ]
    if target["kind"] == "cards":
        items = [
            row
            for row in items
            if int(row.get("mulligan_count") or 0) >= min_mull_count
            and int(row.get("drawn_count") or 0) >= min_drawn_count
        ]
    games = target.get("last_games")
    return {
        **_public_slice(target),
        "games": games if games is not None else snapshot_row["games"],
        "winrate": snapshot_row.get("winrate"),
        "snapshot_id": snapshot_row["id"],
        "changed_at": snapshot_row["fetched_at"],
        "checked_at": target.get("last_success_at"),
        "active": bool(target["active"]),
        "stale": target_is_stale(target),
        "source_url": snapshot_row["source_url"],
        "rows": items,
    }


def get_history(
    selector: Selector, *, card: str | None, limit: int
) -> dict[str, Any] | None:
    """Stored snapshots of one slice, newest first, optionally with one card's row."""

    def read(conn):
        target = _find(conn, selector)
        if target is None:
            return None
        points = store.target_snapshots(conn, int(target["id"]), limit=limit)
        cards = (
            store.card_history(conn, int(target["id"]), card=card, limit=limit)
            if card and selector.kind == "cards"
            else {}
        )
        return target, points, cards

    found = _read(read)
    if found is None:
        return None
    target, points, cards = found
    for point in points:
        point["snapshot_id"] = point.pop("id")
        if card:
            point["card"] = cards.get(int(point["snapshot_id"]))
    return {
        **_public_slice(target),
        "checked_at": target.get("last_success_at"),
        "points": points,
    }


def list_slices(
    *,
    subject_type: str,
    format_name: str | None,
    archetype: str | None,
    deck_id: int | None,
) -> list[dict[str, Any]]:
    return _read(
        lambda conn: store.subject_targets(
            conn,
            subject_type=subject_type,
            format_name=format_name,
            archetype=archetype,
            deck_id=deck_id,
        )
    )


def coverage_report(*, runs: int = 10) -> dict[str, Any]:
    def read(conn):
        return store.coverage(conn), store.recent_runs(conn, limit=runs)

    coverage, recent = _read(read)
    return {
        "coverage": coverage,
        "runs": recent,
        "settings": Settings.from_env().__dict__,
    }


def latest_revision() -> str | None:
    """Cache revision for public HTTP caching."""

    conn = get_db_connection()
    try:
        return store.latest_revision(conn)
    except Exception:  # noqa: BLE001 - a missing table means no revision yet
        return None
    finally:
        conn.close()
