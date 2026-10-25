from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..blizzard_leaderboards import (
    SOURCE_ID,
    get_catalog,
    get_coverage,
    get_player,
    get_stats,
    get_table,
    search_players,
)
from .models import ApiMeta

router = APIRouter(prefix="/v1/leaderboards", tags=["v1-leaderboards"])

Region = Literal["EU", "US", "AP"]
Mode = Literal[
    "standard",
    "wild",
    "battlegrounds",
    "battlegroundsduo",
    "arena",
    "undergroundarena",
    "twist",
]

SEASON_QUERY = Query(
    None,
    alias="season",
    ge=0,
    le=100_000,
    description="Blizzard season id; the newest stored season when omitted.",
)
DAY_QUERY = Query(
    None,
    pattern=r"^\d{4}-\d{2}-\d{2}$",
    description="UTC day of a stored snapshot (YYYY-MM-DD); the latest when omitted.",
)


def _meta(
    *,
    fetched_at: str | None,
    stale: bool,
    count: int,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=fetched_at,
        stale=stale,
        count=count,
        limit=limit,
        offset=offset,
    ).model_dump(exclude_none=True)


def _not_collected() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=(
            "This leaderboard is not collected yet; /v1/leaderboards/seasons lists the "
            "stored regions, modes and seasons"
        ),
    )


@router.get("/seasons")
async def leaderboard_seasons() -> dict[str, Any]:
    """Modes, regions and seasons served by Blizzard and what is stored for each."""

    data = await asyncio.to_thread(get_catalog)
    count = sum(
        len(region["seasons"])
        for mode in data["modes"]
        for region in mode["regions"].values()
    )
    return {"data": data, "meta": _meta(fetched_at=None, stale=False, count=count)}


@router.get("")
async def leaderboard_table(
    mode: Mode = "standard",
    region: Region = "EU",
    season_id: int | None = SEASON_QUERY,
    day: str | None = DAY_QUERY,
    q: str | None = Query(
        None, min_length=1, max_length=64, description="Name substring."
    ),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=200_000),
) -> dict[str, Any]:
    """One page of an official leaderboard snapshot, optionally searched by name."""

    data = await asyncio.to_thread(
        get_table,
        region=region,
        mode=mode,
        season_id=season_id,
        day=day,
        query=q.strip() if q else None,
        limit=limit,
        offset=offset,
    )
    if data is None:
        raise _not_collected()
    snapshot = data["snapshot"]
    return {
        "data": data,
        "meta": _meta(
            fetched_at=snapshot["captured_at"],
            stale=snapshot["stale"],
            count=data["total"],
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/stats")
async def leaderboard_stats(
    mode: Mode = "standard",
    region: Region = "EU",
    season_id: int | None = SEASON_QUERY,
    bucket: float | None = Query(
        None, gt=0, le=10_000, description="Rating histogram bucket width."
    ),
) -> dict[str, Any]:
    """Top-100/1000/5000 rating thresholds, player count by day, rating distribution."""

    data = await asyncio.to_thread(
        get_stats, region=region, mode=mode, season_id=season_id, bucket=bucket
    )
    if data is None:
        raise _not_collected()
    snapshot = data["snapshot"]
    return {
        "data": data,
        "meta": _meta(
            fetched_at=snapshot["captured_at"],
            stale=snapshot["stale"],
            count=len(data["series"]),
        ),
    }


@router.get("/players")
async def leaderboard_players(
    q: str = Query(..., min_length=2, max_length=64, description="Name prefix."),
    region: Region | None = None,
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """Players whose name starts with q, with their places on the live boards."""

    rows = await asyncio.to_thread(
        search_players, query=q.strip(), region=region, limit=limit
    )
    return {
        "data": rows,
        "meta": _meta(fetched_at=None, stale=False, count=len(rows), limit=limit),
    }


@router.get("/player")
async def leaderboard_player(
    name: str = Query(..., min_length=1, max_length=64),
    region: Region = "EU",
    mode: Mode | None = None,
) -> dict[str, Any]:
    """Every stored season of a player: best, last rank and the rank by day."""

    data = await asyncio.to_thread(
        get_player, region=region, name=name.strip(), mode=mode
    )
    if data is None:
        raise HTTPException(
            status_code=404, detail="This player is not in a stored leaderboard"
        )
    return {
        "data": data,
        "meta": _meta(fetched_at=None, stale=False, count=len(data["seasons"])),
    }


@router.get("/coverage")
async def leaderboard_coverage(runs: int = Query(10, ge=0, le=100)) -> dict[str, Any]:
    """Collection progress per mode and region and the last runs."""

    data = await asyncio.to_thread(get_coverage, runs)
    return {
        "data": data,
        "meta": _meta(fetched_at=None, stale=False, count=len(data["boards"])),
    }
