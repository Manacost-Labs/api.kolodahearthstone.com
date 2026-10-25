from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from ..balance_radar import store
from ..balance_radar.history import load_history
from .models import ApiMeta, timestamp_is_stale

router = APIRouter(prefix="/v1/constructed/balance-radar", tags=["v1-constructed"])
SOURCE_ID = "balance_radar"
# Two scheduled runs a day; a day and a half without one is stale.
MAX_AGE_HOURS = 36
# The history is rebuilt weekly or when a new patch appears.
HISTORY_MAX_AGE_HOURS = 8 * 24


def _meta(
    *,
    fetched_at: str | None,
    count: int,
    max_age_hours: float = MAX_AGE_HOURS,
    **extra: Any,
) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=fetched_at,
        stale=timestamp_is_stale(fetched_at, max_age_hours=max_age_hours),
        count=count,
        **extra,
    ).model_dump(exclude_none=True)


@router.get("")
def latest_balance_radar(
    format_name: Literal["standard", "wild"] = Query("standard", alias="format"),
) -> dict[str, Any]:
    """Latest successful nerf/buff radar run for one format."""
    try:
        latest = store.latest_ok(format_name)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Balance Radar store is unavailable"
        ) from exc
    if not latest or not latest.get("payload"):
        raise HTTPException(
            status_code=503, detail="Balance Radar has no successful run yet"
        )
    payload = latest["payload"]
    return {
        "data": payload,
        "meta": _meta(
            fetched_at=payload.get("generatedAt"),
            count=len(payload.get("nerfs") or []) + len(payload.get("buffs") or []),
        ),
    }


@router.get("/runs")
def balance_radar_runs(
    format_name: Literal["standard", "wild"] | None = Query(None, alias="format"),
    status: Literal["ok", "failed", "skipped"] | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10_000),
) -> dict[str, Any]:
    """Run history, newest first, without the full payloads."""
    rows, total = store.list_runs(
        format_name=format_name, status=status, limit=limit, offset=offset
    )
    for row in rows:
        row.pop("inputHash", None)
    latest = next((row["generatedAt"] for row in rows if row["status"] == "ok"), None)
    return {
        "data": rows,
        "meta": _meta(fetched_at=latest, count=total, limit=limit, offset=offset),
    }


@router.get("/runs/{run_id}")
def balance_radar_run(
    run_id: str = Path(..., min_length=8, max_length=80, pattern=r"^[0-9A-Za-z-]+$"),
) -> dict[str, Any]:
    """One stored run; a successful one carries the same payload as the latest endpoint."""
    row = store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Balance Radar run not found")
    payload = row.pop("payload")
    row.pop("usage", None)
    row.pop("inputHash", None)
    return {
        "data": payload,
        "run": row,
        "meta": _meta(
            fetched_at=row.get("generatedAt") or row.get("finishedAt"),
            count=1 if payload else 0,
        ),
    }


def _history_or_503() -> dict[str, Any]:
    history = load_history()
    if not history:
        raise HTTPException(
            status_code=503, detail="Balance history has not been built yet"
        )
    return history


@router.get("/history")
def balance_history(
    card: str | None = Query(
        None,
        min_length=1,
        max_length=120,
        description="cardId, dbfId or part of a name",
    ),
    direction: Literal["nerf", "buff", "rework", "neutral", "mixed"] | None = Query(
        None
    ),
    card_class: str | None = Query(None, alias="class", min_length=3, max_length=20),
    since: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
) -> dict[str, Any]:
    """Past balance patches with exact card changes, newest first."""
    history = _history_or_503()
    needle = (card or "").casefold()

    def keep(change: dict[str, Any]) -> bool:
        if direction and change.get("direction") != direction:
            return False
        if (
            card_class
            and str(change.get("class") or "").casefold() != card_class.casefold()
        ):
            return False
        if needle and needle not in {
            str(change.get("cardId")).casefold(),
            str(change.get("dbfId")),
        }:
            names = (
                f"{change.get('name') or ''} {change.get('nameEn') or ''}".casefold()
            )
            if needle not in names:
                return False
        return True

    patches = []
    for patch in history.get("patches") or []:
        if since and patch["date"] < since:
            continue
        changes = [change for change in patch["changes"] if keep(change)]
        if (card or direction or card_class) and not changes:
            continue
        patches.append({**patch, "changes": changes})
    page = patches[offset : offset + limit]
    return {
        "data": {
            "generatedAt": history.get("generatedAt"),
            "coverage": history.get("coverage"),
            "contentDrops": history.get("contentDrops") or [],
            "patches": page,
        },
        "meta": _meta(
            fetched_at=history.get("generatedAt"),
            count=len(patches),
            max_age_hours=HISTORY_MAX_AGE_HOURS,
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/patterns")
def balance_patterns() -> dict[str, Any]:
    """How Blizzard has balanced the game: thresholds, change types, timing, narrative."""
    history = _history_or_503()
    return {
        "data": {
            "generatedAt": history.get("generatedAt"),
            "coverage": history.get("coverage"),
            "patterns": history.get("patterns"),
            "narrative": history.get("narrative"),
            "model": history.get("model"),
        },
        "meta": _meta(
            fetched_at=history.get("generatedAt"),
            count=int((history.get("patterns") or {}).get("balancePatches") or 0),
            max_age_hours=HISTORY_MAX_AGE_HOURS,
        ),
    }
