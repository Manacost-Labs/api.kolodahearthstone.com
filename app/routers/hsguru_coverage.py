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


@router.get("/brawl/meta")
def hsguru_brawl_meta(
    rank: BrawlRank = "all",
    coin: Coin = "any_player",
    opponent_class: str = Query("any", max_length=16),
    min_games: int = Query(10, ge=0, le=1_000_000),
) -> dict[str, Any]:
    """Archetype winrate, popularity and games of the current Tavern Brawl."""
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


@router.get("/brawl/decks")
def hsguru_brawl_decks(
    rank: BrawlDeckRank = "all",
    class_name: str | None = Query(None, alias="class", max_length=32),
    archetype: str | None = Query(None, max_length=120),
) -> dict[str, Any]:
    """Most played decks of the current Tavern Brawl with deck codes."""
    return _respond(
        BRAWL_SOURCE_ID,
        lambda structured: views.brawl_decks(
            structured, rank=rank, class_name=class_name, archetype=archetype
        ),
    )


@router.get("/esports/upcoming")
def hsguru_esports_upcoming() -> dict[str, Any]:
    """Upcoming and running tournaments listed on HSGuru /esports."""
    return _respond(
        TOURNAMENTS_SOURCE_ID,
        lambda structured: {"items": list(structured.get("upcoming") or [])},
    )


@router.get("/esports/tournaments")
def hsguru_esports_tournaments(
    kind: Literal["tour_stop", "community"] | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Tournament summaries: winner, stages, players and top lineup archetypes."""
    return _respond(
        TOURNAMENTS_SOURCE_ID,
        lambda structured: views.tournament_summaries(structured, kind=kind),
        limit=limit,
        offset=offset,
    )


@router.get("/esports/tournaments/{tournament_id}")
def hsguru_esports_tournament(tournament_id: str) -> dict[str, Any]:
    """One tournament: standings of every stage, lineups and archetype popularity."""
    return _respond(
        TOURNAMENTS_SOURCE_ID,
        lambda structured: views.tournament(structured, tournament_id),
    )


@router.get("/esports/points")
def hsguru_esports_points(
    season: str | None = Query(None, max_length=64),
    leaderboard: Literal["STD", "BG_LL"] = "STD",
    region: Literal["EU", "AM", "AP", "CN"] | None = None,
    limit: int = Query(500, ge=1, le=3000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """HSEsports points of one season and leaderboard (default: current year)."""
    return _respond(
        POINTS_SOURCE_ID,
        lambda structured: views.points(
            structured, season=season, leaderboard=leaderboard, region=region
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/esports/points/seasons")
def hsguru_esports_points_seasons() -> dict[str, Any]:
    """Every collected points season and leaderboard with its player count."""
    return _respond(POINTS_SOURCE_ID, views.points_seasons)


@router.get("/esports/legacy/{table}")
def hsguru_esports_legacy(
    table: Literal["mt-stats", "qualifier-stats", "mt-points"],
    selector: str | None = Query(
        None,
        max_length=64,
        description="qualifier-stats: period (all, 2021, a tour stop); mt-points: season.",
    ),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Archived Masters Tour statistics, qualifier statistics and season points."""
    return _respond(
        LEGACY_SOURCE_ID,
        lambda structured: views.legacy(structured, table, selector=selector),
        limit=limit,
        offset=offset,
    )
