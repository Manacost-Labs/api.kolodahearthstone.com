from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsguru_deck_radar import SOURCE_ID as DECK_RADAR_SOURCE_ID
from ..hsguru_deck_radar import list_deck_radar_events
from ..hsguru_decks import exact_hsguru_decks
from .models import (
    ApiMeta,
    ArchetypeRow,
    DeckRadarEventRow,
    DeckRow,
    Envelope,
    freshest_timestamp,
    timestamp_is_stale,
)

router = APIRouter(prefix="/v1/constructed", tags=["v1-constructed"])


@router.get(
    "/hsguru-deck",
    response_model=Envelope[list[DeckRow]],
    response_model_exclude_none=True,
)
async def hsguru_deck(
    archetype: str = Query(..., min_length=2, max_length=120),
    format_name: str = Query("standard", pattern="^(standard|wild)$"),
    rank: str = Query(
        "legend", pattern="^(legend|diamond_4to1|top_5k|top_legend|all)$"
    ),
) -> Envelope[list[DeckRow]]:
    try:
        rows = await exact_hsguru_decks(archetype, format_name, rank)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="HSGuru deck lookup failed"
        ) from exc
    if not rows:
        raise HTTPException(
            status_code=404, detail="Exact HSGuru archetype deck not found"
        )
    fetched_at = freshest_timestamp(rows, "updated_at")
    return Envelope(
        data=[DeckRow.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="hsguru_decks",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=len(rows),
            limit=len(rows),
            offset=0,
        ),
    )


@router.get(
    "/decks",
    response_model=Envelope[list[DeckRow]],
    response_model_exclude_none=True,
)
def decks(
    class_name: str | None = Query(None, min_length=1, max_length=80),
    format_name: str | None = Query(None, min_length=1, max_length=80),
    source_id: str | None = Query(None, min_length=1, max_length=120),
    min_win_rate: float | None = None,
    q: str | None = Query(None, min_length=1, max_length=120),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[DeckRow]]:
    from ..db import get_db_connection

    conn = get_db_connection()
    try:
        query = "SELECT * FROM decks WHERE 1=1"
        params: list[object] = []
        if class_name:
            query += " AND LOWER(class) = LOWER(?)"
            params.append(class_name)
        if format_name:
            query += " AND LOWER(format) = LOWER(?)"
            params.append(format_name)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        else:
            query += " AND source_id != ?"
            params.append("hsreplay_arena_winning_decks")
        if min_win_rate is not None:
            query += " AND win_rate >= ?"
            params.append(min_win_rate)
        if q:
            query += " AND (title LIKE ? OR archetype LIKE ? OR deck_code LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        total = int(
            conn.execute(
                query.replace("SELECT *", "SELECT COUNT(*)", 1), params
            ).fetchone()[0]
        )
        rows = [
            dict(row)
            for row in conn.execute(
                query + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Database query failed") from exc
    finally:
        conn.close()
    fetched_at = freshest_timestamp(rows, "updated_at", "fetched_at")
    return Envelope(
        data=[DeckRow.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id=source_id or "multiple",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=total,
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/deck-radar",
    response_model=Envelope[list[DeckRadarEventRow]],
    response_model_exclude_none=True,
)
def deck_radar(
    status: Literal["candidate", "confirmed"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[DeckRadarEventRow]]:
    """Return exact deckstrings newly observed after the Radar baseline."""
    try:
        payload = list_deck_radar_events(status=status, limit=limit, offset=offset)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Deck Radar database query failed"
        ) from exc
    fetched_at = payload.get("fetched_at")
    return Envelope(
        data=[DeckRadarEventRow.model_validate(row) for row in payload["events"]],
        meta=ApiMeta(
            source_id=DECK_RADAR_SOURCE_ID,
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at, max_age_hours=2),
            beta=True,
            count=int(payload["total"]),
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/archetypes",
    response_model=Envelope[list[ArchetypeRow]],
    response_model_exclude_none=True,
)
def archetypes(
    class_name: str | None = Query(None, min_length=1, max_length=80),
    q: str | None = Query(None, min_length=1, max_length=120),
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[ArchetypeRow]]:
    from ..hsreplay_archetypes_db import list_archetype_snapshots

    payload = list_archetype_snapshots(
        rank_range=rank_range,
        game_type=game_type,
        class_name=class_name,
        q=q,
        limit=limit,
        offset=offset,
    )
    rows = list(payload.get("archetypes") or [])
    fetched_at = freshest_timestamp(rows, "fetched_at", "as_of_popularity")
    return Envelope(
        data=[ArchetypeRow.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="hsreplay_archetypes",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=int(payload.get("total") or 0),
            limit=limit,
            offset=offset,
        ),
    )
