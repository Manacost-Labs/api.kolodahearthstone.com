from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Response

from .models import (
    ApiMeta,
    BgHeroRow,
    BgMinionRow,
    BgMinionStatsDetail,
    BgMinionStatsHistory,
    BgMinionStatsRow,
    BgMinionStatsSlice,
    Envelope,
    freshest_timestamp,
    timestamp_is_stale,
)

MINION_STATS_SOURCE_ID = "hsreplay_battlegrounds_minion_stats"
MMR_QUERY = Query(
    "TOP_50_PERCENT",
    description="HSReplay MMR slice: TOP_50_PERCENT, TOP_20_PERCENT or ALL.",
)
PERIOD_QUERY = Query(
    "LAST_7_DAYS",
    description="HSReplay time range: LAST_7_DAYS or CURRENT_BATTLEGROUNDS_PATCH.",
)


router = APIRouter(
    prefix="/v1/bg",
    tags=["v1-battlegrounds"],
    deprecated=True,
)
canonical_router = APIRouter(
    prefix="/v1/battlegrounds",
    tags=["v1-battlegrounds"],
)


@router.get(
    "/heroes",
    response_model=Envelope[list[BgHeroRow]],
    response_model_exclude_none=True,
)
def heroes(
    mode: str = Query("solo", pattern="^(solo|duos)$"),
    q: str | None = Query(None, min_length=1, max_length=120),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[BgHeroRow]]:
    from ..hsreplay_bg_hero_details import list_bg_heroes

    payload = list_bg_heroes(mode=mode, q=q)
    all_rows = list(payload.get("heroes") or [])
    rows = all_rows[offset : offset + limit]
    fetched_at = payload.get("fetched_at") or freshest_timestamp(rows, "fetched_at")
    return Envelope(
        data=[BgHeroRow.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="hsreplay_battlegrounds_hero_details",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=int(payload.get("count") or len(all_rows)),
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/minions",
    response_model=Envelope[list[BgMinionRow]],
    response_model_exclude_none=True,
)
def minions(
    q: str | None = Query(None, min_length=1, max_length=120),
    tavern_tier: int | None = Query(None, ge=1, le=7),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[BgMinionRow]]:
    from ..hsreplay_bg_minions_db import list_minion_snapshots

    payload = list_minion_snapshots(
        q=q,
        tavern_tier=tavern_tier,
        limit=limit,
        offset=offset,
    )
    rows = list(payload.get("minions") or [])
    fetched_at = freshest_timestamp(rows, "fetched_at")
    return Envelope(
        data=[BgMinionRow.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="hsreplay_battlegrounds_minions",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=int(payload.get("total") or 0),
            limit=limit,
            offset=offset,
        ),
    )


canonical_router.add_api_route(
    "/heroes",
    heroes,
    methods=["GET"],
    response_model=Envelope[list[BgHeroRow]],
    response_model_exclude_none=True,
)
canonical_router.add_api_route(
    "/minions",
    minions,
    methods=["GET"],
    response_model=Envelope[list[BgMinionRow]],
    response_model_exclude_none=True,
)


def _stats_meta(fetched_at: str | None, *, count: int, limit: int | None = None, offset: int | None = None) -> ApiMeta:
    return ApiMeta(
        source_id=MINION_STATS_SOURCE_ID,
        fetched_at=fetched_at,
        stale=timestamp_is_stale(fetched_at, max_age_hours=48),
        count=count,
        limit=limit,
        offset=offset,
    )


def _mark_degraded(response: Response) -> None:
    """Catalog enrichment failed: never let the revision ETag pin this body."""

    from ..public_cache import DEGRADED_RESPONSE_HEADER

    response.headers[DEGRADED_RESPONSE_HEADER] = "1"


async def _resolve_minion(value: str) -> int:
    from ..hsreplay_bg_minion_stats import resolve_minion

    dbf_id = await asyncio.to_thread(resolve_minion, value)
    if dbf_id is None:
        raise HTTPException(status_code=404, detail=f"Unknown Battlegrounds minion: {value}")
    return dbf_id


@canonical_router.get(
    "/minions/stats",
    response_model=Envelope[list[BgMinionStatsRow]],
    summary="Full HSReplay statistics summary for every Battlegrounds minion",
)
async def minion_stats_list(
    response: Response,
    mmr: str = MMR_QUERY,
    period: str = PERIOD_QUERY,
    tavern_tier: int | None = Query(None, ge=1, le=7),
    sort: str = Query("popularity", pattern="^(popularity|impact|combat_winrate|games_with_minion|tavern_tier)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[BgMinionStatsRow]]:
    from ..hsreplay_bg_minion_stats import catalog_cards, list_minion_stats, minion_stats_rows

    try:
        payload = await asyncio.to_thread(
            list_minion_stats,
            mmr=mmr,
            period=period,
            tavern_tier=tavern_tier,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows: list[dict[str, Any]] = payload["rows"]
    catalog = await catalog_cards([row["dbf_id"] for row in rows])
    if rows and not catalog:
        _mark_degraded(response)
    data = [
        BgMinionStatsRow.model_validate(row)
        for row in await asyncio.to_thread(minion_stats_rows, rows, catalog)
    ]
    snapshot = payload["snapshot"] or {}
    return Envelope(
        data=data,
        meta=_stats_meta(snapshot.get("fetched_at"), count=payload["total"], limit=limit, offset=offset),
    )


@canonical_router.get(
    "/minions/stats/slices",
    response_model=Envelope[list[BgMinionStatsSlice]],
    summary="Stored MMR/time-range slices of minion statistics and their latest snapshot",
)
async def minion_stats_slices() -> Envelope[list[BgMinionStatsSlice]]:
    from ..hsreplay_bg_minion_stats import available_slices

    rows = await asyncio.to_thread(available_slices)
    return Envelope(
        data=[BgMinionStatsSlice.model_validate(row) for row in rows],
        meta=_stats_meta(freshest_timestamp(rows, "fetched_at"), count=len(rows)),
    )


@canonical_router.get(
    "/minions/{minion}/stats",
    response_model=Envelope[BgMinionStatsDetail],
    summary="Full HSReplay statistics of one Battlegrounds minion (normal and golden)",
)
async def minion_stats_detail(
    response: Response,
    minion: str = Path(..., min_length=1, max_length=40, description="dbf id or card id, e.g. 104551 or BG28_300"),
    mmr: str = MMR_QUERY,
    period: str = PERIOD_QUERY,
    composition: int = Query(
        0,
        ge=0,
        le=10_000,
        description="Board positions for one composition id; 0 sums every composition like HSReplay.",
    ),
    snapshot_id: int | None = Query(None, ge=1, description="Read an older stored snapshot of the same slice."),
) -> Envelope[BgMinionStatsDetail]:
    from ..hsreplay_bg_minion_stats import catalog_cards, get_minion_stats

    dbf_id = await _resolve_minion(minion)
    catalog = await catalog_cards([dbf_id])
    if not catalog:
        _mark_degraded(response)
    try:
        payload = await asyncio.to_thread(
            get_minion_stats,
            dbf_id,
            mmr=mmr,
            period=period,
            composition_id=composition,
            snapshot_id=snapshot_id,
            catalog=catalog.get(dbf_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="No statistics stored for this minion and slice")
    return Envelope(
        data=BgMinionStatsDetail.model_validate(payload),
        meta=_stats_meta(payload["slice"]["fetched_at"], count=1),
    )


@canonical_router.get(
    "/minions/{minion}/stats/history",
    response_model=Envelope[BgMinionStatsHistory],
    summary="Snapshot history of one minion's normal and golden totals",
)
async def minion_stats_history(
    minion: str = Path(..., min_length=1, max_length=40),
    mmr: str = MMR_QUERY,
    period: str = PERIOD_QUERY,
    limit: int = Query(90, ge=1, le=1000),
) -> Envelope[BgMinionStatsHistory]:
    from ..hsreplay_bg_minion_stats import get_minion_stats_history

    dbf_id = await _resolve_minion(minion)
    try:
        payload = await asyncio.to_thread(get_minion_stats_history, dbf_id, mmr=mmr, period=period, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    points = payload["points"]
    return Envelope(
        data=BgMinionStatsHistory.model_validate(payload),
        meta=_stats_meta(points[-1]["fetched_at"] if points else None, count=len(points), limit=limit),
    )
