from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsguru_matchups import (
    COLLECTED_COINS,
    FORMAT_IDS,
    MIN_ARCHETYPE_GAMES_PRESETS,
    MIN_GAMES_PRESETS,
    RANKS,
    SOURCE_ID,
    default_period,
    get_matchups,
    list_slices,
    summary,
)
from .models import ApiMeta

router = APIRouter(prefix="/v1/hsguru", tags=["v1-hsguru"])

Format = Literal["standard", "wild"]
Rank = Literal[
    "all",
    "top_legend",
    "top_5k",
    "legend",
    "diamond_to_legend",
    "diamond_4to1",
    "bronze_plat",
]
Coin = Literal["any_player", "on_coin", "going_first"]

FORMAT_QUERY = Query("standard", alias="format", description="standard or wild.")
RANK_QUERY = Query(
    "all",
    description=(
        "HSGuru rank: all, top_legend (Top 1000), top_5k, legend, "
        "diamond_to_legend, diamond_4to1, bronze_plat."
    ),
)
PERIOD_QUERY = Query(
    None,
    min_length=1,
    max_length=64,
    description=(
        "HSGuru window from meta.available.periods: past_3_hours … past_30_days, "
        "patch_*, the expansion (e.g. black_empire) or the Bans window "
        "(e.g. black_empire_ban). Default: the Bans window."
    ),
)
COIN_QUERY = Query(
    "any_player", description="Only any_player: HSGuru publishes no coin split for matchups."
)
MIN_GAMES_QUERY = Query(1, ge=1, le=1_000_000, description="Minimum games of one matchup cell.")
MIN_ARCHETYPE_GAMES_QUERY = Query(
    1, ge=1, le=10_000_000, description="Minimum games of an archetype row."
)
LIMIT_QUERY = Query(None, ge=1, le=200, description="Keep only the N most played archetypes.")

_AVAILABLE_EXAMPLE = {
    "formats": ["standard", "wild"],
    "ranks": list(RANKS),
    "periods": [
        "past_3_hours", "past_6_hours", "past_day", "past_3_days", "past_week",
        "past_2_weeks", "past_30_days", "patch_36.6.3", "black_empire", "black_empire_ban",
    ],
    "coins": ["any_player"],
    "min_games": list(MIN_GAMES_PRESETS),
    "min_archetype_games": list(MIN_ARCHETYPE_GAMES_PRESETS),
    "current_period": "black_empire",
    "default_period": "black_empire_ban",
}
_MATCHUPS_EXAMPLE = {
    "data": {
        "format": "standard",
        "rank": "legend",
        "period": "black_empire_ban",
        "coin": "any_player",
        "min_games": 100,
        "min_archetype_games": 1,
        "total_games": 936588,
        "checked_at": "2026-10-25T16:31:28.884000+00:00",
        "changed_at": "2026-10-25T16:31:28.884000+00:00",
        "stale": False,
        "source_url": (
            "https://www.hsguru.com/matchups?format=2&rank=legend&period=black_empire_ban"
            "&min_archetype_sample=1&min_matchup_sample=1"
        ),
        "archetypes": [
            {"archetype": "Aggro Warlock", "class": "WARLOCK", "games": 182401,
             "winrate": 56.5, "popularity_pct": 19.48},
            {"archetype": "C'Thun DH", "class": "DEMONHUNTER", "games": 121862,
             "winrate": 51.0, "popularity_pct": 13.01},
        ],
        "matchups": [
            {"archetype": "Aggro Warlock", "opponent": "C'Thun DH", "winrate": 54.3, "games": 26669},
            {"archetype": "C'Thun DH", "opponent": "Aggro Warlock", "winrate": 45.7, "games": 26669},
        ],
    },
    "meta": {
        "source_id": SOURCE_ID,
        "fetched_at": "2026-10-25T16:31:28.884000+00:00",
        "stale": False,
        "count": 2,
        "available": _AVAILABLE_EXAMPLE,
    },
}
_SLICES_EXAMPLE = {
    "data": [
        {
            "key": "standard|legend|black_empire_ban|any_player",
            "format": "standard",
            "rank": "legend",
            "period": "black_empire_ban",
            "coin": "any_player",
            "active": True,
            "state": "ok",
            "checked_at": "2026-10-25T16:31:28.884000+00:00",
            "changed_at": "2026-10-25T16:31:28.884000+00:00",
            "total_games": 936588,
            "archetypes": 76,
            "cells": 3383,
            "stale": False,
            "last_error": None,
        }
    ],
    "meta": {
        "source_id": SOURCE_ID,
        "fetched_at": "2026-10-25T16:34:24.343787+00:00",
        "stale": False,
        "count": 140,
        "available": _AVAILABLE_EXAMPLE,
        "last_run": {"attempted": 140, "changed": 140, "failed": 0, "unavailable": 0,
                     "scrape_do_credits": 140, "stop_reason": None},
        "estimate": {"slices": 140, "scrape_do_credits_per_day": 798.0,
                     "scrape_do_credits_per_30_days": 23940},
    },
}


def _example(description: str, example: Any) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"example": example}}}


def _dimensions(structured: dict[str, Any]) -> dict[str, Any]:
    dimensions = structured.get("dimensions") or {}
    periods = [str(period) for period in dimensions.get("periods") or []]
    current = structured.get("current_period")
    return {
        "formats": list(FORMAT_IDS),
        "ranks": list(RANKS),
        "periods": periods,
        "coins": list(COLLECTED_COINS),
        "min_games": list(MIN_GAMES_PRESETS),
        "min_archetype_games": list(MIN_ARCHETYPE_GAMES_PRESETS),
        "current_period": current,
        "default_period": default_period(periods, current),
    }


@router.get(
    "/matchups",
    summary="HSGuru archetype matchup matrix of one slice",
    description=(
        "Archetype-versus-archetype matrix from HSGuru /matchups for one slice: "
        "`archetypes[]` (games, winrate, popularity) by games and `matchups[]` "
        "(winrate and games of every pair at or above `min_games`). Slices are "
        "read with HSGuru's lowest sample filters, so any threshold works. "
        "`meta.available` lists formats, ranks, periods and threshold presets; "
        "the default period is the Bans window. HSGuru publishes no coin split "
        "for matchups, so `coin` other than `any_player` is 422."
    ),
    responses={
        200: _example("The matrix of the slice", _MATCHUPS_EXAMPLE),
        404: _example(
            "The slice is not collected yet",
            {"detail": "This slice is not collected yet; /v1/hsguru/matchups/slices lists the stored slices"},
        ),
        422: _example(
            "Unknown period or a coin split HSGuru does not publish",
            {"detail": {"message": "HSGuru /matchups publishes no coin split; only any_player exists",
                        "allowed_coins": ["any_player"]}},
        ),
        503: _example("Nothing collected yet", {"detail": "HSGuru matchups are not collected yet"}),
    },
)
async def hsguru_matchups(
    format_name: Format = FORMAT_QUERY,
    rank: Rank = RANK_QUERY,
    period: str | None = PERIOD_QUERY,
    coin: Coin = COIN_QUERY,
    min_games: int = MIN_GAMES_QUERY,
    min_archetype_games: int = MIN_ARCHETYPE_GAMES_QUERY,
    limit: int | None = LIMIT_QUERY,
) -> dict[str, Any]:
    """HSGuru archetype-versus-archetype matrix of one slice."""

    if coin not in COLLECTED_COINS:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "HSGuru /matchups publishes no coin split; only any_player exists",
                "allowed_coins": list(COLLECTED_COINS),
            },
        )
    available = _dimensions(await asyncio.to_thread(summary))
    resolved = period or available["default_period"]
    if not resolved:
        raise HTTPException(status_code=503, detail="HSGuru matchups are not collected yet")
    if available["periods"] and resolved not in available["periods"]:
        raise HTTPException(
            status_code=422, detail={"allowed_periods": available["periods"]}
        )
    data = await asyncio.to_thread(
        get_matchups,
        format_name=format_name,
        rank=rank,
        period=resolved,
        min_games=min_games,
        min_archetype_games=min_archetype_games,
        limit=limit,
    )
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "This slice is not collected yet; /v1/hsguru/matchups/slices lists "
                "the stored slices"
            ),
        )
    meta = ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=data["checked_at"],
        stale=data["stale"],
        count=len(data["matchups"]),
    ).model_dump(exclude_none=True)
    meta["available"] = available
    return {"data": data, "meta": meta}


@router.get(
    "/matchups/slices",
    summary="Every HSGuru matchup slice with its state",
    description=(
        "One row per planned slice (format, rank, period): queue state, last read, "
        "last change, total games, archetypes and cells. `meta.last_run` gives the "
        "pages and Scrape.do credits of the latest run, `meta.estimate` the credits "
        "a day by TTL."
    ),
    responses={200: _example("Every slice", _SLICES_EXAMPLE)},
)
async def hsguru_matchup_slices() -> dict[str, Any]:
    """Every planned HSGuru matchup slice with its last read and size."""

    slices, structured = await asyncio.gather(
        asyncio.to_thread(list_slices), asyncio.to_thread(summary)
    )
    meta = ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=structured.get("fetched_at"),
        stale=any(item["stale"] for item in slices if item["active"]),
        count=len(slices),
    ).model_dump(exclude_none=True)
    meta["available"] = _dimensions(structured)
    meta["last_run"] = structured.get("last_run")
    meta["estimate"] = structured.get("estimate")
    return {"data": slices, "meta": meta}
