from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..blizzard_leaderboards import (
    SOURCE_ID,
    get_catalog,
    get_coverage,
    get_player,
    get_player_history,
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

HISTORY_MODE_QUERY = Query(..., description="One mode: the series spans its seasons.")


def _example(data: Any, count: int = 1, **meta: Any) -> dict[int | str, dict[str, Any]]:
    """OpenAPI 200 example in the {data, meta} envelope of this router."""

    return {
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "data": data,
                        "meta": {
                            "source_id": SOURCE_ID,
                            "stale": False,
                            "count": count,
                            **meta,
                        },
                    }
                }
            }
        }
    }


# Live run of 2026-10-25 (EU Battlegrounds, season 19), shortened.
_BOARD = {
    "region": "EU",
    "mode": "battlegrounds",
    "season_id": 19,
    "season_start": None,
    "is_latest": True,
    "final": False,
    "rated": True,
}
_SNAPSHOT = {
    "day": "2026-10-25",
    "captured_at": "2026-10-25T16:42:14+00:00",
    "total_size": 6549,
    "stored_rows": 6547,
    "truncated": False,
    "final": False,
    "stale": False,
}
TABLE_EXAMPLE = _example(
    {
        "board": _BOARD,
        "snapshot": _SNAPSHOT,
        "days": ["2026-10-25"],
        "query": None,
        "total": 6547,
        "rows": [
            {"rank": 1, "name": "XQN", "rating": 20104.0, "previous_rank": None},
            {
                "rank": 2,
                "name": "СтоякБезДела",
                "rating": 19861.0,
                "previous_rank": None,
            },
        ],
    },
    count=6547,
    fetched_at="2026-10-25T16:42:14+00:00",
    limit=2,
    offset=0,
)
STATS_EXAMPLE = _example(
    {
        "board": _BOARD,
        "snapshot": _SNAPSHOT,
        "top_rating": 20104.0,
        "thresholds": [
            {"rank": 100, "reached": True, "rating": 13259.0},
            {"rank": 1000, "reached": True, "rating": 9563.0},
            {"rank": 5000, "reached": True, "rating": 8035.0},
        ],
        "rating": {
            "rated_players": 6547,
            "min": 8000.0,
            "max": 20104.0,
            "mean": 8753.79,
            "median": 8230.0,
        },
        "histogram": [
            {"from": 8000, "to": 8500, "players": 4101},
            {"from": 8500, "to": 9000, "players": 858},
        ],
        "histogram_bucket": 500,
        "series": [
            {
                "day": "2026-10-25",
                "captured_at": "2026-10-25T16:42:14+00:00",
                "total_size": 6549,
                "stored_rows": 6547,
                "truncated": 0,
                "final": 0,
                "rating_at_1": 20104.0,
                "rating_at_100": 13259.0,
                "rating_at_1000": 9563.0,
                "rating_at_5000": 8035.0,
            }
        ],
    }
)
PLAYERS_EXAMPLE = _example(
    [
        {
            "region": "EU",
            "name": "XQN",
            "current": [
                {"mode": "battlegrounds", "season_id": 19, "rank": 1, "rating": 20104.0}
            ],
        }
    ],
    limit=20,
)
PLAYER_EXAMPLE = _example(
    {
        "player": {"region": "EU", "name": "XQN"},
        "shared_name": False,
        "seasons": [
            {
                "mode": "battlegrounds",
                "season_id": 19,
                "season_start": None,
                "is_latest": True,
                "final": False,
                "rated": True,
                "best_rank": 1,
                "best_day": "2026-10-25",
                "peak_rating": 20104.0,
                "peak_rating_day": "2026-10-25",
                "final_rank": None,
                "final_rating": None,
                "first_day": "2026-10-25",
                "last_rank": 1,
                "last_rating": 20104.0,
                "last_day": "2026-10-25",
                "in_latest_snapshot": True,
                "days": 1,
                "series": [
                    {
                        "day": "2026-10-25",
                        "rank": 1,
                        "rating": 20104.0,
                        "total_size": 6549,
                        "final": False,
                    }
                ],
            }
        ],
    }
)
# EU Battlegrounds of one player: season 18 ended with its final read, 19 is live.
PLAYER_HISTORY_EXAMPLE = _example(
    {
        "player": {"region": "EU", "name": "XQN"},
        "shared_name": False,
        "mode": "battlegrounds",
        "rated": True,
        "from": None,
        "to": None,
        "seasons": [
            {
                "mode": "battlegrounds",
                "season_id": 18,
                "season_start": None,
                "is_latest": False,
                "final": True,
                "rated": True,
                "best_rank": 2,
                "best_day": "2026-10-26",
                "peak_rating": 19650.0,
                "peak_rating_day": "2026-10-26",
                "final_rank": 2,
                "final_rating": 19650.0,
                "first_day": "2026-10-26",
                "last_rank": 2,
                "last_rating": 19650.0,
                "last_day": "2026-10-26",
                "in_latest_snapshot": True,
                "days": 1,
            },
            {
                "mode": "battlegrounds",
                "season_id": 19,
                "season_start": None,
                "is_latest": True,
                "final": False,
                "rated": True,
                "best_rank": 1,
                "best_day": "2026-10-25",
                "peak_rating": 20104.0,
                "peak_rating_day": "2026-10-26",
                "final_rank": None,
                "final_rating": None,
                "first_day": "2026-10-25",
                "last_rank": 1,
                "last_rating": 20104.0,
                "last_day": "2026-10-26",
                "in_latest_snapshot": True,
                "days": 2,
            },
        ],
        "points": [
            {
                "season_id": 19,
                "day": "2026-10-25",
                "rank": 1,
                "rating": 20090.0,
                "total_size": 6613,
                "final": False,
            },
            {
                "season_id": 18,
                "day": "2026-10-26",
                "rank": 2,
                "rating": 19650.0,
                "total_size": 50000,
                "final": True,
            },
            {
                "season_id": 19,
                "day": "2026-10-26",
                "rank": 1,
                "rating": 20104.0,
                "total_size": 6631,
                "final": False,
            },
        ],
    },
    count=3,
)
SEASONS_EXAMPLE = _example(
    {
        "regions": ["EU", "US", "AP"],
        "modes": [
            {
                "mode": "standard",
                "rated": False,
                "regions": {
                    "EU": {
                        "latest_season": 156,
                        "seasons": [
                            {
                                "season_id": 156,
                                "season_start": "2026-10-01",
                                "is_latest": True,
                                "final": False,
                                "has_data": True,
                                "day": "2026-10-25",
                                "total_size": 16668,
                                "truncated": False,
                            }
                        ],
                    }
                },
            }
        ],
    },
    count=490,
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


@router.get("/seasons", responses=SEASONS_EXAMPLE)
async def leaderboard_seasons() -> dict[str, Any]:
    """Modes, regions and seasons served by Blizzard and what is stored for each."""

    data = await asyncio.to_thread(get_catalog)
    count = sum(
        len(region["seasons"])
        for mode in data["modes"]
        for region in mode["regions"].values()
    )
    return {"data": data, "meta": _meta(fetched_at=None, stale=False, count=count)}


@router.get("", responses=TABLE_EXAMPLE)
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


@router.get("/stats", responses=STATS_EXAMPLE)
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


@router.get("/players", responses=PLAYERS_EXAMPLE)
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


@router.get("/player", responses=PLAYER_EXAMPLE)
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


@router.get("/player/history", responses=PLAYER_HISTORY_EXAMPLE)
async def leaderboard_player_history(
    name: str = Query(..., min_length=1, max_length=64),
    mode: Mode = HISTORY_MODE_QUERY,
    region: Region = "EU",
    day_from: str | None = Query(
        None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$", description="First UTC day."
    ),
    day_to: str | None = Query(
        None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$", description="Last UTC day."
    ),
) -> dict[str, Any]:
    """Rank and rating of a player by day across seasons, with season peaks and finals."""

    for value in (day_from, day_to):
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="from and to must be real days YYYY-MM-DD"
                ) from None

    data = await asyncio.to_thread(
        get_player_history,
        region=region,
        name=name.strip(),
        mode=mode,
        day_from=day_from,
        day_to=day_to,
    )
    if data is None:
        raise HTTPException(
            status_code=404, detail="This player is not in a stored leaderboard"
        )
    return {
        "data": data,
        "meta": _meta(fetched_at=None, stale=False, count=len(data["points"])),
    }


@router.get("/coverage")
async def leaderboard_coverage(runs: int = Query(10, ge=0, le=100)) -> dict[str, Any]:
    """Collection progress per mode and region and the last runs."""

    data = await asyncio.to_thread(get_coverage, runs)
    return {
        "data": data,
        "meta": _meta(fetched_at=None, stale=False, count=len(data["boards"])),
    }
