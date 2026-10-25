from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsreplay_card_choices import (
    SOURCE_ID,
    CardNotFound,
    coverage_report,
    get_card_choices,
    list_choice_cards,
)
from .models import ApiMeta

router = APIRouter(prefix="/v1/hsreplay", tags=["v1-hsreplay"])

Format = Literal["standard", "wild"]
Rank = Literal["legend", "diamond_4to1", "diamond", "bronze_gold"]
Kind = Literal["discover", "adapt", "dredge", "choose_one", "excavate"]

CARD_QUERY = Query(
    ...,
    min_length=1,
    max_length=40,
    pattern=r"^[A-Za-z0-9_]+$",
    description="Card id (CORE_YOP_001) or dbf id (97377) of a card that offers a choice.",
)
FORMAT_QUERY = Query(
    None,
    alias="format",
    description="standard or wild; defaults to standard when the card is in Standard.",
)
RANK_QUERY = Query(
    "legend",
    description=(
        "legend or bronze_gold (collected weekly); diamond_4to1 and diamond when "
        "enabled with HS_CARD_CHOICES_RANKS."
    ),
)

KIND_QUERY = Query(None, description="Restrict to one choice mechanic.")


def _meta(*, fetched_at: str | None, stale: bool, count: int) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID, fetched_at=fetched_at, stale=stale, count=count
    ).model_dump(exclude_none=True)


@router.get(
    "/card-choices",
    summary="What players pick from one Discover, Adapt or Choose One card",
    responses={404: {"description": "Unknown card, or the card offers no choice."}},
)
async def hsreplay_card_choices(
    card: str = CARD_QUERY,
    format_name: Format | None = FORMAT_QUERY,
    rank: Rank = RANK_QUERY,
) -> dict[str, Any]:
    """HSReplay pick statistics of one choice card plus its full option pool.

    ``data.options`` are HSReplay's rows: ``popularity`` is how often the option is
    taken when it is offered (the column sums to more than 100%), ``win_rate`` the
    winrate after taking it. ``low_sample`` marks exact 0/100 values (a handful of
    games), ``undervalued`` an option with a winrate at least one point above the
    average that is taken less often than the median option.

    ``data.pool`` is the full pool from the card base: ``resolved`` is false when
    the pool depends on the game (your deck, the opponent's hand) or the text is not
    understood; ``has_stats`` is false for options HSReplay does not list ("мало
    данных"). Excavate cards carry ``pool.tiers`` (treasures by tier, no pick
    statistics); Choose One cards are not tracked by HSReplay.
    ``data.stats.state``: ``ok``, ``no_data`` (HSReplay answered 204),
    ``not_collected`` (outside the collected top cards), ``not_tracked`` or a
    pending/failed target state.
    """

    try:
        data = await asyncio.to_thread(
            get_card_choices, card, format_name=format_name, rank=rank
        )
    except CardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {
        "data": data,
        "meta": _meta(
            fetched_at=data["stats"]["checked_at"],
            stale=bool(data["stats"]["stale"]),
            count=len(data["options"]),
        ),
    }


@router.get("/card-choices/cards", summary="Cards that offer a choice")
async def hsreplay_card_choices_cards(
    format_name: Format | None = FORMAT_QUERY,
    kind: Kind | None = KIND_QUERY,
) -> dict[str, Any]:
    """Every collectible card with Discover, Adapt, Dredge, Choose One or Excavate,
    its formats, whether HSReplay tracks its picks and which format/rank slices are
    stored."""

    rows = await asyncio.to_thread(
        list_choice_cards, format_name=format_name, kind=kind
    )
    newest = max(
        (
            item["checked_at"]
            for row in rows
            for item in row["collected"]
            if item["checked_at"]
        ),
        default=None,
    )
    return {
        "data": rows,
        "meta": _meta(fetched_at=newest, stale=False, count=len(rows)),
    }


@router.get(
    "/card-choices/coverage", summary="HSReplay card choices coverage and last run"
)
async def hsreplay_card_choices_coverage() -> dict[str, Any]:
    """Targets with data per format and rank and the last run with its Scrape.do spend."""

    report = await asyncio.to_thread(coverage_report)
    finished = (report.get("last_run") or {}).get("finished_at")
    return {
        "data": report,
        "meta": _meta(
            fetched_at=finished, stale=finished is None, count=len(report["coverage"])
        ),
    }
