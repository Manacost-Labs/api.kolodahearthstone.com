from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsguru_mulligan import (
    SOURCE_ID,
    Selector,
    coverage_report,
    current_period,
    get_history,
    get_statistics,
    list_slices,
)
from .models import ApiMeta

router = APIRouter(prefix="/v1/hsguru", tags=["v1-hsguru"])

Format = Literal["standard", "wild"]
Rank = Literal[
    "all", "diamond_4to1", "diamond_to_legend", "legend", "top_5k", "top_legend"
]
Coin = Literal["any_player", "on_coin", "going_first"]
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

ARCHETYPE_QUERY = Query(
    None, min_length=1, max_length=200, description="HSGuru archetype name."
)
DECK_QUERY = Query(
    None, ge=1, le=2_000_000_000, description="HSGuru deck id (/deck/<id>)."
)
FORMAT_QUERY = Query(
    None,
    alias="format",
    description="Required with archetype; optional with deck_id.",
)
PERIOD_QUERY = Query(
    "current",
    min_length=1,
    max_length=64,
    description=(
        "HSGuru period: past_day, past_3_days, past_week, past_2_weeks, past_30_days, "
        "patch_*, the expansion window (e.g. black_empire), the Bans window "
        "(e.g. black_empire_ban), or current for the meta matrix current window."
    ),
)


def _subject(
    archetype: str | None, deck_id: int | None, format_name: str | None
) -> tuple[str, str | None, int | None]:
    if (archetype is None) == (deck_id is None):
        raise HTTPException(
            status_code=422, detail="Pass exactly one of archetype or deck_id"
        )
    if archetype is not None:
        if format_name is None:
            raise HTTPException(
                status_code=422, detail="format is required with archetype"
            )
        return "archetype", archetype.strip(), None
    return "deck", None, deck_id


def _period(period: str) -> str:
    if period != "current":
        return period
    resolved = current_period()
    if not resolved:
        raise HTTPException(
            status_code=503, detail="The current HSGuru period is not known yet"
        )
    return resolved


def _meta(*, fetched_at: str | None, stale: bool, count: int) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=fetched_at,
        stale=stale,
        count=count,
    ).model_dump(exclude_none=True)


def _not_collected() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=(
            "This slice is not collected yet; /v1/hsguru/mulligan/slices lists the stored "
            "slices of an archetype or deck"
        ),
    )


async def _statistics(selector: Selector, **filters: int) -> dict[str, Any]:
    data = await asyncio.to_thread(get_statistics, selector, **filters)
    if data is None:
        raise _not_collected()
    return {
        "data": data,
        "meta": _meta(
            fetched_at=data["checked_at"], stale=data["stale"], count=len(data["rows"])
        ),
    }


@router.get("/mulligan")
async def hsguru_mulligan(
    archetype: str | None = ARCHETYPE_QUERY,
    deck_id: int | None = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
    rank: Rank = "all",
    period: str = PERIOD_QUERY,
    coin: Coin = "any_player",
    opponent_class: OpponentClass = "any",
    min_mull_count: int = Query(0, ge=0, le=1_000_000),
    min_drawn_count: int = Query(0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    """HSGuru mulligan guide of an archetype or deck: per-card impact and counts."""

    subject_type, name, deck = _subject(archetype, deck_id, format_name)
    selector = Selector(
        kind="cards",
        subject_type=subject_type,
        format_name=format_name,
        archetype=name,
        deck_id=deck,
        rank=rank,
        period=_period(period),
        coin=coin,
        opponent_class=opponent_class,
    )
    return await _statistics(
        selector, min_mull_count=min_mull_count, min_drawn_count=min_drawn_count
    )


@router.get("/class-matchups")
async def hsguru_class_matchups(
    archetype: str | None = ARCHETYPE_QUERY,
    deck_id: int | None = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
    rank: Rank = "all",
    period: str = PERIOD_QUERY,
    coin: Coin = "any_player",
) -> dict[str, Any]:
    """Winrate of an archetype or deck against each class, with the Total row."""

    subject_type, name, deck = _subject(archetype, deck_id, format_name)
    selector = Selector(
        kind="matchups",
        subject_type=subject_type,
        format_name=format_name,
        archetype=name,
        deck_id=deck,
        rank=rank,
        period=_period(period),
        coin=coin,
    )
    return await _statistics(selector)


@router.get("/mulligan/history")
async def hsguru_mulligan_history(
    archetype: str | None = ARCHETYPE_QUERY,
    deck_id: int | None = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
    kind: Literal["cards", "matchups"] = "cards",
    rank: Rank = "all",
    period: str = PERIOD_QUERY,
    coin: Coin = "any_player",
    opponent_class: OpponentClass = "any",
    card: str | None = Query(
        None, min_length=1, max_length=120, description="dbf id, card id or card name"
    ),
    limit: int = Query(60, ge=1, le=500),
) -> dict[str, Any]:
    """Stored snapshots of one slice (games, winrate), optionally with one card's row."""

    subject_type, name, deck = _subject(archetype, deck_id, format_name)
    selector = Selector(
        kind=kind,
        subject_type=subject_type,
        format_name=format_name,
        archetype=name,
        deck_id=deck,
        rank=rank,
        period=_period(period),
        coin=coin,
        opponent_class=opponent_class,
    )
    data = await asyncio.to_thread(get_history, selector, card=card, limit=limit)
    if data is None:
        raise _not_collected()
    return {
        "data": data,
        "meta": _meta(
            fetched_at=data["checked_at"], stale=False, count=len(data["points"])
        ),
    }


@router.get("/mulligan/slices")
async def hsguru_mulligan_slices(
    archetype: str | None = ARCHETYPE_QUERY,
    deck_id: int | None = DECK_QUERY,
    format_name: Format | None = FORMAT_QUERY,
) -> dict[str, Any]:
    """Every stored mulligan and class-matchup slice of one archetype or deck."""

    if (archetype is None) == (deck_id is None):
        raise HTTPException(
            status_code=422, detail="Pass exactly one of archetype or deck_id"
        )
    rows = await asyncio.to_thread(
        list_slices,
        subject_type="archetype" if archetype is not None else "deck",
        format_name=format_name,
        archetype=archetype.strip() if archetype else None,
        deck_id=deck_id,
    )
    newest = max(
        (row["last_success_at"] for row in rows if row["last_success_at"]), default=None
    )
    return {
        "data": rows,
        "meta": _meta(fetched_at=newest, stale=not rows, count=len(rows)),
    }


@router.get("/mulligan/coverage")
async def hsguru_mulligan_coverage(
    runs: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """Collection coverage by subject, kind and tier, plus the recent refresh runs."""

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
