from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from ..hsguru_streamers import (
    SOURCE_ID,
    coverage_report,
    get_streamer,
    latest_revision,
    list_streamers,
    recent_decks,
    sqlite_fetch,
    streamer_decks,
    streaming_now,
)
from ..sources import SOURCE_BY_ID
from . import hsguru_streamers_examples as examples
from .models import ApiMeta

router = APIRouter(prefix="/v1/hsguru", tags=["v1-hsguru"])

Format = Literal["standard", "wild", "classic", "twist"]
PlayerClass = Literal[
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
Sort = Literal["active", "followers", "decks", "rank"]
Mode = Literal[
    "standard",
    "wild",
    "classic",
    "twist",
    "battlegrounds",
    "arena",
    "mercenaries",
    "duels",
    "tavern_brawl",
    "fireside_gathering",
]

FORMAT_QUERY = Query(None, alias="format", description="Deck format.")
CLASS_QUERY = Query(None, description="Upper-case class key, e.g. PRIEST.")
LANGUAGE_QUERY = Query(
    None,
    pattern=r"^[a-z]{2}$",
    description="Two-letter stream language HSGuru knows (by default only ru is read).",
)
MODE_QUERY = Query(None, description="HSGuru mode, or the current deck's format.")
KNOWN_QUERY = Query(False, description="Only streamers HSGuru has decks of.")
LIVE_LIMIT_QUERY = Query(100, ge=1, le=300)
STREAMER_PATH = Path(
    ...,
    min_length=1,
    max_length=64,
    description="Streamer slug: Twitch login, HSGuru display name or Twitch user id.",
)


def _meta(*, count: int, stale: bool | None = None, **extra: Any) -> dict[str, Any]:
    fetched_at = latest_revision()
    if stale is None:
        source = SOURCE_BY_ID[SOURCE_ID]
        stale = fetched_at is None or datetime.fromisoformat(fetched_at) < datetime.now(
            UTC
        ) - timedelta(hours=source.stale_hours or 2)
    return ApiMeta(
        source_id=SOURCE_ID, fetched_at=fetched_at, stale=stale, count=count, **extra
    ).model_dump(exclude_none=True)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="HSGuru has no decks of this streamer")


@router.get(
    "/streamers",
    summary="HSGuru streamers with channel links, live stream and last decks",
    responses=examples.STREAMERS,
)
async def hsguru_streamers(
    q: str | None = Query(
        None, min_length=1, max_length=64, description="Name search."
    ),
    format_name: Format | None = FORMAT_QUERY,
    player_class: PlayerClass | None = CLASS_QUERY,
    language: str | None = LANGUAGE_QUERY,
    live: bool = Query(False, description="Only streamers live right now."),
    days: int = Query(
        30, ge=0, le=3650, description="Played a deck in N days; 0 = ever."
    ),
    sort: Sort = "active",
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    decks: int = Query(3, ge=0, le=10, description="Last decks per streamer."),
) -> dict[str, Any]:
    """HSGuru streamers with Twitch/YouTube/X links, live state and last decks.

    Streamers who played a deck in the last `days` (0 = ever), filtered by deck
    `format` and `player_class`; `sort=active` lists live streamers first, then by
    the last game. `recent_decks` holds the last `decks` decks; `window` counts the
    decks and the best legend rank inside the filters. `meta.count` is the total.
    """

    data = await list_streamers(
        sqlite_fetch,
        q=q,
        format_name=format_name,
        player_class=player_class,
        language=language,
        live_only=live,
        days=days or None,
        sort=sort,
        limit=limit,
        offset=offset,
        decks_per_streamer=decks,
    )
    return {
        "data": data["items"],
        "meta": _meta(count=data["total"], limit=limit, offset=offset),
    }


@router.get(
    "/streamers/coverage",
    summary="Coverage and the last runs of the HSGuru streamers pipeline",
    responses=examples.COVERAGE,
)
async def hsguru_streamers_coverage(
    runs: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """Stored streamers, decks, VODs and the last refresh runs."""

    data = await asyncio.to_thread(coverage_report, runs=runs)
    return {"data": data, "meta": _meta(count=int(data["decks"] or 0))}


@router.get(
    "/streamers/{streamer}",
    summary="One HSGuru streamer: profile, links, live stream, facets, VODs",
    responses=examples.STREAMER,
)
async def hsguru_streamer(streamer: str = STREAMER_PATH) -> dict[str, Any]:
    """One streamer: profile, links, live stream, deck counts by format and class, VODs."""

    data = await get_streamer(sqlite_fetch, streamer=streamer)
    if data is None:
        raise _not_found()
    return {"data": data, "meta": _meta(count=data["decks_total"])}


@router.get(
    "/streamers/{streamer}/decks",
    summary="Decks of one HSGuru streamer with VOD links",
    responses=examples.STREAMER_DECKS,
)
async def hsguru_streamer_decks(
    streamer: str = STREAMER_PATH,
    format_name: Format | None = FORMAT_QUERY,
    player_class: PlayerClass | None = CLASS_QUERY,
    days: int = Query(0, ge=0, le=3650, description="Played in N days; 0 = ever."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
) -> dict[str, Any]:
    """Decks of one streamer, newest game first, with VOD links where known."""

    data = await streamer_decks(
        sqlite_fetch,
        streamer=streamer,
        format_name=format_name,
        player_class=player_class,
        days=days or None,
        limit=limit,
        offset=offset,
    )
    if data is None:
        raise _not_found()
    return {
        "data": data["items"],
        "streamer": data["streamer"],
        "meta": _meta(count=data["total"], limit=limit, offset=offset),
    }


@router.get(
    "/streamer-decks",
    summary="Decks of every HSGuru streamer, newest game first",
    responses=examples.RECENT_DECKS,
)
async def hsguru_recent_streamer_decks(
    format_name: Format | None = FORMAT_QUERY,
    player_class: PlayerClass | None = CLASS_QUERY,
    archetype: str | None = Query(None, min_length=1, max_length=200),
    max_rank: int | None = Query(
        None, ge=1, le=1_000_000, description="Legend peak at or better than this rank."
    ),
    days: int = Query(7, ge=0, le=3650, description="Played in N days; 0 = ever."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
) -> dict[str, Any]:
    """Decks of every HSGuru streamer, newest game first."""

    data = await recent_decks(
        sqlite_fetch,
        format_name=format_name,
        player_class=player_class,
        archetype=archetype,
        max_rank=max_rank,
        days=days or None,
        limit=limit,
        offset=offset,
    )
    return {
        "data": data["items"],
        "meta": _meta(count=data["total"], limit=limit, offset=offset),
    }


@router.get(
    "/streaming-now",
    summary="Who is live now, with which deck and in which mode",
    responses=examples.STREAMING_NOW,
)
async def hsguru_streaming_now(
    language: str | None = LANGUAGE_QUERY,
    mode: Mode | None = MODE_QUERY,
    known: bool = KNOWN_QUERY,
    limit: int = LIVE_LIMIT_QUERY,
) -> dict[str, Any]:
    """Live Hearthstone streams, with the deck played on the current broadcast.

    Ordered by viewers. `mode` is HSGuru's mode or, when HSGuru does not know it,
    the format of the deck played after the stream started (`mode_source=deck`).
    `streamer` is set for streamers HSGuru has decks of. `meta.stale` is true when
    the last read is older than 20 minutes. Never cached.
    """

    data = await streaming_now(
        sqlite_fetch, language=language, mode=mode, known_only=known, limit=limit
    )
    meta = _meta(count=data["total"], stale=data["stale"])
    meta["observed_at"] = data["observed_at"]
    return {"data": data["items"], "meta": meta}
