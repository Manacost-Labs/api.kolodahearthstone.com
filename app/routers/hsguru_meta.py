from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..hsguru_meta_matrix import MIN_GAMES, PERIODS, SOURCE_ID
from ..storage import load_dataset
from .models import ApiMeta, timestamp_is_stale


router = APIRouter(prefix="/v1/hsguru", tags=["v1-hsguru"])
ANALYSIS_SOURCE_ID = "hsguru_archetype_analysis"


@router.get("/meta")
def hsguru_meta(
    format_name: Literal["standard", "wild"] = Query("standard", alias="format"),
    rank: Literal[
        "all",
        "diamond",
        "diamond_4to1",
        "diamond_to_legend",
        "legend",
        "top_5k",
        "top_legend",
        "top_500",
        "top_100",
    ] = "all",
    period: str = Query("past_day", min_length=1, max_length=64),
    coin: Literal["any_player"] = "any_player",
    min_games: int = Query(100),
) -> dict:
    if min_games not in MIN_GAMES:
        raise HTTPException(status_code=422, detail={"allowed_min_games": MIN_GAMES})
    dataset = load_dataset(SOURCE_ID)
    if not dataset:
        raise HTTPException(status_code=503, detail="HSGuru meta matrix is not available yet")
    structured = ((dataset.get("data") or {}).get("structured") or {})
    allowed_periods = list(
        (structured.get("dimensions") or {}).get("periods") or PERIODS
    )
    if period not in allowed_periods:
        raise HTTPException(
            status_code=422,
            detail={"allowed_periods": allowed_periods},
        )
    key = "|".join((format_name, rank, period, coin))
    selected = next(
        (item for item in structured.get("slices") or [] if item.get("key") == key),
        None,
    )
    if selected is None:
        raise HTTPException(status_code=503, detail="Requested HSGuru meta slice is unavailable")
    items = [
        row for row in selected.get("rows") or []
        if int(row.get("games") or 0) >= min_games
    ]
    fetched_at = dataset.get("fetched_at") or structured.get("fetched_at")
    discovered_patch = str(
        (structured.get("patch_discovery") or {}).get("selected_period") or ""
    )
    if discovered_patch not in allowed_periods or not discovered_patch.startswith("patch_"):
        discovered_patch = next(
            (
                candidate
                for candidate in reversed(allowed_periods)
                if str(candidate).startswith("patch_")
            ),
            "",
        )
    response_meta = ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=fetched_at,
        stale=timestamp_is_stale(fetched_at, max_age_hours=36),
        count=len(items),
    ).model_dump(exclude_none=True)
    response_meta.update(
        {
            "available_periods": allowed_periods,
            "current_patch_period": discovered_patch or None,
        }
    )
    return {
        "data": {
            "format": format_name,
            "rank": rank,
            "period": period,
            "coin": coin,
            "min_games": min_games,
            "source_url": selected.get("source_url"),
            "items": items,
        },
        "meta": response_meta,
    }


@router.get("/archetypes")
def hsguru_current_archetypes(
    format_name: Literal["all", "standard", "wild"] = Query("all", alias="format"),
    q: str | None = Query(None, min_length=1, max_length=120),
    min_games: int = Query(50, ge=50),
    has_decks: bool | None = Query(
        None,
        description="Filter to archetypes with or without cached HSGuru builds",
    ),
    sort: Literal["games", "winrate", "popularity", "name"] = Query("games"),
    order: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
) -> dict[str, Any]:
    dataset = load_dataset(SOURCE_ID)
    if not dataset:
        raise HTTPException(status_code=503, detail="HSGuru meta matrix is not available yet")
    structured = ((dataset.get("data") or {}).get("structured") or {})
    catalog = structured.get("current_catalog") or {}
    if not catalog.get("archetypes"):
        raise HTTPException(status_code=503, detail="Current HSGuru catalog is unavailable")
    rows = [
        dict(row)
        for row in catalog.get("archetypes") or []
        if isinstance(row, dict)
        and int(row.get("games") or 0) >= min_games
        and (format_name == "all" or row.get("format") == format_name)
        and (q is None or q.casefold() in str(row.get("archetype") or "").casefold())
        and (has_decks is None or bool(row.get("decks")) is has_decks)
    ]
    sort_field = {
        "games": "games",
        "winrate": "winrate",
        "popularity": "popularity_pct",
        "name": "archetype",
    }[sort]

    def sort_key(row: dict[str, Any]) -> tuple[bool, Any]:
        value = row.get(sort_field)
        normalized = str(value).casefold() if sort == "name" else value
        return value is None, normalized if normalized is not None else 0

    rows.sort(key=sort_key, reverse=order == "desc")
    total = len(rows)
    fetched_at = dataset.get("fetched_at") or structured.get("fetched_at")
    return {
        "data": rows[offset : offset + limit],
        "criteria": catalog.get("criteria"),
        "coverage": catalog.get("coverage"),
        "meta": ApiMeta(
            source_id=SOURCE_ID,
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at, max_age_hours=36),
            count=total,
            limit=limit,
            offset=offset,
        ).model_dump(exclude_none=True),
    }


@router.get("/archetypes/history")
def hsguru_archetype_history(
    archetype: str = Query(..., min_length=1, max_length=120),
    format_name: Literal["standard", "wild"] = Query(..., alias="format"),
    limit: int = Query(180, ge=1, le=1000),
) -> dict[str, Any]:
    from ..db import get_db_connection

    conn = get_db_connection()
    try:
        exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='hsguru_archetype_history'
            """
        ).fetchone()
        rows = [] if not exists else [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM hsguru_archetype_history
                WHERE format = ? AND archetype = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (format_name, archetype, limit),
            ).fetchall()
        ]
    finally:
        conn.close()
    return {
        "data": rows,
        "meta": {
            "source_id": SOURCE_ID,
            "format": format_name,
            "archetype": archetype,
            "count": len(rows),
        },
    }
@router.get("/archetypes/analysis")
def hsguru_archetype_analysis(
    archetype: str = Query(..., min_length=1, max_length=120),
    format_name: Literal["standard", "wild"] = Query(..., alias="format"),
) -> dict[str, Any]:
    dataset = load_dataset(ANALYSIS_SOURCE_ID)
    if not dataset:
        raise HTTPException(
            status_code=503,
            detail="HSGuru archetype analysis is not available",
        )
    structured = ((dataset.get("data") or {}).get("structured") or {})
    if structured.get("type") != ANALYSIS_SOURCE_ID:
        raise HTTPException(
            status_code=503,
            detail="HSGuru archetype analysis has an invalid schema",
        )
    selected = next(
        (
            dict(row)
            for row in structured.get("archetypes") or []
            if isinstance(row, dict)
            and row.get("format") == format_name
            and str(row.get("archetype") or "").casefold() == archetype.casefold()
        ),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=404,
            detail="Archetype analysis is not available for the requested filters",
        )
    fetched_at = selected.get("updated_at") or dataset.get("fetched_at")
    return {
        "data": selected,
        "meta": ApiMeta(
            source_id=ANALYSIS_SOURCE_ID,
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at, max_age_hours=36),
            count=1,
        ).model_dump(exclude_none=True),
    }
