from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from .. import hsguru_coverage_views as views
from ..hsguru_brawl import SOURCE_ID as BRAWL_SOURCE_ID
from ..hsguru_esports import (
    LEGACY_SOURCE_ID,
    POINTS_SOURCE_ID,
    TOURNAMENTS_SOURCE_ID,
)
from ..sources import SOURCE_BY_ID
from ..storage import load_dataset
from . import hsguru_coverage_examples as examples
from .models import ApiMeta, timestamp_is_stale

router = APIRouter(prefix="/v1/hsguru", tags=["v1-hsguru"])

BrawlRank = Literal[
    "all",
    "top_legend",
    "top_5k",
    "legend",
    "diamond_4to1",
    "diamond_to_legend",
    "bronze_plat",
]
BrawlDeckRank = Literal["all", "legend", "diamond_to_legend"]
Coin = Literal["any_player", "on_coin", "going_first"]

BRAWL_RANK_QUERY = Query(
    "all",
    description="HSGuru rank; Brawl games almost all fall into `all`.",
)
BRAWL_COIN_QUERY = Query(
    "any_player", description="Any player, on coin or going first."
)
BRAWL_DECK_RANK_QUERY = Query("all", description="HSGuru rank of the deck list.")


def _respond(
    source_id: str,
    select: Callable[[dict[str, Any]], Any],
    *,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    dataset = load_dataset(source_id)
    structured = views.structured_of(dataset)
    if not structured:
        raise HTTPException(status_code=503, detail=f"{source_id} is not available yet")
    try:
        data = select(structured)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    items = data.get("items") if isinstance(data, dict) else data
    count = len(items) if isinstance(items, list) else 1
    if isinstance(items, list) and limit is not None:
        page = items[offset : offset + limit]
        if isinstance(data, dict):
            data = {**data, "items": page}
        else:
            data = page
    fetched_at = (dataset or {}).get("fetched_at") or structured.get("fetched_at")
    source = SOURCE_BY_ID[source_id]
    meta = ApiMeta(
        source_id=source_id,
        fetched_at=fetched_at,
        stale=timestamp_is_stale(fetched_at, max_age_hours=source.stale_hours or 48),
        count=count,
        limit=limit,
        offset=offset if limit is not None else None,
    ).model_dump(exclude_none=True)
    return {"data": data, "meta": meta}


@router.get(
    "/brawl/meta",
    summary="HSGuru Tavern Brawl archetype meta",
    responses=examples.response(examples.BRAWL_META, BRAWL_SOURCE_ID, 72),
)
def hsguru_brawl_meta(
    rank: BrawlRank = BRAWL_RANK_QUERY,
    coin: Coin = BRAWL_COIN_QUERY,
    opponent_class: str = Query(
        "any",
        max_length=16,
        description="`any` or an upper-case class key such as `MAGE` (all ranks only).",
    ),
    min_games: int = Query(
        10, ge=0, le=1_000_000, description="Drop archetypes with fewer games."
    ),
) -> dict[str, Any]:
    """Archetype winrate, popularity, games, turns, duration and climbing speed
    of the current Tavern Brawl (`format=-1&period=brawl`), refreshed every
    three hours. `meta.count` is the number of archetypes after `min_games`."""
    return _respond(
        BRAWL_SOURCE_ID,
        lambda structured: views.brawl_meta(
            structured,
            rank=rank,
            coin=coin,
            opponent_class=opponent_class,
            min_games=min_games,
        ),
    )


@router.get(
    "/brawl/decks",
    summary="HSGuru Tavern Brawl decks",
    responses=examples.response(examples.BRAWL_DECKS, BRAWL_SOURCE_ID, 13),
)
def hsguru_brawl_decks(
    rank: BrawlDeckRank = BRAWL_DECK_RANK_QUERY,
    class_name: str | None = Query(
        None, alias="class", max_length=32, description="Class name, e.g. `Priest`."
    ),
    archetype: str | None = Query(
        None, max_length=120, description="Exact archetype name (case-insensitive)."
    ),
) -> dict[str, Any]:
    """Most played decks of the current Tavern Brawl (from 50 games) with deck
    codes, winrate and games."""
    return _respond(
        BRAWL_SOURCE_ID,
        lambda structured: views.brawl_decks(
            structured, rank=rank, class_name=class_name, archetype=archetype
        ),
    )


@router.get(
    "/esports/upcoming",
    summary="Upcoming tournaments (HSGuru /esports)",
    responses=examples.response(examples.UPCOMING, TOURNAMENTS_SOURCE_ID, 15),
)
def hsguru_esports_upcoming() -> dict[str, Any]:
    """Upcoming and running tournaments listed on HSGuru /esports: Battlefy id,
    name, UTC start time and tags (Open/Bo3/region/format)."""
    return _respond(
        TOURNAMENTS_SOURCE_ID,
        lambda structured: {"items": list(structured.get("upcoming") or [])},
    )


@router.get(
    "/esports/tournaments",
    summary="Tournament summaries",
    responses=examples.response(
        examples.TOURNAMENTS, TOURNAMENTS_SOURCE_ID, 32, limit=1, offset=0
    ),
)
def hsguru_esports_tournaments(
    kind: Literal["tour_stop", "community"] | None = Query(
        None,
        description="`tour_stop` (official Masters Tours) or `community` "
        "(started within 14 days, from /esports).",
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Tournaments newest first: winner (first place of the last stage), stage
    names, player count, top five lineup archetypes. `lineups_complete` is
    false when HSGuru shows only the first 50 of more lineups."""
    return _respond(
        TOURNAMENTS_SOURCE_ID,
        lambda structured: views.tournament_summaries(structured, kind=kind),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/esports/tournaments/{tournament_id}",
    summary="One tournament with standings and lineups",
    responses=examples.response(examples.TOURNAMENT, TOURNAMENTS_SOURCE_ID, 1),
)
def hsguru_esports_tournament(tournament_id: str) -> dict[str, Any]:
    """One tournament by Battlefy id: standings of every stage, player lineups
    with deck codes and lineup archetype/class popularity. Unknown id: 404."""
    return _respond(
        TOURNAMENTS_SOURCE_ID,
        lambda structured: views.tournament(structured, tournament_id),
    )


@router.get(
    "/esports/points",
    summary="HSEsports points of a season",
    responses=examples.response(
        examples.POINTS, POINTS_SOURCE_ID, 227, limit=1, offset=0
    ),
)
def hsguru_esports_points(
    season: str | None = Query(
        None,
        max_length=64,
        description="Season key, e.g. `2026`, `2026_summer`, `china_2026_spring`; "
        "default: the latest whole year.",
    ),
    leaderboard: Literal["STD", "BG_LL"] = Query(
        "STD", description="`STD` (Standard) or `BG_LL` (Battlegrounds)."
    ),
    region: Literal["EU", "AM", "AP", "CN"] | None = Query(
        None, description="Only players of this region."
    ),
    limit: int = Query(500, ge=1, le=3000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """HSEsports points: rank, player (region, country, profile), one column
    per month or tournament of the season and the total."""
    return _respond(
        POINTS_SOURCE_ID,
        lambda structured: views.points(
            structured, season=season, leaderboard=leaderboard, region=region
        ),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/esports/points/seasons",
    summary="Collected HSEsports points seasons",
    responses=examples.response(examples.POINTS_SEASONS, POINTS_SOURCE_ID, 36),
)
def hsguru_esports_points_seasons() -> dict[str, Any]:
    """Every collected points season and leaderboard with its player count."""
    return _respond(POINTS_SOURCE_ID, views.points_seasons)


@router.get(
    "/esports/legacy/{table}",
    summary="Masters Tour archive table",
    responses=examples.response(
        examples.LEGACY, LEGACY_SOURCE_ID, 753, limit=1, offset=0
    ),
)
def hsguru_esports_legacy(
    table: Literal["mt-stats", "qualifier-stats", "mt-points"],
    selector: str | None = Query(
        None,
        max_length=64,
        description="qualifier-stats: period (`current`, `all`, a year or a tour "
        "stop; default `all`); mt-points: season such as `2022_fall` (default "
        "the newest). `data.options` lists the values.",
    ),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Archived Masters Tour tables: `mt-stats` (players across all tour stops),
    `qualifier-stats` (cups, top 8/16, winrate) and `mt-points` (legacy season
    points or earnings 2020–2022)."""
    return _respond(
        LEGACY_SOURCE_ID,
        lambda structured: views.legacy(structured, table, selector=selector),
        limit=limit,
        offset=offset,
    )
