from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsreplay_mulligan import (
    SOURCE_ID,
    Selector,
    SliceUnavailable,
    coverage_report,
    get_history,
    get_mulligan,
    list_decks,
    list_slices,
)
from .models import ApiMeta

router = APIRouter(prefix="/v1/hsreplay", tags=["v1-hsreplay"])

Format = Literal["standard", "wild"]
Rank = Literal["legend", "diamond_4to1", "diamond", "bronze_gold"]
Initiative = Literal["all", "first", "coin"]
Period = Literal["last_30_days", "current_patch", "current_expansion"]
OpponentClass = Literal[
    "any",
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
]

DECK_QUERY = Query(
    ...,
    min_length=8,
    max_length=40,
    pattern=r"^[A-Za-z0-9]+$",
    description="HSReplay deck shortid, as in https://hsreplay.net/decks/<shortid>/.",
)
FORMAT_QUERY = Query(
    None,
    alias="format",
    description="standard or wild; optional, a deck belongs to one format.",
)
RANK_QUERY = Query(
    "legend",
    description=(
        "legend, diamond_4to1 (Diamond 4-1), diamond (Diamond 10-1) or bronze_gold. "
        "Only bronze_gold with initiative=all is free on HSReplay; the rest needs the "
        "premium session."
    ),
)
INITIATIVE_QUERY = Query(
    "all",
    description="all, first (going first) or coin (on the coin).",
)
PERIOD_QUERY = Query(
    "last_30_days",
    description="HSReplay time range: last_30_days, current_patch or current_expansion.",
)
OPPONENT_QUERY = Query(
    "any",
    description=(
        "any, or an upper-case class key; per-class splits exist only in premium answers."
    ),
)

POOL_RANK_QUERY = Query(None, description="Restrict to one rank pool.")
DECK_LIMIT_QUERY = Query(200, ge=1, le=1000)


def _meta(*, fetched_at: str | None, stale: bool, count: int) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=fetched_at,
        stale=stale,
        count=count,
    ).model_dump(exclude_none=True)


def _unavailable(exc: SliceUnavailable) -> HTTPException:
    detail: Any = exc.detail
    if exc.premium_session is not None:
        detail = {"message": exc.detail, "premium_session": exc.premium_session}
    return HTTPException(status_code=exc.status, detail=detail)


@router.get(
    "/mulligan",
    summary="HSReplay mulligan guide of one deck slice",
    responses={
        404: {
            "description": "The deck or slice is not collected (or has no breakdown)."
        },
        503: {
            "description": (
                "A premium slice cannot be served because the HSReplay premium session "
                "is rejected; the body carries premium_session."
            )
        },
    },
)
async def hsreplay_mulligan(
    deck: str = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
    rank: Rank = RANK_QUERY,
    initiative: Initiative = INITIATIVE_QUERY,
    period: Period = PERIOD_QUERY,
    opponent_class: OpponentClass = OPPONENT_QUERY,
) -> dict[str, Any]:
    """HSReplay mulligan v2 of a deck: per card keep %, opening-hand, drawn and played
    winrate, turns held and turn played, plus ``*_impact`` (winrate minus the base
    winrate in percentage points, the unit of ``/v1/hsguru/mulligan``).

    ``data.source`` is ``hsreplay``; ``data.scope`` tells whether HSReplay had
    enough games of this exact deck (``deck``) or fell back to the archetype or
    class. ``no_data`` is true when HSReplay had no games in the slice (HTTP 204).
    """

    selector = Selector(
        deck_id=deck,
        format_name=format_name,
        rank=rank,
        initiative=initiative,
        period=period,
        opponent_class=opponent_class,
    )
    try:
        data = await asyncio.to_thread(get_mulligan, selector)
    except SliceUnavailable as exc:
        raise _unavailable(exc) from None
    return {
        "data": data,
        "meta": _meta(
            fetched_at=data["checked_at"], stale=data["stale"], count=len(data["rows"])
        ),
    }


@router.get("/mulligan/decks", summary="Decks of the HSReplay mulligan collection")
async def hsreplay_mulligan_decks(
    format_name: Format | None = FORMAT_QUERY,
    rank: Rank | None = POOL_RANK_QUERY,
    limit: int = DECK_LIMIT_QUERY,
) -> dict[str, Any]:
    """The deck pool per format and rank (the top of HSReplay's deck list by games
    plus the best winrates) with archetype, class, games, winrate and how many of
    each deck's slices hold data."""

    rows = await asyncio.to_thread(
        list_decks, format_name=format_name, rank=rank, limit=limit
    )
    newest = max((row["checked_at"] for row in rows if row["checked_at"]), default=None)
    return {
        "data": rows,
        "meta": _meta(fetched_at=newest, stale=not rows, count=len(rows)),
    }


@router.get("/mulligan/slices", summary="Stored slices of one HSReplay deck")
async def hsreplay_mulligan_slices(
    deck: str = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
) -> dict[str, Any]:
    """Every planned slice of one deck (rank x initiative x period) with its state,
    last check, whether it is premium and the stored scope."""

    rows = await asyncio.to_thread(list_slices, deck_id=deck, format_name=format_name)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="This deck is not in the HSReplay mulligan collection",
        )
    newest = max(
        (row["last_success_at"] for row in rows if row["last_success_at"]), default=None
    )
    return {
        "data": rows,
        "meta": _meta(fetched_at=newest, stale=newest is None, count=len(rows)),
    }


@router.get("/mulligan/history", summary="Snapshots of one HSReplay deck slice")
async def hsreplay_mulligan_history(
    deck: str = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
    rank: Rank = RANK_QUERY,
    initiative: Initiative = INITIATIVE_QUERY,
    period: Period = PERIOD_QUERY,
    opponent_class: OpponentClass = OPPONENT_QUERY,
    card: str | None = Query(
        None, min_length=1, max_length=120, description="dbf id, card id or card name"
    ),
    limit: int = Query(60, ge=1, le=500),
) -> dict[str, Any]:
    """Stored snapshots of one slice (games, base winrate, scope), newest first,
    optionally with one card's row in each."""

    selector = Selector(
        deck_id=deck,
        format_name=format_name,
        rank=rank,
        initiative=initiative,
        period=period,
        opponent_class=opponent_class,
    )
    try:
        data = await asyncio.to_thread(get_history, selector, card=card, limit=limit)
    except SliceUnavailable as exc:
        raise _unavailable(exc) from None
    return {
        "data": data,
        "meta": _meta(
            fetched_at=data["checked_at"], stale=False, count=len(data["points"])
        ),
    }


@router.get(
    "/mulligan/coverage",
    summary="HSReplay mulligan coverage, premium session and recent runs",
)
async def hsreplay_mulligan_coverage(
    runs: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """Coverage by format, rank, initiative and period, the premium session status
    of the last run (``ok``, ``denied``, ``not_premium``, ``missing_session`` or
    ``unknown``) and the recent runs with their Scrape.do spend."""

    report = await asyncio.to_thread(coverage_report, runs=runs)
    newest = next(
        (run["finished_at"] for run in report["runs"] if run.get("finished_at")), None
    )
    return {
        "data": report,
        "meta": _meta(
            fetched_at=newest, stale=newest is None, count=len(report["coverage"])
        ),
    }
