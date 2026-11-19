from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsreplay_assemble import (
    SOURCE_ID,
    BaseNotFound,
    CardNotFound,
    get_assemble,
    list_assemble_cards,
    newest_checked_at,
)
from .models import ApiMeta

router = APIRouter(prefix="/v1/hsreplay", tags=["v1-hsreplay"])

Format = Literal["standard", "wild"]

CARD_QUERY = Query(
    ...,
    min_length=1,
    max_length=40,
    pattern=r"^[A-Za-z0-9_]+$",
    description="Card id (BE_044) or dbf id (128892) of an Assemble card.",
)
FORMAT_QUERY = Query("standard", alias="format", description="standard or wild.")
BASE_QUERY = Query(
    None,
    min_length=1,
    max_length=12,
    pattern=r"^(top|[0-9]+)$",
    description=(
        "Omit for the full tree. ``top`` returns donors only for the base offered "
        "most often; a dbf id returns donors only for that base."
    ),
)


def _meta(*, fetched_at: str | None, stale: bool, count: int) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID, fetched_at=fetched_at, stale=stale, count=count
    ).model_dump(exclude_none=True)


@router.get(
    "/assemble",
    summary="Bases, donors and the halves players take for one Assemble card",
    responses={404: {"description": "Not an Assemble card, or unknown base."}},
)
async def hsreplay_assemble(
    card: str = CARD_QUERY,
    format_name: Format = FORMAT_QUERY,
    base: str | None = BASE_QUERY,
) -> dict[str, Any]:
    """HSReplay Assemble Pools of one card: which base minions come up
    (``offer_rate``, sums to about 100), which two donors are offered with each
    base (``offer_rate`` sums to about 200) and what happens when a donor's
    ``effect`` or ``trigger`` half replaces the same half of the base.

    Rates are percent (0-100, not rounded). ``options[].text`` is the assembled
    card text built from the card base (ru and en, ``null`` when a text has no
    trigger/effect separator). ``low_sample`` marks fewer than 10 picks
    (the ``*`` on HSReplay); ``undervalued`` an option with a winrate at least one
    point above the base's average that is taken less often than the median.
    ``stats.state``: ``ok`` (also a last-good snapshot), ``no_data``,
    ``not_collected`` or ``failed``.
    """

    try:
        data = await asyncio.to_thread(
            get_assemble, card, format_name=format_name, base_selector=base
        )
    except (CardNotFound, BaseNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    stats = data["stats"]
    return {
        "data": data,
        "meta": _meta(
            fetched_at=stats["checked_at"],
            stale=bool(stats["stale"]),
            count=len(data["bases"]),
        ),
    }


@router.get("/assemble/cards", summary="Assemble cards and their collected state")
async def hsreplay_assemble_cards(
    format_name: Format = FORMAT_QUERY,
) -> dict[str, Any]:
    """Every collectible Assemble card of the card base with the state of its
    HSReplay statistics in the format, the pool size and the winrate after assembly."""

    rows = await asyncio.to_thread(list_assemble_cards, format_name=format_name)
    return {
        "data": rows,
        "meta": _meta(
            fetched_at=newest_checked_at(rows),
            stale=any(bool(row["stale"]) for row in rows),
            count=len(rows),
        ),
    }
