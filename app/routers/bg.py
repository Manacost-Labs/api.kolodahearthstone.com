from __future__ import annotations

from fastapi import APIRouter, Query

from .models import ApiMeta, BgHeroRow, BgMinionRow, Envelope, freshest_timestamp, timestamp_is_stale


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
