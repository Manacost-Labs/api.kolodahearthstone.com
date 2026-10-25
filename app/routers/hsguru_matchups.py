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


@router.get("/matchups")
async def hsguru_matchups(
    format_name: Format = Query("standard", alias="format"),
    rank: Rank = "all",
    period: str | None = Query(
        None,
        min_length=1,
        max_length=64,
        description=(
            "HSGuru window from meta.available.periods: past_3_hours … past_30_days, "
            "patch_*, the expansion (e.g. black_empire) or the Bans window "
            "(e.g. black_empire_ban). Default: the Bans window."
        ),
    ),
    coin: Coin = "any_player",
    min_games: int = Query(
        1, ge=1, le=1_000_000, description="Minimum games of one matchup cell."
    ),
    min_archetype_games: int = Query(
        1, ge=1, le=10_000_000, description="Minimum games of an archetype row."
    ),
    limit: int | None = Query(
        None, ge=1, le=200, description="Keep only the N most played archetypes."
    ),
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


@router.get("/matchups/slices")
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
