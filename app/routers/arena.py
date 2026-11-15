from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Query

from ..config import stale_dataset_hours
from ..sources import SOURCE_BY_ID
from ..storage import load_dataset
from .models import (
    ApiMeta,
    ArenaCardsMeta,
    ArenaClassRow,
    Envelope,
    MetaEnvelope,
    timestamp_is_stale,
)

router = APIRouter(prefix="/v1/arena", tags=["v1-arena"])

ARENA_CLASS_SOURCES = {
    "hsreplay_arena",
    "hsreplay_arena_class_pages_firecrawl",
}

# The only admitted source of per-card Arena statistics on this route.
ARENA_CARDS_SOURCE_ID = "hsreplay_arena_cards_advanced"
ARENA_CARD_FIELDS: tuple[str, ...] = (
    "id",
    "dbfId",
    "name",
    "cost",
    "type",
    "rarity",
    "cardClass",
    "arena_class",
    "tier",
    "score",
    "win_rate",
    "deck_winrate",
    "winrate_when_drawn",
    "winrate_when_played",
    "popularity",
    "in_runs",
    "avg_copies",
    "times_played",
    "pick_rate",
    "offer_rate",
)
ARENA_CARD_DEFAULT_FIELDS = "id,score,tier,cardClass,win_rate"
_ARENA_GAME_TYPES = {
    "BGT_UNDERGROUND_ARENA": "underground",
    "BGT_ARENA": "normal",
}

ArenaCardsEnvelope = MetaEnvelope[list[dict[str, Any]], ArenaCardsMeta]


@router.get(
    "/classes",
    response_model=Envelope[list[ArenaClassRow]],
    response_model_exclude_none=True,
)
def classes(
    source_id: str = Query("hsreplay_arena_class_pages_firecrawl"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
) -> Envelope[list[ArenaClassRow]]:
    if source_id not in ARENA_CLASS_SOURCES:
        raise HTTPException(status_code=422, detail="Unsupported arena class source")
    dataset = load_dataset(source_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Arena class dataset is not cached")
    structured = ((dataset.get("data") or {}).get("structured") or {})
    all_rows = [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    rows = all_rows[offset : offset + limit]
    fetched_at = str(dataset.get("fetched_at")) if dataset.get("fetched_at") else None
    return Envelope(
        data=[ArenaClassRow.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id=source_id,
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=len(all_rows),
            limit=limit,
            offset=offset,
        ),
    )


def _selected_fields(raw: str) -> list[str]:
    fields: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name not in ARENA_CARD_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown field '{name[:40]}'; allowed: "
                    + ", ".join(ARENA_CARD_FIELDS)
                ),
            )
        if name not in fields:
            fields.append(name)
    if not fields:
        raise HTTPException(status_code=422, detail="fields must name at least one field")
    return fields


def _arena_slice(structured: dict[str, Any]) -> tuple[str | None, str | None]:
    """Game type and time range of the stored slice, read from the upstream query."""

    api_url = str((structured.get("source") or {}).get("api_url") or "")
    query = parse_qs(urlsplit(api_url).query)
    game_filter = (query.get("ArenaGameTypeFilter") or [None])[0]
    time_range = (query.get("ArenaTimestampRangeFilter") or [None])[0]
    selected = (structured.get("upstream_freshness") or {}).get("selected_params") or []
    for item in selected if isinstance(selected, list) else []:
        key, _, value = str(item).partition(".")
        if key == "ArenaGameTypeFilter" and value:
            game_filter = value
        elif key == "ArenaTimestampRangeFilter" and value:
            time_range = value
    return _ARENA_GAME_TYPES.get(str(game_filter or "")), time_range


@router.get(
    "/cards",
    response_model=ArenaCardsEnvelope,
    summary="Compact per-card Arena statistics",
    responses={
        404: {"description": "The Arena card dataset is not cached."},
        422: {"description": "Unknown field or class."},
    },
)
def cards(
    fields: str = Query(
        ARENA_CARD_DEFAULT_FIELDS,
        max_length=400,
        description="Comma-separated card fields: " + ", ".join(ARENA_CARD_FIELDS) + ".",
    ),
    card_class: str | None = Query(
        None,
        alias="class",
        pattern="^[A-Za-z]{1,20}$",
        description="Filter by the card's own class (`cardClass`), e.g. MAGE or NEUTRAL.",
    ),
    limit: int = Query(5000, ge=1, le=5000),
    offset: int = Query(0, ge=0, le=100_000),
) -> ArenaCardsEnvelope:
    """Rows of `hsreplay_arena_cards_advanced` cut down to the requested fields, so
    a client reads card scores without the full 1.7 MB dataset.

    `meta.game_type` is the Arena mode of the stored slice (`underground` or
    `normal`) and `meta.time_range` its HSReplay time filter; both are `null`
    when the dataset does not say. `score` and the winrates are `null` where
    HSReplay publishes no value for a card.
    """

    selected = _selected_fields(fields)
    dataset = load_dataset(ARENA_CARDS_SOURCE_ID)
    structured = ((dataset or {}).get("data") or {}).get("structured") or {}
    if dataset is None or structured.get("type") != "arena_card_tiers":
        raise HTTPException(status_code=404, detail="Arena card dataset is not cached")
    all_rows = [row for row in (structured.get("cards") or []) if isinstance(row, dict)]
    if card_class:
        wanted = card_class.upper()
        all_rows = [
            row for row in all_rows if str(row.get("cardClass") or "").upper() == wanted
        ]
    rows = all_rows[offset : offset + limit]
    fetched_at = str(dataset.get("fetched_at")) if dataset.get("fetched_at") else None
    game_type, time_range = _arena_slice(structured)
    return ArenaCardsEnvelope(
        data=[{name: row.get(name) for name in selected} for row in rows],
        meta=ArenaCardsMeta(
            source_id=ARENA_CARDS_SOURCE_ID,
            fetched_at=fetched_at,
            stale=timestamp_is_stale(
                fetched_at,
                max_age_hours=SOURCE_BY_ID[ARENA_CARDS_SOURCE_ID].stale_hours
                or stale_dataset_hours(),
            ),
            count=len(all_rows),
            limit=limit,
            offset=offset,
            game_type=game_type,
            time_range=time_range,
            fields=selected,
        ),
    )
