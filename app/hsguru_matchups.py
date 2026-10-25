"""HSGuru archetype-versus-archetype matchup matrices for every public slice.

HSGuru ``/matchups`` serves precomputed matrices only for ``format x rank x
period`` (``DeckTracker.aggregated_matchups``): Standard is recomputed every
hour at :51 UTC, Wild every three hours at :23. ``min_matchup_sample`` and
``min_archetype_sample`` only filter the rendered table, so one page read with
both at 1 holds every cell and any threshold is applied locally. The coin
filter is not part of the public aggregate: ``player_has_coin`` falls back to
a premium live query that ends on HSGuru's error page even for our session
(checked 2026-10-25), so only ``any_player`` is collected.

Every slice is an incremental target in SQLite (``hsguru_matchup_slices``)
holding its latest matrix. A slice is due when its TTL expired: short windows
every hour, wide and cumulative windows every half or whole day (see
``ttl_hours``). Each hourly run reads the due slices through plain Scrape.do
(``render=false``, one credit, a few seconds a page) with at most five pages
in parallel, the local FlareSolverr as fallback.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from lxml import html as lxml_html

from .db import get_db_connection
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import Source
from .storage import load_dataset, save_dataset, save_status

logger = logging.getLogger(__name__)

SOURCE_ID = "hsguru_matchups"
PAGE_SOURCE_ID = f"{SOURCE_ID}:page"
MATRIX_SOURCE_ID = "hsguru_meta_matrix"
HSGURU_MATCHUPS_URL = "https://www.hsguru.com/matchups"
FORMAT_IDS = {"standard": 2, "wild": 1}
# The ranks HSGuru /matchups offers (its rank menu), most selective first.
RANKS = (
    "all",
    "top_legend",
    "top_5k",
    "legend",
    "diamond_to_legend",
    "diamond_4to1",
    "bronze_plat",
)
COINS = ("any_player", "on_coin", "going_first")
COLLECTED_COINS = ("any_player",)
ROLLING_PERIODS = (
    "past_3_hours",
    "past_6_hours",
    "past_day",
    "past_3_days",
    "past_week",
    "past_2_weeks",
    "past_30_days",
)
# HSGuru's lowest sample filters; every page is read with them.
QUERY_MIN_SAMPLE = 1
# HSGuru's own menu values, offered to readers as presets.
MIN_GAMES_PRESETS = (1, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
MIN_ARCHETYPE_GAMES_PRESETS = (
    1, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000,
)
MAX_PERIODS = 16

# Hours between reads by period class. Standard is recomputed hourly, Wild every
# three hours, so Wild TTLs never go below three hours.
STANDARD_TTL_HOURS = {
    "past_3_hours": 1,
    "past_6_hours": 1,
    "past_day": 2,
    "ban": 2,
    "past_3_days": 6,
    "past_week": 12,
    "patch": 12,
    "named": 12,
    "past_2_weeks": 24,
    "past_30_days": 24,
}
WILD_TTL_HOURS = {
    "past_3_hours": 3,
    "past_6_hours": 3,
    "past_day": 6,
    "ban": 6,
    "past_3_days": 12,
    "past_week": 24,
    "patch": 24,
    "named": 24,
    "past_2_weeks": 48,
    "past_30_days": 48,
}
# An hourly timer meets a one-hour TTL a few seconds early; the slack keeps
# such a slice due instead of skipping it for a whole hour.
DUE_SLACK_HOURS = 0.25
UNAVAILABLE_RETRY_HOURS = 24
MAX_CONCURRENCY = 5
SCRAPE_DO_TIMEOUT_MS = 30_000
# Plain page only: the matrix is in HSGuru's dead render and a browser profile
# costs five credits for the same table.
SCRAPE_DO_PROFILES = ((False, False),)
RUN_CIRCUIT_THRESHOLD = 10
PREMIUM_MARKER = "you do not have access to these filters"
ERROR_MARKER = "looks like something went wrong"
_ARCHETYPE_LABEL_RE = re.compile(r"^(?P<name>.+) - (?P<games>\d+) games$", re.DOTALL)
_CELL_LABEL_RE = re.compile(
    r"^(?P<pair>.+) - (?P<winrate>\d+(?:\.\d+)?)% (?P<games>\d+) games$", re.DOTALL
)
_CLASS_TOKENS = frozenset(
    {
        "deathknight",
        "demonhunter",
        "druid",
        "hunter",
        "mage",
        "paladin",
        "priest",
        "rogue",
        "shaman",
        "warlock",
        "warrior",
    }
)

SLICES = "hsguru_matchup_slices"
RUNS = "hsguru_matchup_runs"
SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {SLICES} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slice_key TEXT NOT NULL UNIQUE,
    format TEXT NOT NULL,
    rank TEXT NOT NULL,
    period TEXT NOT NULL,
    coin TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    planned_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_changed_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_after TEXT,
    last_error TEXT,
    total_games INTEGER,
    archetypes INTEGER,
    cells INTEGER,
    content_hash TEXT,
    source_url TEXT,
    backend TEXT,
    request_credits INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {RUNS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL,
    slices_planned INTEGER NOT NULL DEFAULT 0,
    slices_due INTEGER NOT NULL DEFAULT 0,
    attempted INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    unavailable INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    pages_free INTEGER NOT NULL DEFAULT 0,
    pages_scrape_do INTEGER NOT NULL DEFAULT 0,
    scrape_do_credits INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT
)
""",
    f"CREATE INDEX IF NOT EXISTS idx_hsguru_matchup_slices_lookup ON {SLICES}(format, rank, period, coin)",
)


def ensure_hsguru_matchups_schema(conn: sqlite3.Connection) -> None:
    """Create the additive HSGuru matchup tables on an existing database."""

    for statement in SCHEMA:
        conn.execute(statement)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    # Scrape.do allows 50 parallel requests per account; the mulligan takes 40.
    concurrency: int = MAX_CONCURRENCY
    max_pages: int = 400
    deadline_seconds: int = 30 * 60

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        return cls(
            concurrency=_env_int(
                "HS_HSGURU_MATCHUPS_CONCURRENCY",
                base.concurrency,
                minimum=1,
                maximum=MAX_CONCURRENCY,
            ),
            max_pages=_env_int(
                "HS_HSGURU_MATCHUPS_MAX_PAGES", base.max_pages, minimum=1, maximum=5000
            ),
            deadline_seconds=_env_int(
                "HS_HSGURU_MATCHUPS_DEADLINE_SECONDS",
                base.deadline_seconds,
                minimum=60,
                maximum=3 * 3600,
            ),
        )


# --- plan -----------------------------------------------------------------------


def slice_key(format_name: str, rank: str, period: str, coin: str = "any_player") -> str:
    return f"{format_name}|{rank}|{period}|{coin}"


def slice_url(format_name: str, rank: str, period: str) -> str:
    query = urlencode(
        {
            "format": FORMAT_IDS[format_name],
            "rank": rank,
            "period": period,
            "min_archetype_sample": QUERY_MIN_SAMPLE,
            "min_matchup_sample": QUERY_MIN_SAMPLE,
        }
    )
    return f"{HSGURU_MATCHUPS_URL}?{query}"


def period_class(period: str) -> str:
    if period in ROLLING_PERIODS:
        return period
    if period.endswith("_ban"):
        return "ban"
    if period.startswith("patch_"):
        return "patch"
    return "named"


def ttl_hours(format_name: str, period: str) -> float:
    table = WILD_TTL_HOURS if format_name == "wild" else STANDARD_TTL_HOURS
    return float(table[period_class(period)])


def _valid_period(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z0-9_.]{1,64}", value))


def matrix_structured() -> dict[str, Any]:
    matrix = load_dataset(MATRIX_SOURCE_ID) or {}
    return ((matrix.get("data") or {}).get("structured")) or {}


def planned_periods(structured: Mapping[str, Any]) -> tuple[str, ...]:
    """HSGuru's advertised windows as the meta matrix discovered them.

    The meta and matchups pages share HSGuru's period menu, so the matrix
    dimensions (rolling windows, the current patch, the expansion and the Bans
    window) are the matchup periods too. Without a matrix only the rolling
    windows are known.
    """

    advertised = (structured.get("dimensions") or {}).get("periods") or []
    periods = [period for period in advertised if _valid_period(period)]
    if not periods:
        periods = list(ROLLING_PERIODS)
    return tuple(dict.fromkeys(periods))[:MAX_PERIODS]


def current_period(structured: Mapping[str, Any]) -> str | None:
    period = ((structured.get("current_catalog") or {}).get("criteria") or {}).get(
        "period"
    )
    return str(period) if _valid_period(period) else None


def plan_slices(periods: Iterable[str]) -> list[dict[str, Any]]:
    return [
        {
            "slice_key": slice_key(format_name, rank, period),
            "format": format_name,
            "rank": rank,
            "period": period,
            "coin": "any_player",
        }
        for format_name in FORMAT_IDS
        for rank in RANKS
        for period in periods
    ]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def due_score(target: Mapping[str, Any], now: datetime) -> float | None:
    """How overdue a slice is (>= 1 means due); ``None`` when not due."""

    retry_after = _parse_time(target.get("next_attempt_after"))
    if retry_after is not None and retry_after > now:
        return None
    success = _parse_time(target.get("last_success_at"))
    if success is None:
        return float("inf")
    ttl = ttl_hours(str(target["format"]), str(target["period"]))
    age_hours = (now - success).total_seconds() / 3600
    if age_hours + DUE_SLACK_HOURS < ttl:
        return None
    return max(1.0, age_hours / ttl)


def due_slices(
    targets: Iterable[Mapping[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    scored = [
        (score, dict(target))
        for target in targets
        if (score := due_score(target, now)) is not None
    ]
    # Never-read slices first, then the most overdue; Standard before Wild.
    scored.sort(
        key=lambda item: (
            item[1].get("last_success_at") is not None,
            -min(item[0], 1e9),
            item[1]["format"] != "standard",
            RANKS.index(item[1]["rank"]) if item[1]["rank"] in RANKS else len(RANKS),
        )
    )
    return [target for _, target in scored]


def estimate_plan(periods: Iterable[str]) -> dict[str, Any]:
    """Pages (= Scrape.do credits with the plain profile) per day by TTL."""

    by_period: dict[str, float] = {}
    for target in plan_slices(tuple(periods)):
        key = f"{target['format']}:{target['period']}"
        by_period[key] = by_period.get(key, 0.0) + 24 / ttl_hours(
            target["format"], target["period"]
        )
    total = sum(by_period.values())
    return {
        "slices": len(plan_slices(tuple(periods))),
        "pages_per_day": round(total, 1),
        "scrape_do_credits_per_day": round(total, 1),
        "scrape_do_credits_per_30_days": round(total * 30),
        "by_format_period": {key: round(value, 1) for key, value in sorted(by_period.items())},
    }


# --- parsing --------------------------------------------------------------------


class PageUnavailable(RuntimeError):
    """HSGuru has no public aggregate for this slice."""


def _classes(element: Any) -> set[str]:
    return {token.casefold() for token in str(element.get("class") or "").split()}


def _matrix_table(document: Any) -> Any | None:
    tables = document.xpath("//table[.//*[@phx-value-total_games]]")
    return tables[0] if tables else None


def parse_matchups_page(html: str) -> dict[str, Any]:
    """Parse one HSGuru /matchups page into archetypes and non-empty cells.

    Every cell carries ``aria-label="A versus B - 54.2% 1234 games"`` and every
    row ``aria-label="A - 179032 games"`` with the overall winrate as text.
    """

    lowered = html.casefold()
    if PREMIUM_MARKER in lowered:
        raise PageUnavailable("no_public_aggregate")
    if not html.strip():
        raise ValueError("empty page")
    document = lxml_html.fromstring(html)
    table = _matrix_table(document)
    if table is None:
        if ERROR_MARKER in lowered:
            raise RuntimeError("upstream_error_page")
        raise ValueError("matchup table not found")
    total_raw = table.xpath(".//*[@phx-value-total_games]/@phx-value-total_games")[0]
    total_games = int(str(total_raw).strip() or 0)

    archetypes: list[dict[str, Any]] = []
    raw_cells: list[tuple[str, str, float, int]] = []
    for tr in table.xpath("./tbody/tr"):
        tds = tr.xpath("./td")
        if len(tds) < 2:
            continue
        label = _ARCHETYPE_LABEL_RE.match(str(tds[0].get("aria-label") or "").strip())
        if label is None:
            raise ValueError("archetype row without a games label")
        name = label.group("name").strip()
        winrate_text = tds[0].text_content().strip()
        try:
            winrate = float(winrate_text) if winrate_text else None
        except ValueError as exc:
            raise ValueError(f"bad archetype winrate {winrate_text!r}") from exc
        if winrate is not None and not 0 <= winrate <= 100:
            raise ValueError(f"archetype winrate out of range for {name}")
        class_name = next(
            (token for token in sorted(_classes(tds[1]) & _CLASS_TOKENS)), ""
        )
        archetypes.append(
            {
                "archetype": name,
                "class": class_name.upper() if class_name else None,
                "games": int(label.group("games")),
                "winrate": winrate,
            }
        )
        prefix = f"{name} versus "
        for td in tds[2:]:
            cell = _CELL_LABEL_RE.match(str(td.get("aria-label") or "").strip())
            if cell is None or not cell.group("pair").startswith(prefix):
                raise ValueError(f"unexpected matchup cell in row {name}")
            games = int(cell.group("games"))
            if games <= 0:
                continue
            cell_winrate = float(cell.group("winrate"))
            if not 0 <= cell_winrate <= 100:
                raise ValueError(f"matchup winrate out of range in row {name}")
            raw_cells.append(
                (name, cell.group("pair")[len(prefix):].strip(), cell_winrate, games)
            )

    index = {row["archetype"]: position for position, row in enumerate(archetypes)}
    if len(index) != len(archetypes):
        raise ValueError("duplicate archetype rows")
    cells = []
    for name, opponent, winrate, games in raw_cells:
        if opponent not in index:
            raise ValueError(f"matchup opponent {opponent!r} has no row")
        cells.append([index[name], index[opponent], winrate, games])
    if archetypes and total_games <= 0:
        raise ValueError("rows without total games")
    return {"total_games": total_games, "archetypes": archetypes, "cells": cells}


@functools.lru_cache(maxsize=8)
def _parse_cached(html: str) -> dict[str, Any]:
    return parse_matchups_page(html)


def page_is_valid(html: str) -> bool:
    """Accept a parsed matrix, or HSGuru's statement that no public one exists."""

    try:
        _parse_cached(html)
    except PageUnavailable:
        return True
    except Exception:  # noqa: BLE001 - the provider retries a rejected page
        return False
    return True


def content_hash(parsed: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# --- acquisition ----------------------------------------------------------------

PageFetcher = Callable[[str], Awaitable[tuple[str, dict[str, Any]]]]


@dataclass
class Acquirer:
    """Plain Scrape.do first, the local FlareSolverr only when it failed."""

    pages_free: int = 0
    pages_scrape_do: int = 0
    scrape_do_credits: int = 0

    def _source(self, url: str) -> Source:
        return Source(
            id=PAGE_SOURCE_ID, url=url, site="hsguru", category="matchups", kind="pipeline"
        )

    async def _paid(self, url: str) -> tuple[str, dict[str, Any]]:
        from .firecrawl_backend import _scrape_sync
        from .meta_intelligence.budget import acquisition_scope

        with acquisition_scope(SOURCE_ID, job_class="detail"):
            result = await asyncio.to_thread(
                _scrape_sync,
                self._source(url),
                formats=["html"],
                only_main_content=True,
                headers=None,
                max_age_ms=0,
                timeout_ms=SCRAPE_DO_TIMEOUT_MS,
                skip_providers={"decodo", "firecrawl", "brightdata", "scrapfly"},
                accept_result=lambda scraped: page_is_valid(scraped.html or ""),
                scrape_do_profiles=SCRAPE_DO_PROFILES,
            )
        credits = int(getattr(result, "request_credits", 0) or 0)
        self.scrape_do_credits += credits
        self.pages_scrape_do += 1
        return str(result.html or ""), {
            "backend": str(getattr(result, "backend", "scrape_do") or "scrape_do"),
            "request_credits": credits,
        }

    async def _free(self, url: str) -> tuple[str, dict[str, Any]]:
        from .scrapers.flaresolverr import fetch_via_flaresolverr

        result = await fetch_via_flaresolverr(self._source(url), wait_ms=0)
        html = str(getattr(result, "html", "") or "")
        status = int(getattr(result, "http_status", 200) or 0)
        if not 200 <= status <= 299:
            raise RuntimeError(f"http_status_{status}")
        if not page_is_valid(html):
            raise RuntimeError("content_validation")
        self.pages_free += 1
        return html, {"backend": "flaresolverr", "request_credits": 0}

    async def fetch(self, url: str) -> tuple[str, dict[str, Any]]:
        errors = []
        for route in (self._paid, self._free):
            try:
                return await route(url)
            except Exception as exc:  # noqa: BLE001 - provider boundary
                errors.append(f"{route.__name__.strip('_')}={str(exc)[:120] or type(exc).__name__}")
        raise RuntimeError("; ".join(errors))


# --- store ----------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = get_db_connection()
    ensure_hsguru_matchups_schema(conn)
    return conn


def sync_slices(
    conn: sqlite3.Connection, planned: Iterable[Mapping[str, Any]], *, planned_at: str
) -> int:
    """Upsert the plan; slices that left it stay readable but inactive."""

    keys = []
    with conn:
        for target in planned:
            keys.append(target["slice_key"])
            conn.execute(
                f"""
                INSERT INTO {SLICES} (slice_key, format, rank, period, coin, active, planned_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(slice_key) DO UPDATE SET active = 1, planned_at = excluded.planned_at
                """,
                (
                    target["slice_key"],
                    target["format"],
                    target["rank"],
                    target["period"],
                    target["coin"],
                    planned_at,
                ),
            )
        placeholders = ",".join("?" for _ in keys) or "''"
        conn.execute(
            f"UPDATE {SLICES} SET active = 0 WHERE active = 1 AND slice_key NOT IN ({placeholders})",
            keys,
        )
    return len(keys)


def _retry_after(failures: int, now: datetime) -> datetime:
    return now + timedelta(minutes=min(30 * (2 ** max(0, failures)), 12 * 60))


def record_success(
    conn: sqlite3.Connection,
    target: Mapping[str, Any],
    parsed: Mapping[str, Any],
    *,
    fetched_at: str,
    source_url: str,
    backend: str,
    request_credits: int,
) -> bool:
    digest = content_hash(parsed)
    changed = digest != target.get("content_hash")
    with conn:
        conn.execute(
            f"""
            UPDATE {SLICES} SET
                state = ?, last_attempt_at = ?, last_success_at = ?,
                last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END,
                consecutive_failures = 0, next_attempt_after = NULL, last_error = NULL,
                total_games = ?, archetypes = ?, cells = ?, content_hash = ?,
                source_url = ?, backend = ?, request_credits = ?, payload_json = ?
            WHERE slice_key = ?
            """,
            (
                "ok" if parsed["archetypes"] else "no_data",
                fetched_at,
                fetched_at,
                int(changed),
                fetched_at,
                int(parsed["total_games"]),
                len(parsed["archetypes"]),
                len(parsed["cells"]),
                digest,
                source_url,
                backend,
                request_credits,
                json.dumps(parsed, ensure_ascii=False, separators=(",", ":")),
                target["slice_key"],
            ),
        )
    return changed


def record_failure(
    conn: sqlite3.Connection,
    target: Mapping[str, Any],
    *,
    attempted_at: str,
    state: str,
    error: str,
    retry_after: str,
) -> None:
    with conn:
        conn.execute(
            f"""
            UPDATE {SLICES} SET state = ?, last_attempt_at = ?, last_error = ?,
                consecutive_failures = consecutive_failures + 1, next_attempt_after = ?
            WHERE slice_key = ?
            """,
            (state, attempted_at, error[:300], retry_after, target["slice_key"]),
        )


def _slice_rows(conn: sqlite3.Connection, *, active_only: bool) -> list[dict[str, Any]]:
    clause = "WHERE active = 1" if active_only else ""
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT id, slice_key, format, rank, period, coin, active, state,
                   last_attempt_at, last_success_at, last_changed_at,
                   consecutive_failures, next_attempt_after, last_error,
                   total_games, archetypes, cells, content_hash, source_url, backend
            FROM {SLICES} {clause} ORDER BY format, rank, period
            """
        )
    ]


# --- refresh --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_day_start(now: datetime) -> str:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _public_slice(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": row["slice_key"],
        "format": row["format"],
        "rank": row["rank"],
        "period": row["period"],
        "coin": row["coin"],
        "active": bool(row["active"]),
        "state": row["state"],
        "checked_at": row["last_success_at"],
        "changed_at": row["last_changed_at"],
        "total_games": row["total_games"],
        "archetypes": row["archetypes"],
        "cells": row["cells"],
        "stale": slice_is_stale(row),
        "last_error": row["last_error"],
    }


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
    structured = matrix_structured()
    periods = planned_periods(structured)
    planned = plan_slices(periods)
    acquirer = Acquirer()
    fetch = fetch_page or acquirer.fetch
    counts = {"attempted": 0, "changed": 0, "unchanged": 0, "unavailable": 0, "failed": 0}
    stop_reason: str | None = None
    conn = _connect()
    try:
        with conn:
            run_id = conn.execute(
                f"INSERT INTO {RUNS} (started_at, state) VALUES (?, 'running')",
                (started_at,),
            ).lastrowid
        sync_slices(conn, planned, planned_at=started_at)
        due = due_slices(_slice_rows(conn, active_only=True), started)
        budget = max(1, max_pages or settings.max_pages)
        queue = list(due[:budget])
        failures_in_row = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal failures_in_row, stop_reason
            while True:
                async with lock:
                    if not queue or stop_reason is not None:
                        return
                    if loop.time() >= deadline:
                        stop_reason = "deadline"
                        return
                    target = queue.pop(0)
                url = slice_url(target["format"], target["rank"], target["period"])
                attempted_at = clock().isoformat()
                counts["attempted"] += 1
                try:
                    html, meta = await fetch(url)
                    parsed = _parse_cached(html)
                except PageUnavailable as exc:
                    counts["unavailable"] += 1
                    record_failure(
                        conn,
                        target,
                        attempted_at=attempted_at,
                        state="unavailable",
                        error=str(exc),
                        retry_after=(
                            clock() + timedelta(hours=UNAVAILABLE_RETRY_HOURS)
                        ).isoformat(),
                    )
                    async with lock:
                        failures_in_row = 0
                    continue
                except Exception as exc:  # noqa: BLE001 - one slice's failure is isolated
                    counts["failed"] += 1
                    record_failure(
                        conn,
                        target,
                        attempted_at=attempted_at,
                        state="failed",
                        error=str(exc) or type(exc).__name__,
                        retry_after=_retry_after(
                            int(target.get("consecutive_failures") or 0), clock()
                        ).isoformat(),
                    )
                    async with lock:
                        failures_in_row += 1
                        if failures_in_row >= RUN_CIRCUIT_THRESHOLD and stop_reason is None:
                            stop_reason = "provider_circuit_open"
                    continue
                async with lock:
                    failures_in_row = 0
                changed = record_success(
                    conn,
                    target,
                    parsed,
                    fetched_at=attempted_at,
                    source_url=url,
                    backend=str(meta.get("backend") or "unknown"),
                    request_credits=int(meta.get("request_credits") or 0),
                )
                counts["changed" if changed else "unchanged"] += 1

        await asyncio.gather(*(worker() for _ in range(settings.concurrency)))
        if stop_reason is None and len(due) > budget:
            stop_reason = "page_budget"
        succeeded = counts["changed"] + counts["unchanged"]
        if counts["failed"] and not succeeded:
            state = SourceState.FETCH_ERROR
        elif counts["failed"]:
            state = SourceState.PARTIAL
        else:
            state = SourceState.OK
        finished_at = clock().isoformat()
        credits_today = conn.execute(
            f"SELECT coalesce(sum(scrape_do_credits), 0) FROM {RUNS} WHERE started_at >= ?",
            (_utc_day_start(started),),
        ).fetchone()[0]
        with conn:
            conn.execute(
                f"""
                UPDATE {RUNS} SET finished_at = ?, state = ?, slices_planned = ?,
                    slices_due = ?, attempted = ?, changed = ?, unchanged = ?,
                    unavailable = ?, failed = ?, pages_free = ?, pages_scrape_do = ?,
                    scrape_do_credits = ?, stop_reason = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    str(state),
                    len(planned),
                    len(due),
                    counts["attempted"],
                    counts["changed"],
                    counts["unchanged"],
                    counts["unavailable"],
                    counts["failed"],
                    acquirer.pages_free,
                    acquirer.pages_scrape_do,
                    acquirer.scrape_do_credits,
                    stop_reason,
                    run_id,
                ),
            )
        slices = [_public_slice(row) for row in _slice_rows(conn, active_only=True)]
    finally:
        conn.close()

    with_data = sum(1 for row in slices if row["checked_at"])
    remaining_due = max(0, len(due) - counts["attempted"])
    # A run that read nothing because nothing was due still publishes.
    published = with_data > 0 and (succeeded > 0 or not counts["failed"])
    summary = {
        **counts,
        "slices_due": len(due),
        "pages_free": acquirer.pages_free,
        "pages_scrape_do": acquirer.pages_scrape_do,
        "scrape_do_credits": acquirer.scrape_do_credits,
        "scrape_do_credits_today": int(credits_today) + acquirer.scrape_do_credits,
        "stop_reason": stop_reason,
    }
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": finished_at,
                "http_status": 200,
                "final_url": HSGURU_MATCHUPS_URL,
                "backend": "hsguru_matchups_store",
                "data": {
                    "structured": {
                        "type": SOURCE_ID,
                        "fetched_at": finished_at,
                        "dimensions": {
                            "formats": list(FORMAT_IDS),
                            "ranks": list(RANKS),
                            "periods": list(periods),
                            "coins": list(COLLECTED_COINS),
                            "min_games": list(MIN_GAMES_PRESETS),
                            "min_archetype_games": list(MIN_ARCHETYPE_GAMES_PRESETS),
                        },
                        "current_period": current_period(structured),
                        "slices_active": len(slices),
                        "slices_with_data": with_data,
                        "slices_due_after_run": remaining_due,
                        "slices": slices,
                        "estimate": estimate_plan(periods),
                        "last_run": {
                            **summary,
                            "started_at": started_at,
                            "finished_at": finished_at,
                            "state": str(state),
                        },
                    }
                },
            },
        )
    previous = load_dataset(SOURCE_ID) or {}
    status = {
        "source_id": SOURCE_ID,
        "site": "hsguru",
        "category": "matchups",
        "url": HSGURU_MATCHUPS_URL,
        "state": str(SourceState.OK if published else state),
        "fetched_at": finished_at if published else previous.get("fetched_at") or finished_at,
        "http_status": 200 if succeeded or published else None,
        "backend": "scrape_do+flaresolverr",
        "rows_total": with_data,
        "detail": (
            f"HSGuru matchups: {succeeded}/{counts['attempted']} pages ok, "
            f"{counts['unavailable']} without a public aggregate, {remaining_due} still due, "
            f"stop={stop_reason or 'done'}, scrape.do credits {acquirer.scrape_do_credits}"
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
        "source_id": SOURCE_ID,
        "fetched_at": finished_at,
        "slices": len(slices),
        "slices_with_data": with_data,
        "slices_due_after_run": remaining_due,
        **summary,
    }


async def refresh_hsguru_matchups(
    *,
    settings: Settings | None = None,
    fetch_page: PageFetcher | None = None,
    now: datetime | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Read the overdue HSGuru matchup slices unless another run owns them."""

    locks = ResourceLockSet([SOURCE_ID], metadata={"operation": "refresh_hsguru_matchups"})
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {"ok": True, "published": False, "source_id": SOURCE_ID, **exc.as_outcome()}
    try:
        return await _refresh_unlocked(
            settings=settings or Settings.from_env(),
            fetch_page=fetch_page,
            now=now,
            max_pages=max_pages,
        )
    finally:
        locks.release()


# --- reads ----------------------------------------------------------------------


def slice_is_stale(row: Mapping[str, Any], now: datetime | None = None) -> bool:
    checked = _parse_time(row.get("last_success_at"))
    if checked is None or not row.get("active"):
        return True
    age_hours = ((now or _now()) - checked).total_seconds() / 3600
    return age_hours > 2 * ttl_hours(str(row["format"]), str(row["period"])) + 1


def summary() -> dict[str, Any]:
    """The published run summary: dimensions, current period, slice states."""

    dataset = load_dataset(SOURCE_ID) or {}
    return ((dataset.get("data") or {}).get("structured")) or {}


def default_period(periods: Iterable[str], current: str | None) -> str | None:
    """The Bans window HearthPulse shows by default, else the current window."""

    available = list(periods)
    for period in available:
        if period.endswith("_ban"):
            return period
    if current in available:
        return current
    return "past_week" if "past_week" in available else (available[0] if available else None)


def list_slices() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        return [_public_slice(row) for row in _slice_rows(conn, active_only=False)]
    finally:
        conn.close()


def get_matchups(
    *,
    format_name: str,
    rank: str,
    period: str,
    min_games: int = 1,
    min_archetype_games: int = 1,
    limit: int | None = None,
) -> dict[str, Any] | None:
    """One stored matrix filtered to the reader's thresholds, or ``None``."""

    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {SLICES} WHERE slice_key = ? AND payload_json IS NOT NULL",
            (slice_key(format_name, rank, period),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    row = dict(row)
    payload = json.loads(row["payload_json"])
    total = int(payload.get("total_games") or 0)
    source_rows = payload.get("archetypes") or []
    kept = sorted(
        (
            (position, item)
            for position, item in enumerate(source_rows)
            if int(item.get("games") or 0) >= min_archetype_games
        ),
        key=lambda pair: (-int(pair[1]["games"]), pair[1]["archetype"]),
    )
    if limit is not None:
        kept = kept[:limit]
    names = {position: item["archetype"] for position, item in kept}
    archetypes = [
        {
            **item,
            "popularity_pct": round(100 * int(item["games"]) / total, 2) if total else None,
        }
        for _, item in kept
    ]
    matchups = [
        {
            "archetype": names[row_index],
            "opponent": names[col_index],
            "winrate": winrate,
            "games": games,
        }
        for row_index, col_index, winrate, games in payload.get("cells") or []
        if row_index in names and col_index in names and games >= min_games
    ]
    return {
        "format": format_name,
        "rank": rank,
        "period": period,
        "coin": "any_player",
        "min_games": min_games,
        "min_archetype_games": min_archetype_games,
        "total_games": total,
        "checked_at": row["last_success_at"],
        "changed_at": row["last_changed_at"],
        "stale": slice_is_stale(row),
        "source_url": row["source_url"],
        "archetypes": archetypes,
        "matchups": matchups,
    }


def latest_revision() -> str | None:
    """Cache revision for public HTTP caching: the newest successful read."""

    try:
        conn = _connect()
    except Exception:  # noqa: BLE001 - no database means no revision yet
        return None
    try:
        row = conn.execute(f"SELECT max(last_success_at) FROM {SLICES}").fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        conn.close()
