"""Official Blizzard Hearthstone leaderboards: collection and read model.

The public community endpoint ``/en-us/api/community/leaderboardsData`` needs
no key and serves 25 rows a page for one region, mode and season. Every
response also carries ``seasonMetaData``: the seasons each region and mode
still serves, newest first. The newest season of a pair is *live*; older ones
are *history*.

Two scopes share one lock so Blizzard never sees two runs at once:

* ``live`` re-reads every live board in full (about 4,300 pages, 18 minutes at
  four requests a second) and replaces that UTC day's snapshot;
* ``history`` reads past seasons once, newest first, within a page budget per
  run. A season that stopped being live gets its final read before the
  backfill continues.

Requests are paced globally (``requests_per_second``) with a small concurrency
cap, retried with backoff on 429/5xx/timeouts, and a 429 halves the pace for
the rest of the run. Responses are requested gzip-compressed (17 KB instead of
300 KB a page). A board is written only when every page of it was read, so a
snapshot is never a partial table. Boards larger than ``max_pages`` (old
Battlegrounds seasons list up to 1.5 million players) keep their top
``max_pages`` pages and are marked ``truncated``.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from . import blizzard_leaderboards_store as store
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

SOURCE_ID = "blizzard_leaderboards"
API_URL = "https://hearthstone.blizzard.com/en-us/api/community/leaderboardsData"
PUBLIC_URL = "https://hearthstone.blizzard.com/en-us/community/leaderboards"
REGIONS = ("EU", "US", "AP")
MODES = (
    "standard",
    "wild",
    "battlegrounds",
    "battlegroundsduo",
    "arena",
    "undergroundarena",
    "twist",
)
# Modes whose rows carry a rating (Battlegrounds MMR, Arena average wins or score).
RATED_MODES = frozenset(
    {"battlegrounds", "battlegroundsduo", "arena", "undergroundarena"}
)
HISTOGRAM_BUCKETS: dict[str, float] = {
    "battlegrounds": 500,
    "battlegroundsduo": 500,
    "undergroundarena": 250,
    "arena": 0.5,
}
SCOPES = ("live", "history", "all")
PAGE_SIZE = 25
STALE_HOURS = 12
USER_AGENT = "HearthPulse-leaderboards/1.0 (+https://hearthpulse.net)"
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class LeaderboardError(RuntimeError):
    """Blizzard answered, but not with a usable leaderboard page."""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_list(name: str, allowed: Sequence[str]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return tuple(allowed)
    wanted = {item.strip() for item in raw.split(",") if item.strip()}
    return tuple(item for item in allowed if item in wanted) or tuple(allowed)


@dataclass(frozen=True)
class Settings:
    regions: tuple[str, ...] = REGIONS
    modes: tuple[str, ...] = MODES
    concurrency: int = 3
    requests_per_second: float = 4.0
    max_pages: int = 2000
    history_pages: int = 3000
    live_seconds: int = 50 * 60
    history_seconds: int = 20 * 60
    timeout_seconds: float = 30.0
    attempts: int = 4
    backoff_seconds: float = 2.0
    stop_after_failures: int = 12

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            regions=_env_list("HS_LEADERBOARDS_REGIONS", REGIONS),
            modes=_env_list("HS_LEADERBOARDS_MODES", MODES),
            concurrency=_env_int("HS_LEADERBOARDS_CONCURRENCY", 3, 1, 8),
            requests_per_second=_env_float("HS_LEADERBOARDS_RPS", 4.0, 0.2, 10.0),
            max_pages=_env_int("HS_LEADERBOARDS_MAX_PAGES", 2000, 1, 100_000),
            history_pages=_env_int("HS_LEADERBOARDS_HISTORY_PAGES", 3000, 0, 1_000_000),
            live_seconds=_env_int(
                "HS_LEADERBOARDS_LIVE_SECONDS", 50 * 60, 60, 6 * 3600
            ),
            history_seconds=_env_int(
                "HS_LEADERBOARDS_HISTORY_SECONDS", 20 * 60, 60, 6 * 3600
            ),
        )


class Pacer:
    """Space request starts globally; a 429 doubles the spacing for the run."""

    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / requests_per_second
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)

    def slow_down(self) -> None:
        self.interval = min(self.interval * 2, 4.0)


@dataclass
class Client:
    settings: Settings
    http: httpx.AsyncClient
    pacer: Pacer
    semaphore: asyncio.Semaphore
    requests: int = 0
    retries: int = 0
    throttled: int = 0
    consecutive_failures: int = 0

    async def page(
        self, region: str, mode: str, season_id: int | None, page: int
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"region": region, "leaderboardId": mode, "page": page}
        if season_id is not None:
            params["seasonId"] = season_id
        error: Exception | None = None
        for attempt in range(self.settings.attempts):
            if attempt:
                self.retries += 1
                await asyncio.sleep(self.settings.backoff_seconds * 2 ** (attempt - 1))
            async with self.semaphore:
                await self.pacer.wait()
                self.requests += 1
                try:
                    response = await self.http.get(API_URL, params=params)
                except httpx.HTTPError as exc:
                    error = exc
                    continue
            if response.status_code in _RETRY_STATUS:
                error = LeaderboardError(f"HTTP {response.status_code}")
                if response.status_code == 429:
                    self.throttled += 1
                    self.pacer.slow_down()
                    retry_after = response.headers.get("retry-after", "")
                    if retry_after.isdigit():
                        await asyncio.sleep(min(int(retry_after), 60))
                continue
            if response.status_code != 200:
                self.consecutive_failures += 1
                raise LeaderboardError(f"HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                error = LeaderboardError(f"invalid JSON: {exc}")
                continue
            if not isinstance(payload, dict):
                error = LeaderboardError("response is not an object")
                continue
            self.consecutive_failures = 0
            return payload
        self.consecutive_failures += 1
        raise LeaderboardError(f"{region}/{mode}/{season_id} page {page}: {error}")


def new_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> Client:
    http = httpx.AsyncClient(
        transport=transport,
        timeout=settings.timeout_seconds,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        follow_redirects=False,
    )
    return Client(
        settings=settings,
        http=http,
        pacer=Pacer(settings.requests_per_second),
        semaphore=asyncio.Semaphore(settings.concurrency),
    )


# --- parsing ------------------------------------------------------------------


def parse_metadata(
    payload: Mapping[str, Any],
    *,
    regions: Sequence[str] = REGIONS,
    modes: Sequence[str] = MODES,
) -> tuple[dict[tuple[str, str], list[int]], dict[tuple[str, int], str]]:
    """Seasons served per (region, mode) and season start dates per (mode, season)."""

    meta = payload.get("seasonMetaData")
    if not isinstance(meta, dict):
        raise LeaderboardError("seasonMetaData is missing")
    seasons: dict[tuple[str, str], list[int]] = {}
    for region in regions:
        for mode in modes:
            entry = (meta.get(region) or {}).get(mode) or {}
            ids = sorted(
                {
                    int(item["season_id"])
                    for item in entry.get("seasons") or []
                    if isinstance(item, dict) and isinstance(item.get("season_id"), int)
                }
            )
            if ids:
                seasons[(region, mode)] = ids
    if not seasons:
        raise LeaderboardError("seasonMetaData lists no season")
    starts: dict[tuple[str, int], str] = {}
    display = payload.get("displayMetaData")
    if isinstance(display, dict):
        for mode in modes:
            dated = (display.get(mode) or {}).get("seasonsWithStartDate") or {}
            if not isinstance(dated, dict):
                continue
            for season, millis in dated.items():
                if str(season).isdigit() and isinstance(millis, (int, float)):
                    starts[(mode, int(season))] = (
                        datetime.fromtimestamp(millis / 1000, UTC).date().isoformat()
                    )
    return seasons, starts


def parse_page(
    payload: Mapping[str, Any],
) -> tuple[int, int, list[tuple[int, str, float | None]]]:
    board = payload.get("leaderboard")
    if not isinstance(board, dict):
        raise LeaderboardError("leaderboard is missing")
    pagination = board.get("pagination") or {}
    try:
        total_size = int(pagination.get("totalSize") or 0)
        total_pages = int(pagination.get("totalPages") or 0)
    except (TypeError, ValueError) as exc:
        raise LeaderboardError("pagination is not numeric") from exc
    raw_rows = board.get("rows")
    if not isinstance(raw_rows, list):
        raise LeaderboardError("rows are missing")
    rows: list[tuple[int, str, float | None]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        rank, name, rating = item.get("rank"), item.get("accountid"), item.get("rating")
        if not isinstance(rank, int) or rank < 1 or not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue
        if isinstance(rating, bool) or not isinstance(rating, (int, float)):
            rating = None
        rows.append((rank, name[:64], float(rating) if rating is not None else None))
    return total_size, total_pages, rows


@dataclass
class BoardRead:
    total_size: int
    total_pages: int
    pages_read: int
    rows: list[tuple[int, str, float | None]] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.total_pages > self.pages_read


async def read_board(
    client: Client, board: Mapping[str, Any], max_pages: int
) -> BoardRead:
    region, mode, season = (
        str(board["region"]),
        str(board["mode"]),
        int(board["season_id"]),
    )
    total_size, total_pages, rows = parse_page(
        await client.page(region, mode, season, 1)
    )
    last = min(total_pages, max_pages)
    pages: list[list[tuple[int, str, float | None]]] = [rows]
    if last > 1:

        async def one(number: int) -> list[tuple[int, str, float | None]]:
            return parse_page(await client.page(region, mode, season, number))[2]

        tasks = [asyncio.create_task(one(number)) for number in range(2, last + 1)]
        try:
            pages.extend(await asyncio.gather(*tasks))
        except BaseException:
            # One failed page fails the board: stop its other requests now.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    by_rank: dict[int, tuple[int, str, float | None]] = {}
    for page_rows in pages:
        for row in page_rows:
            by_rank.setdefault(row[0], row)
    return BoardRead(
        total_size=total_size,
        total_pages=total_pages,
        pages_read=max(last, 1) if total_pages else 0,
        rows=[by_rank[rank] for rank in sorted(by_rank)],
    )


# --- refresh --------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


async def refresh_blizzard_leaderboards(
    *,
    scope: str = "live",
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Any = None,
    max_boards: int | None = None,
) -> dict[str, Any]:
    """Read live boards or the history backfill unless another run owns the lock."""

    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    locks = ResourceLockSet(
        [SOURCE_ID], metadata={"operation": f"refresh_{SOURCE_ID}_{scope}"}
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
            scope=scope,
            settings=settings or Settings.from_env(),
            transport=transport,
            clock=clock or (lambda: datetime.now(UTC)),
            max_boards=max_boards,
        )
    finally:
        locks.release()


async def _refresh_unlocked(
    *,
    scope: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
    clock: Any,
    max_boards: int | None,
) -> dict[str, Any]:
    started_at = _iso(clock())
    started = time.monotonic()
    conn = store.connect()
    run_id = store.start_run(conn, scope, started_at)
    client = new_client(settings, transport)
    counts = {"live_ok": 0, "history_ok": 0, "failed": 0, "rows": 0, "pages": 0}
    failures: list[dict[str, Any]] = []
    stop_reason: str | None = None
    try:
        metadata = await client.page(settings.regions[0], settings.modes[0], None, 1)
        seasons, starts = parse_metadata(
            metadata, regions=settings.regions, modes=settings.modes
        )
        store.sync_boards(conn, seasons, starts, started_at)
        plan: list[tuple[str, dict[str, Any]]] = []
        if scope in ("live", "all"):
            plan += [
                ("live", board)
                for board in store.live_boards(conn, settings.regions, settings.modes)
            ]
        if scope in ("history", "all"):
            plan += [
                ("history", board)
                for board in store.history_boards(
                    conn, settings.regions, settings.modes
                )
            ]
        if max_boards is not None:
            plan = plan[:max_boards]
        history_pages = 0
        for kind, board in plan:
            budget = (
                settings.live_seconds if kind == "live" else settings.history_seconds
            )
            if scope == "all":
                budget = settings.live_seconds + settings.history_seconds
            if time.monotonic() - started > budget:
                stop_reason = "time_budget"
                break
            if kind == "history" and history_pages >= settings.history_pages:
                stop_reason = "page_budget"
                break
            if client.consecutive_failures >= settings.stop_after_failures:
                stop_reason = "upstream_failing"
                break
            requests_before = client.requests
            label = f"{board['region']}/{board['mode']}/{board['season_id']}"
            try:
                read = await read_board(client, board, settings.max_pages)
            except (LeaderboardError, httpx.HTTPError) as exc:
                counts["failed"] += 1
                failures.append({"board": label, "error": str(exc)[:300]})
                store.record_board_failure(
                    conn, int(board["id"]), str(exc), _iso(clock())
                )
                continue
            finally:
                if kind == "history":
                    history_pages += client.requests - requests_before
            captured = clock()
            store.write_snapshot(
                conn,
                board,
                day=captured.astimezone(UTC).date().isoformat(),
                captured_at=_iso(captured),
                total_size=read.total_size,
                total_pages=read.total_pages,
                rows=read.rows,
                truncated=read.truncated,
                final=kind == "history",
            )
            counts[f"{kind}_ok"] += 1
            counts["rows"] += len(read.rows)
            counts["pages"] += read.pages_read
        planned = len(plan)
    except (LeaderboardError, httpx.HTTPError) as exc:
        planned = 0
        stop_reason = "metadata_failed"
        failures.append({"board": "metadata", "error": str(exc)[:300]})
    finally:
        await client.http.aclose()

    succeeded = counts["live_ok"] + counts["history_ok"]
    finished_at = _iso(clock())
    if stop_reason == "metadata_failed" or (counts["failed"] and not succeeded):
        state = SourceState.FETCH_ERROR
    elif counts["failed"]:
        state = SourceState.PARTIAL
    else:
        state = SourceState.OK
    summary = {
        "scope": scope,
        "planned_boards": planned,
        **counts,
        "requests": client.requests,
        "retries": client.retries,
        "throttled": client.throttled,
        "stop_reason": stop_reason,
        "failures": failures[:20],
        "seconds": round(time.monotonic() - started, 1),
    }
    store.finish_run(
        conn, run_id, state=str(state), finished_at=finished_at, summary=summary
    )
    report = store.coverage(conn, runs=0)
    conn.close()

    published = stop_reason != "metadata_failed" and (
        succeeded > 0 or not counts["failed"]
    )
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": finished_at,
                "http_status": 200,
                "final_url": PUBLIC_URL,
                "backend": "blizzard_leaderboards_store",
                "data": {
                    "structured": {
                        "type": SOURCE_ID,
                        "fetched_at": finished_at,
                        "boards": report["boards"],
                        "snapshots": report["snapshots"],
                        "stored_rows": report["stored_rows"],
                        "last_run": {**summary, "started_at": started_at},
                    }
                },
            },
        )
    previous = load_dataset(SOURCE_ID) or {}
    save_status(
        SOURCE_ID,
        {
            "source_id": SOURCE_ID,
            "site": "blizzard",
            "category": "leaderboards",
            "url": PUBLIC_URL,
            "state": str(SourceState.OK if published else state),
            "fetched_at": finished_at
            if published
            else previous.get("fetched_at") or finished_at,
            "http_status": 200 if succeeded else None,
            "backend": "blizzard_community_api",
            "rows_total": report["stored_rows"],
            "detail": (
                f"Blizzard leaderboards {scope}: {succeeded}/{planned} boards ok, "
                f"{counts['failed']} failed, {client.requests} requests, "
                f"stop={stop_reason or 'done'}"
            ),
            "serving_cached_dataset": not published and bool(previous),
            "last_refresh_state": str(state),
            "last_refresh_at": finished_at,
        },
    )
    return {
        "ok": state in (SourceState.OK, SourceState.PARTIAL) and published,
        "state": str(state),
        "published": published,
        "source_id": SOURCE_ID,
        "fetched_at": finished_at,
        "errors": [f"{item['board']}: {item['error']}" for item in failures],
        **summary,
    }


# --- read model -------------------------------------------------------------------


def _read(callback: Any) -> Any:
    if not store.db_path().exists():
        return None
    conn = store.connect()
    try:
        return callback(conn)
    finally:
        conn.close()


def latest_revision() -> str | None:
    return _read(store.latest_revision)


def _is_stale(captured_at: str | None, board: Mapping[str, Any]) -> bool:
    if not board.get("is_latest") or not captured_at:
        return False
    moment = datetime.fromisoformat(captured_at)
    return datetime.now(UTC) - moment > timedelta(hours=STALE_HOURS)


def _public_board(board: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "region": board["region"],
        "mode": board["mode"],
        "season_id": board["season_id"],
        "season_start": board["season_start"],
        "is_latest": bool(board["is_latest"]),
        "final": bool(board["final"]),
        "rated": board["mode"] in RATED_MODES,
    }


def _public_snapshot(
    snapshot: Mapping[str, Any], board: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "day": snapshot["day"],
        "captured_at": snapshot["captured_at"],
        "total_size": snapshot["total_size"],
        "stored_rows": snapshot["stored_rows"],
        "truncated": bool(snapshot["truncated"]),
        "final": bool(snapshot["final"]),
        "stale": _is_stale(snapshot["captured_at"], board),
    }


def get_catalog() -> dict[str, Any]:
    rows = _read(store.catalog) or []
    modes: dict[str, dict[str, Any]] = {}
    for row in rows:
        mode = modes.setdefault(
            row["mode"],
            {"mode": row["mode"], "rated": row["mode"] in RATED_MODES, "regions": {}},
        )
        region = mode["regions"].setdefault(
            row["region"], {"latest_season": None, "seasons": []}
        )
        if row["is_latest"]:
            region["latest_season"] = row["season_id"]
        region["seasons"].append(
            {
                "season_id": row["season_id"],
                "season_start": row["season_start"],
                "is_latest": bool(row["is_latest"]),
                "final": bool(row["final"]),
                "has_data": row["day"] is not None,
                "day": row["day"],
                "total_size": row["total_size"],
                "truncated": bool(row["truncated"]) if row["day"] else False,
            }
        )
    ordered = [modes[mode] for mode in MODES if mode in modes]
    return {"regions": list(REGIONS), "modes": ordered}


def get_table(
    *,
    region: str,
    mode: str,
    season_id: int | None,
    day: str | None,
    query: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any] | None:
    def read(conn: Any) -> dict[str, Any] | None:
        board = store.find_board(conn, region, mode, season_id)
        if not board:
            return None
        snapshot = store.find_snapshot(conn, board, day)
        if not snapshot:
            return None
        rows, total = store.table_rows(
            conn, snapshot, query=query, limit=limit, offset=offset
        )
        return {
            "board": _public_board(board),
            "snapshot": _public_snapshot(snapshot, board),
            "days": store.snapshot_days(conn, int(board["id"])),
            "query": query,
            "total": total,
            "rows": rows,
        }

    return _read(read)


def get_stats(
    *, region: str, mode: str, season_id: int | None, bucket: float | None
) -> dict[str, Any] | None:
    def read(conn: Any) -> dict[str, Any] | None:
        board = store.find_board(conn, region, mode, season_id)
        if not board:
            return None
        snapshot = store.find_snapshot(conn, board, None)
        if not snapshot:
            return None
        rated = mode in RATED_MODES
        width = bucket or HISTOGRAM_BUCKETS.get(mode)
        thresholds = [
            {
                "rank": rank,
                "reached": int(snapshot["total_size"]) >= rank,
                "rating": snapshot[f"rating_at_{rank}"],
            }
            for rank in store.THRESHOLD_RANKS
            if rank > 1
        ]
        return {
            "board": _public_board(board),
            "snapshot": _public_snapshot(snapshot, board),
            "top_rating": snapshot["rating_at_1"],
            "thresholds": thresholds,
            "rating": store.rating_summary(conn, int(snapshot["id"]))
            if rated
            else None,
            "histogram": store.rating_histogram(conn, int(snapshot["id"]), width)
            if rated and width
            else None,
            "histogram_bucket": width if rated else None,
            "series": store.snapshot_series(conn, int(board["id"])),
        }

    return _read(read)


def search_players(
    *, query: str, region: str | None, limit: int
) -> list[dict[str, Any]]:
    return (
        _read(
            lambda conn: store.search_players(conn, query, region=region, limit=limit)
        )
        or []
    )


def get_player(*, region: str, name: str, mode: str | None) -> dict[str, Any] | None:
    def read(conn: Any) -> dict[str, Any] | None:
        player = store.find_player(conn, region, name)
        if not player:
            return None
        seasons: dict[int, dict[str, Any]] = {}
        shared_name = False
        seen: set[int] = set()
        for entry in store.player_entries(conn, int(player["id"]), mode=mode):
            snapshot_id = int(entry["snapshot_id"])
            if snapshot_id in seen:
                shared_name = True
            seen.add(snapshot_id)
            item = seasons.setdefault(
                int(entry["board_id"]),
                {
                    "mode": entry["mode"],
                    "season_id": entry["season_id"],
                    "season_start": entry["season_start"],
                    "is_latest": bool(entry["is_latest"]),
                    "final": bool(entry["board_final"]),
                    "rated": entry["mode"] in RATED_MODES,
                    "best_rank": None,
                    "best_day": None,
                    "last_rank": None,
                    "last_rating": None,
                    "last_day": None,
                    "in_latest_snapshot": False,
                    "days": 0,
                    "series": [],
                },
            )
            if item["best_rank"] is None or entry["rank"] < item["best_rank"]:
                item["best_rank"], item["best_day"] = entry["rank"], entry["day"]
            if not item["series"] or item["series"][-1]["day"] != entry["day"]:
                item["days"] += 1
                item["series"].append(
                    {
                        "day": entry["day"],
                        "rank": entry["rank"],
                        "rating": entry["rating"],
                        "total_size": entry["total_size"],
                        "final": bool(entry["final"]),
                    }
                )
                item["last_rank"], item["last_rating"] = entry["rank"], entry["rating"]
                item["last_day"] = entry["day"]
            if snapshot_id == entry["latest_snapshot_id"]:
                item["in_latest_snapshot"] = True
        ordered = sorted(
            seasons.values(),
            key=lambda item: (
                MODES.index(item["mode"]) if item["mode"] in MODES else 99,
                -item["season_id"],
            ),
        )
        return {
            "player": {"region": player["region"], "name": player["name"]},
            "shared_name": shared_name,
            "seasons": ordered,
        }

    return _read(read)


def get_coverage(runs: int) -> dict[str, Any]:
    return _read(lambda conn: store.coverage(conn, runs)) or {
        "boards": [],
        "snapshots": 0,
        "stored_rows": 0,
        "runs": [],
    }
