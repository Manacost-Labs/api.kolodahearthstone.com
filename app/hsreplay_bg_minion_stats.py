"""HSReplay Battlegrounds minion statistics pipeline and read service.

Four HSReplay analytics queries cover every minion of a slice (MMR percentile
and time range).  Each slice is validated and stored atomically in SQLite
(``app.bg_minion_stats_store``); a slice that fails keeps serving its previous
snapshot.  The platform analytics delta mirrors the tables into PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from . import bg_minion_stats_store as store
from .bg_minion_stats_formulas import (
    ALL_COMPOSITIONS,
    MinionStatsPayloadError,
    aggregate_positions,
    build_minion_stats,
    parse_medians,
    parse_minion_list,
    parse_pct_players_remaining,
    parse_purchases,
    payload_as_of,
    public_summary,
    summarize_variant,
)
from .cards_index import _load_raw_cards, cards_by_dbfid, cards_by_id
from .db import get_db_connection, init_db
from .hsreplay_client import HsReplayQueryProcessingError, fetch_hsreplay_json
from .proxy_errors import ProxyPaymentRequiredError
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

SOURCE_ID = "hsreplay_battlegrounds_minion_stats"
ANALYTICS_BASE = "https://hsreplay.net/analytics/query"
PAGE_URL = "https://hsreplay.net/battlegrounds/minions/"
QUERIES = {
    "minion_list": "battlegrounds_minion_list",
    "positions": "battlegrounds_minion_position",
    "purchases": "battlegrounds_purchase_rates_by_turn",
    "medians": "battlegrounds_minion_stats",
}
# HSReplay answers "not supported or enabled" for TOP_5/TOP_1 on these queries.
MMR_OPTIONS = ("ALL", "TOP_50_PERCENT", "TOP_20_PERCENT")
TIME_RANGE_OPTIONS = ("LAST_7_DAYS", "CURRENT_BATTLEGROUNDS_PATCH")
DEFAULT_MMR = "TOP_50_PERCENT"
DEFAULT_TIME_RANGE = "LAST_7_DAYS"

MIN_MINIONS = 150
MAX_MINION_DROP_RATIO = 0.35
MIN_POSITION_COVERAGE = 0.80
MIN_PURCHASE_COVERAGE = 0.75
MIN_MEDIAN_COVERAGE = 0.80
MIN_POPULARITY_FILL = 0.80

CATALOG_ORIGIN = "https://api.kolodahearthstone.com"
CATALOG_TIMEOUT_SECONDS = 2.5


@dataclass(frozen=True)
class MinionStatsSlice:
    mmr_percentile: str
    time_range: str
    positions: bool

    @property
    def key(self) -> str:
        return f"{self.mmr_percentile}:{self.time_range}"


# Positions are ~24 MB per slice, so only the primary MMR carries them.
DEFAULT_SLICES: tuple[MinionStatsSlice, ...] = (
    MinionStatsSlice("TOP_50_PERCENT", "LAST_7_DAYS", True),
    MinionStatsSlice("TOP_50_PERCENT", "CURRENT_BATTLEGROUNDS_PATCH", True),
    MinionStatsSlice("TOP_20_PERCENT", "LAST_7_DAYS", False),
    MinionStatsSlice("TOP_20_PERCENT", "CURRENT_BATTLEGROUNDS_PATCH", False),
    MinionStatsSlice("ALL", "LAST_7_DAYS", False),
    MinionStatsSlice("ALL", "CURRENT_BATTLEGROUNDS_PATCH", False),
)


def normalize_mmr(value: str | None) -> str:
    normalized = str(value or DEFAULT_MMR).strip().upper()
    if normalized not in MMR_OPTIONS:
        raise ValueError(f"Unsupported mmr: {normalized}; expected one of {', '.join(MMR_OPTIONS)}")
    return normalized


def normalize_time_range(value: str | None) -> str:
    normalized = str(value or DEFAULT_TIME_RANGE).strip().upper()
    if normalized not in TIME_RANGE_OPTIONS:
        raise ValueError(
            f"Unsupported period: {normalized}; expected one of {', '.join(TIME_RANGE_OPTIONS)}"
        )
    return normalized


def retention_days() -> dict[str, int]:
    rounds = int(os.environ.get("HS_BG_MINION_STATS_ROUND_RETENTION_DAYS", "60"))
    details = int(os.environ.get("HS_BG_MINION_STATS_DETAIL_RETENTION_DAYS", "30"))
    return {
        "bg_minion_stat_rounds": max(7, rounds),
        "bg_minion_stat_positions": max(7, details),
        "bg_minion_stat_purchases": max(7, details),
        "bg_minion_stat_medians": max(7, details),
    }


def query_url(query: str, mmr_percentile: str, time_range: str) -> str:
    params = urlencode({"BattlegroundsMMRPercentile": mmr_percentile, "BattlegroundsTimeRange": time_range})
    return f"{ANALYTICS_BASE}/{QUERIES[query]}/?{params}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- refresh ------------------------------------------------------------------

Fetcher = Callable[[str, str], Any]


async def _default_fetch(url: str, cache_key: str) -> dict[str, Any]:
    return await fetch_hsreplay_json(url, source_id=SOURCE_ID, cache_key=cache_key)


def _coverage(dbf_ids: set[int], rows: list[dict[str, Any]]) -> float:
    return len({row["dbf_id"] for row in rows} & dbf_ids) / max(len(dbf_ids), 1)


def slice_quality_errors(
    *,
    slice_: MinionStatsSlice,
    minions: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    purchases: list[dict[str, Any]],
    medians: list[dict[str, Any]],
    previous_total: int | None,
) -> list[str]:
    dbf_ids = {minion["dbf_id"] for minion in minions}
    errors: list[str] = []
    if len(dbf_ids) < MIN_MINIONS:
        errors.append(f"minion list too small ({len(dbf_ids)} < {MIN_MINIONS})")
    allow_drop = os.environ.get("HS_BG_MINION_STATS_ALLOW_POOL_DROP", "").strip() == "1"
    if previous_total and not allow_drop and len(dbf_ids) < previous_total * (1 - MAX_MINION_DROP_RATIO):
        errors.append(f"minion list regressed ({len(dbf_ids)} vs previous {previous_total})")
    normal = [row for row in summaries if row["variant"] == "normal"]
    fill = sum(row["popularity"] is not None for row in normal) / max(len(normal), 1)
    if fill < MIN_POPULARITY_FILL:
        errors.append(f"normal popularity fill too low ({fill:.1%})")
    checks = [("purchases", purchases, MIN_PURCHASE_COVERAGE), ("medians", medians, MIN_MEDIAN_COVERAGE)]
    if slice_.positions:
        overall = [row for row in positions if row["composition_id"] == ALL_COMPOSITIONS]
        checks.append(("positions", overall, MIN_POSITION_COVERAGE))
    for label, rows, minimum in checks:
        coverage = _coverage(dbf_ids, rows)
        if coverage < minimum:
            errors.append(f"{label} cover too few minions ({coverage:.1%} < {minimum:.0%})")
    return errors


async def _refresh_slice(
    conn: sqlite3.Connection,
    slice_: MinionStatsSlice,
    *,
    fetch: Fetcher,
    composition_names: Mapping[int, str],
    fetched_at: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mmr_percentile": slice_.mmr_percentile,
        "time_range": slice_.time_range,
        "positions": slice_.positions,
    }
    previous = store.latest_snapshot(conn, slice_.mmr_percentile, slice_.time_range)
    queries = [name for name in QUERIES if slice_.positions or name != "positions"]
    payloads: dict[str, dict[str, Any]] = {}
    try:
        for name in queries:
            payloads[name] = await fetch(
                query_url(name, slice_.mmr_percentile, slice_.time_range),
                f"bg:minion-stats:{name}:{slice_.mmr_percentile}:{slice_.time_range}",
            )
        minions = parse_minion_list(payloads["minion_list"])
        remaining = parse_pct_players_remaining(payloads["minion_list"])
        dbf_ids = {minion["dbf_id"] for minion in minions}
        positions = aggregate_positions(payloads["positions"], dbf_ids) if slice_.positions else []
        purchases = parse_purchases(payloads["purchases"], dbf_ids)
        medians = parse_medians(payloads["medians"], dbf_ids)
        summaries = [
            {"dbf_id": minion["dbf_id"], "variant": variant, **summarize_variant(aggregates, remaining)}
            for minion in minions
            for variant, aggregates in minion["rounds"].items()
        ]
        as_of = {
            "list_as_of": payload_as_of(payloads["minion_list"]),
            "positions_as_of": payload_as_of(payloads["positions"]) if slice_.positions else None,
            "purchases_as_of": payload_as_of(payloads["purchases"]),
            "medians_as_of": payload_as_of(payloads["medians"]),
        }
    except HsReplayQueryProcessingError as exc:
        return _slice_failure(result, "upstream_processing", exc, previous)
    except ProxyPaymentRequiredError as exc:
        return _slice_failure(result, f"proxy_{exc.status_code}", exc, previous)
    except (MinionStatsPayloadError, ValueError, TypeError, KeyError) as exc:
        return _slice_failure(result, "schema_rejected", exc, previous)
    except Exception as exc:  # noqa: BLE001 - one slice must not abort the others
        return _slice_failure(result, str(SourceState.FETCH_ERROR), exc, previous)
    del payloads
    errors = slice_quality_errors(
        slice_=slice_,
        minions=minions,
        summaries=summaries,
        positions=positions,
        purchases=purchases,
        medians=medians,
        previous_total=previous.get("minions_total") if previous else None,
    )
    result["minions"] = len(minions)
    if errors:
        return {
            **result,
            "state": "quality_rejected",
            "quality_errors": errors,
            "serving_snapshot_id": _id(previous),
        }
    if (
        previous
        and as_of["list_as_of"]
        and all(previous.get(field) == value for field, value in as_of.items())
    ):
        return {**result, "state": "unchanged", "snapshot_id": previous["id"], **as_of}
    counts = store.write_snapshot(
        conn,
        snapshot={
            "source_id": SOURCE_ID,
            "mmr_percentile": slice_.mmr_percentile,
            "time_range": slice_.time_range,
            "fetched_at": fetched_at,
            **as_of,
            "minions_total": len(minions),
            "has_positions": int(slice_.positions),
            "composition_names": dict(composition_names),
        },
        minions=minions,
        pct_players_remaining=remaining,
        summaries=summaries,
        positions=positions,
        purchases=purchases,
        medians=medians,
    )
    return {**result, "state": "stored", "snapshot_id": counts.pop("snapshot_id"), "rows": counts, **as_of}


def _id(snapshot: Mapping[str, Any] | None) -> int | None:
    return int(snapshot["id"]) if snapshot else None


def _slice_failure(
    result: dict[str, Any], state: str, exc: Exception, previous: Mapping[str, Any] | None
) -> dict[str, Any]:
    return {
        **result,
        "state": state,
        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        "serving_snapshot_id": _id(previous),
    }


async def _composition_names() -> dict[int, str]:
    from .hsreplay_bg_stats import _fetch_composition_names

    return await _fetch_composition_names(SOURCE_ID)


GOOD_SLICE_STATES = frozenset({"stored", "unchanged"})


def _compact_rows(conn: sqlite3.Connection, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    cards = _card_index()
    by_dbf: dict[int, dict[str, Any]] = {}
    for row in store.summaries(conn, int(snapshot["id"])):
        entry = by_dbf.setdefault(
            row["dbf_id"],
            {"dbf_id": row["dbf_id"], "tavern_tier": row["tavern_tier"], **cards(row["dbf_id"])},
        )
        entry[row["variant"]] = {
            key: value
            for key, value in (public_summary(row) or {}).items()
            if key in {"impact", "combat_winrate", "popularity", "games_with_minion"}
        }
    return sorted(by_dbf.values(), key=lambda row: -((row.get("normal") or {}).get("popularity") or 0))


async def _refresh_unlocked(
    *, slices: tuple[MinionStatsSlice, ...], fetch: Fetcher, names: Callable[[], Any] | None
) -> dict[str, Any]:
    fetched_at = _now()
    errors: list[dict[str, Any]] = []
    composition_names: dict[int, str] = {}
    try:
        composition_names = await (names or _composition_names)()
    except Exception as exc:  # noqa: BLE001 - labels are optional
        errors.append({"stage": "composition_names", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    init_db()
    conn = get_db_connection()
    try:
        results = []
        for slice_ in slices:
            results.append(
                await _refresh_slice(
                    conn,
                    slice_,
                    fetch=fetch,
                    composition_names=composition_names,
                    fetched_at=fetched_at,
                )
            )
        pruned = store.prune_details(conn, retention_days=retention_days())
        current = {
            (row["mmr_percentile"], row["time_range"]): row for row in store.latest_snapshots(conn)
        }
        primary = current.get((DEFAULT_MMR, DEFAULT_TIME_RANGE))
        compact = _compact_rows(conn, primary) if primary else []
    finally:
        conn.close()

    good = [row for row in results if row["state"] in GOOD_SLICE_STATES]
    failed = [row for row in results if row["state"] not in GOOD_SLICE_STATES]
    serving_cached = bool(failed) and all(row.get("serving_snapshot_id") for row in failed)
    primary_ok = any(
        row["state"] in GOOD_SLICE_STATES
        and (row["mmr_percentile"], row["time_range"]) == (DEFAULT_MMR, DEFAULT_TIME_RANGE)
        for row in results
    )
    state = SourceState.OK if not failed else SourceState.PARTIAL if good else SourceState.FETCH_ERROR
    published_slices = [
        {
            "mmr_percentile": snapshot["mmr_percentile"],
            "time_range": snapshot["time_range"],
            "snapshot_id": snapshot["id"],
            "fetched_at": snapshot["fetched_at"],
            "list_as_of": snapshot["list_as_of"],
            "minions_total": snapshot["minions_total"],
            "has_positions": bool(snapshot["has_positions"]),
        }
        for snapshot in current.values()
    ]
    if primary_ok and compact:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": fetched_at,
                "http_status": 200,
                "final_url": PAGE_URL,
                "content_length": None,
                "backend": "hsreplay_json_api",
                "data": {
                    "structured": {
                        "type": "bg_minion_stats",
                        "fetched_at": fetched_at,
                        "filters": {"mmr_percentile": DEFAULT_MMR, "time_range": DEFAULT_TIME_RANGE},
                        "slices": published_slices,
                        "minions": compact,
                        "source": {"backend": "hsreplay_json_api", "queries": list(QUERIES.values())},
                    }
                },
            },
        )
    previous_dataset = load_dataset(SOURCE_ID) or {}
    status_fetched_at = fetched_at if primary_ok else previous_dataset.get("fetched_at") or fetched_at
    detail = ", ".join(f"{row['mmr_percentile']}/{row['time_range']}={row['state']}" for row in results)
    status: dict[str, Any] = {
        "source_id": SOURCE_ID,
        "site": "hsreplay",
        "category": "battlegrounds",
        "url": PAGE_URL,
        "state": SourceState.OK if not failed or serving_cached else state,
        "fetched_at": status_fetched_at,
        "http_status": 200 if good else None,
        "backend": "hsreplay_json_api",
        "rows_total": len(compact),
        "detail": f"BG minion stats slices: {detail}",
        "slices": results,
        "errors": errors,
        "serving_cached_dataset": serving_cached,
        "last_refresh_state": state,
        "last_refresh_at": fetched_at,
    }
    if failed:
        status["last_refresh_failure_class"] = failed[0]["state"]
        status["last_refresh_error"] = "; ".join(
            f"{row['mmr_percentile']}/{row['time_range']}: "
            + (row.get("error") or "; ".join(row.get("quality_errors") or []))
            for row in failed
        )[:700]
    save_status(SOURCE_ID, status)
    return {
        "ok": not failed,
        "state": state,
        "published": bool(good),
        "serving_cached_dataset": serving_cached,
        "source_id": SOURCE_ID,
        "fetched_at": fetched_at,
        "minions": len(compact),
        "slices": results,
        "pruned": pruned,
        "errors": errors,
    }


async def refresh_bg_minion_stats(
    *,
    slices: tuple[MinionStatsSlice, ...] = DEFAULT_SLICES,
    fetch: Fetcher | None = None,
    names: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Refresh every slice unless another process owns this source."""

    locks = ResourceLockSet([SOURCE_ID], metadata={"operation": "refresh_bg_minion_stats"})
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {"ok": True, "published": False, "source_id": SOURCE_ID, **exc.as_outcome()}
    try:
        from .hsreplay_client import reset_hsreplay_refresh_state

        reset_hsreplay_refresh_state()
        return await _refresh_unlocked(slices=slices, fetch=fetch or _default_fetch, names=names)
    finally:
        locks.release()


# --- read service -------------------------------------------------------------


_RU_NAMES: dict[int, str] = {}


def _ru_names() -> dict[int, str]:
    if not _RU_NAMES:
        try:
            _RU_NAMES.update(
                {int(card["dbfId"]): card.get("name") for card in _load_raw_cards("ruRU") if card.get("dbfId")}
            )
        except Exception:  # noqa: BLE001 - names are decorative
            return {}
    return _RU_NAMES


def _card_index() -> Callable[[int], dict[str, Any]]:
    en = cards_by_dbfid()
    ru = _ru_names()

    def lookup(dbf_id: int) -> dict[str, Any]:
        card = en.get(int(dbf_id)) or {}
        return {"card_id": card.get("id"), "name": card.get("name"), "name_ru": ru.get(int(dbf_id))}

    return lookup


MAX_DBF_ID = 2**31 - 1


def resolve_minion(value: str | int) -> int | None:
    """Accept a dbf id or a card id (golden card ids map to the normal minion)."""

    text = str(value).strip()
    if text.isdigit():
        dbf_id = int(text)
        if not 0 < dbf_id <= MAX_DBF_ID:
            return None
    else:
        card = cards_by_id().get(text) or next(
            (card for card_id, card in cards_by_id().items() if card_id.lower() == text.lower()), None
        )
        if not card or not card.get("dbfId"):
            return None
        dbf_id = int(card["dbfId"])
    card = cards_by_dbfid().get(dbf_id) or {}
    normal = card.get("battlegroundsNormalDbfId")
    return int(normal) if isinstance(normal, int) and normal > 0 else dbf_id


def card_payload(
    dbf_id: int,
    catalog: Mapping[str, Any] | None = None,
    *,
    lookup: Callable[[int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Card identity from HearthstoneJSON, overlaid with our catalog's names and media."""

    card = cards_by_dbfid().get(int(dbf_id)) or {}
    names = (lookup or _card_index())(dbf_id)
    races = card.get("races") or ([card["race"]] if card.get("race") else [])
    out = {
        "dbf_id": int(dbf_id),
        "card_id": names["card_id"],
        "name": names["name"],
        "name_ru": names["name_ru"],
        "tavern_tier": card.get("techLevel"),
        "minion_types": races,
        "mechanics": card.get("mechanics") or [],
        "attack": card.get("attack"),
        "health": card.get("health"),
        "golden_dbf_id": card.get("battlegroundsPremiumDbfId"),
        "images": None,
        "catalog": False,
    }
    if catalog:
        out.update(
            {
                "card_id": catalog.get("card_id") or out["card_id"],
                "name": catalog.get("name_en") or out["name"],
                "name_ru": catalog.get("name_ru") or out["name_ru"],
                "tavern_tier": catalog.get("tavern_tier") or out["tavern_tier"],
                "golden_dbf_id": catalog.get("golden_dbf") or out["golden_dbf_id"],
                "in_pool": catalog.get("in_pool"),
                "images": catalog_images(catalog),
                "catalog": True,
            }
        )
    return out


def _absolute(url: Any) -> str | None:
    if not url:
        return None
    text = str(url)
    return text if text.startswith("http") else f"{CATALOG_ORIGIN}{text if text.startswith('/') else '/' + text}"


def catalog_images(row: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "card": _absolute(row.get("framed_image") or row.get("card_image")),
        "card_golden": _absolute(row.get("golden_framed_image") or row.get("golden_image")),
        "art": _absolute(row.get("art_image")),
        "horizontal": row.get("horizontal_image_url"),
    }


async def catalog_cards(dbf_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Best-effort catalog rows; the statistics stay available without PostgreSQL."""

    if not dbf_ids:
        return {}
    try:
        from .graphql_api.repository import get_graphql_repository

        repository = get_graphql_repository()
        lookup = getattr(repository, "battleground_cards_by_dbf", None)
        if lookup is None:
            return {}
        return await asyncio.wait_for(lookup(sorted(set(dbf_ids))), timeout=CATALOG_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - optional enrichment
        return {}


def _read(callback: Callable[[sqlite3.Connection], Any]) -> Any:
    init_db()
    conn = get_db_connection()
    try:
        return callback(conn)
    finally:
        conn.close()


SORT_FIELDS = ("popularity", "impact", "combat_winrate", "games_with_minion", "tavern_tier")


def minion_stats_rows(rows: list[dict[str, Any]], catalog: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """List rows with card identity; one card index for the whole page."""

    lookup = _card_index()
    out = []
    for row in rows:
        card = card_payload(row["dbf_id"], catalog.get(row["dbf_id"]), lookup=lookup)
        out.append(
            {
                "dbf_id": row["dbf_id"],
                "card_id": card["card_id"],
                "name": card["name"],
                "name_ru": card["name_ru"],
                "tavern_tier": row["tavern_tier"] or card["tavern_tier"],
                "images": card["images"],
                "normal": row.get("normal"),
                "golden": row.get("golden"),
            }
        )
    return out


def list_minion_stats(
    *,
    mmr: str | None = None,
    period: str | None = None,
    tavern_tier: int | None = None,
    sort: str = "popularity",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    mmr_percentile, time_range = normalize_mmr(mmr), normalize_time_range(period)
    if sort not in SORT_FIELDS:
        raise ValueError(f"Unsupported sort: {sort}; expected one of {', '.join(SORT_FIELDS)}")

    def run(conn: sqlite3.Connection) -> dict[str, Any]:
        snapshot = store.latest_snapshot(conn, mmr_percentile, time_range)
        if snapshot is None:
            return {"snapshot": None, "total": 0, "rows": []}
        grouped: dict[int, dict[str, Any]] = {}
        for row in store.summaries(conn, snapshot["id"]):
            entry = grouped.setdefault(row["dbf_id"], {"dbf_id": row["dbf_id"], "tavern_tier": row["tavern_tier"]})
            entry[row["variant"]] = public_summary(row)
        rows = [row for row in grouped.values() if tavern_tier is None or row["tavern_tier"] == tavern_tier]
        if sort == "tavern_tier":
            rows.sort(key=lambda row: (row["tavern_tier"] or 99, row["dbf_id"]))
        else:
            rows.sort(
                key=lambda row: (
                    (row.get("normal") or {}).get(sort) is None,
                    -((row.get("normal") or {}).get(sort) or 0),
                    row["dbf_id"],
                )
            )
        return {"snapshot": snapshot, "total": len(rows), "rows": rows[offset : offset + limit]}

    return _read(run)


def get_minion_stats(
    dbf_id: int,
    *,
    mmr: str | None = None,
    period: str | None = None,
    composition_id: int = ALL_COMPOSITIONS,
    snapshot_id: int | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    mmr_percentile, time_range = normalize_mmr(mmr), normalize_time_range(period)

    def run(conn: sqlite3.Connection) -> dict[str, Any] | None:
        snapshot = (
            store.snapshot_by_id(conn, snapshot_id)
            if snapshot_id is not None
            else store.latest_snapshot(conn, mmr_percentile, time_range)
        )
        if snapshot is None or (snapshot["mmr_percentile"], snapshot["time_range"]) != (mmr_percentile, time_range):
            return None
        rows = store.minion_detail_rows(conn, snapshot["id"], dbf_id, composition_id)
        if not rows["summaries"] or (snapshot_id is not None and not rows["rounds"]):
            # Old snapshots keep only totals after retention; use /stats/history.
            return None
        return build_minion_stats(
            card=card_payload(dbf_id, catalog),
            snapshot=snapshot,
            composition_id=composition_id,
            **rows,
        )

    return _read(run)


def get_minion_stats_history(
    dbf_id: int, *, mmr: str | None = None, period: str | None = None, limit: int = 90
) -> dict[str, Any]:
    mmr_percentile, time_range = normalize_mmr(mmr), normalize_time_range(period)
    rows = _read(lambda conn: store.summary_history(conn, dbf_id, mmr_percentile, time_range, limit=limit))
    points: dict[int, dict[str, Any]] = {}
    for row in rows:
        point = points.setdefault(
            row["snapshot_id"],
            {
                "snapshot_id": row["snapshot_id"],
                "fetched_at": row["fetched_at"],
                "as_of": row["list_as_of"],
                "tavern_tier": row["tavern_tier"],
                "normal": None,
                "golden": None,
            },
        )
        point[row["variant"]] = {
            key: value
            for key, value in (public_summary(row) or {}).items()
            if key in {"impact", "combat_winrate", "popularity", "games_with_minion", "wins", "losses"}
        }
    return {
        "dbf_id": dbf_id,
        "mmr_percentile": mmr_percentile,
        "time_range": time_range,
        "points": [points[key] for key in sorted(points)],
    }


def available_slices() -> list[dict[str, Any]]:
    return _read(
        lambda conn: [
            {
                "mmr_percentile": row["mmr_percentile"],
                "time_range": row["time_range"],
                "snapshot_id": row["id"],
                "fetched_at": row["fetched_at"],
                "list_as_of": row["list_as_of"],
                "has_positions": bool(row["has_positions"]),
                "minions_total": row["minions_total"],
            }
            for row in store.latest_snapshots(conn)
        ]
    )


def latest_revision() -> str | None:
    """Cache revision for public HTTP caching: the newest snapshot id."""

    conn = get_db_connection()
    try:
        row = conn.execute(f"SELECT max(id) FROM {store.SNAPSHOTS}").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return str(row[0]) if row and row[0] is not None else None


def slices_from_cli(values: list[str] | None) -> tuple[MinionStatsSlice, ...]:
    """Map ``MMR:TIME_RANGE`` CLI values onto configured slices (diagnostic runs)."""

    if not values:
        return DEFAULT_SLICES
    configured = {slice_.key: slice_ for slice_ in DEFAULT_SLICES}
    out: list[MinionStatsSlice] = []
    for value in values:
        mmr, _, time_range = str(value).partition(":")
        key = f"{normalize_mmr(mmr)}:{normalize_time_range(time_range)}"
        out.append(configured.get(key) or MinionStatsSlice(*key.split(":"), positions=False))
    return tuple(out)


def assemble_mirrored_stats(
    rows: Mapping[str, Any], *, dbf_id: int, composition_id: int, catalog: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Build the same payload as REST from PostgreSQL ``analytics`` rows (GraphQL)."""

    if not rows or not rows.get("summaries"):
        return None
    snapshot = dict(rows["snapshot"])
    for field, target in (
        ("pct_players_remaining_json", "pct_players_remaining"),
        ("composition_names_json", "composition_names"),
    ):
        value = snapshot.pop(field, None)
        snapshot[target] = json.loads(value) if isinstance(value, str) else value or {}
    positions = []
    for row in rows.get("positions") or []:
        row = dict(row)
        for field in ("position_occurrences", "position_sum_placement"):
            value = row.pop(f"{field}_json")
            row[field] = json.loads(value) if isinstance(value, str) else list(value)
        positions.append(row)
    return build_minion_stats(
        card=card_payload(dbf_id, catalog),
        snapshot=snapshot,
        summaries=rows["summaries"],
        rounds=rows.get("rounds") or [],
        positions=positions,
        position_compositions=rows.get("position_compositions") or [],
        purchases=rows.get("purchases") or [],
        medians=rows.get("medians") or [],
        composition_id=composition_id,
    )
