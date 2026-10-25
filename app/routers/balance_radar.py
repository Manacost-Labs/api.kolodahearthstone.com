from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from ..balance_radar import store
from .models import ApiMeta, timestamp_is_stale

router = APIRouter(prefix="/v1/constructed/balance-radar", tags=["v1-constructed"])
SOURCE_ID = "balance_radar"
# Two scheduled runs a day; a day and a half without one is stale.
MAX_AGE_HOURS = 36


def _meta(*, fetched_at: str | None, count: int, **extra: Any) -> dict[str, Any]:
    return ApiMeta(
        source_id=SOURCE_ID,
        fetched_at=fetched_at,
        stale=timestamp_is_stale(fetched_at, max_age_hours=MAX_AGE_HOURS),
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
