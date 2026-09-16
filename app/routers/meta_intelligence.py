from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..meta_intelligence import budget, service
from ..meta_intelligence.freshness import with_freshness
from ..meta_intelligence.models import Settings
from ..meta_intelligence.selectors import hot_jobs
from ..meta_intelligence.signals import KINDS
from ..meta_intelligence.store import Store


def enabled() -> None:
    if not Settings.from_env().enabled:
        raise HTTPException(503, "Meta Intelligence Lab is disabled")


# Existing require_admin is attached in main at registration, to avoid cycles.
router = APIRouter(prefix="/v1/meta-intelligence", tags=["meta-intelligence"], dependencies=[Depends(enabled)])


def selected_scope(format: Literal["standard", "wild"] = "standard",
                   rank: str = Query("legend", max_length=40),
                   period: str = Query("past_week", max_length=60),
                   patch: str = Query("unknown", max_length=60)) -> dict:
    return {"format": format, "rank": rank, "period": period, "patch": patch}


@router.get("/health")
def health() -> dict:
    store = Store()
    settings = Settings.from_env()
    available = service.scopes(store)
    with store.connect() as conn:
        demo = conn.execute("SELECT value FROM lab_meta WHERE key='demo'").fetchone()
        latest = conn.execute("""SELECT * FROM (SELECT *,ROW_NUMBER() OVER(
            PARTITION BY source_id,format,rank,period ORDER BY fetched_at DESC,id DESC) AS ordinal FROM snapshots) WHERE ordinal=1""").fetchall()
    slices = []
    for row in latest:
        snap = with_freshness(store, dict(row))
        slices.append({**{k: snap[k] for k in ("source_id", "format", "rank", "period", "patch", "fetched_at")}, **snap["freshness"]})
    known = {s["source_id"] for s in slices}
    for job in hot_jobs(0, settings):
        if job.source_id not in known:
            slices.append({"source_id": job.source_id, "format": job.format, "rank": job.rank, "period": job.period,
                           "state": "missing", "fresh": False, "reasons": ["no_valid_snapshot"]})
    return {"state": "ok" if slices and all(s["fresh"] for s in slices) else "incomplete", "scopes": available, "demo": bool(demo),
            "slices": slices, "mode": "internal_preview", "schema_version": 1,
            "capabilities": {"card_metrics": "archetype", "matchups": "source_dependent", "deck_coverage": "source_limited", "causal_card_effects": False}}


@router.get("/budget")
def budget_status() -> dict:
    return budget.summary(Store(), Settings.from_env())


@router.get("/archetypes")
def list_archetypes(scope: dict = Depends(selected_scope), q: str = Query("", max_length=120),
                    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0, le=10000)) -> dict:
    rows = [r for r in service.archetypes(Store(), **scope) if q.casefold() in r["archetype"].casefold()]
    return {"data": rows[offset:offset+limit], "total": len(rows), "scope": scope}


@router.get("/archetypes/{archetype}/timeline")
def archetype_timeline(archetype: str, scope: dict = Depends(selected_scope), limit: int = Query(180, ge=1, le=1000)) -> dict:
    return {"data": service.timeline(Store(), archetype, limit=limit, **scope), "scope": scope}


@router.get("/archetypes/{archetype}/compare")
def compare_decks(archetype: str, left: str = Query(..., min_length=64, max_length=64),
                  right: str = Query(..., min_length=64, max_length=64), scope: dict = Depends(selected_scope)) -> dict:
    data = service.compare(Store(), archetype, left, right, **scope)
    if data is None:
        raise HTTPException(404, "Decks not found in the selected archetype/slice")
    return data


@router.get("/archetypes/{archetype}")
def archetype_detail(archetype: str, scope: dict = Depends(selected_scope)) -> dict:
    data = service.detail(Store(), archetype, **scope)
    if data is None:
        raise HTTPException(404, "Archetype not found in the selected slice")
    return data


@router.get("/cards")
def cards(scope: dict = Depends(selected_scope), q: str = Query("", max_length=120)) -> dict:
    return {"data": [r for r in service.card_catalog(Store(), **scope) if q.casefold() in r["name"].casefold()], "scope": scope}


def _signal_view(row: dict, store: Store) -> dict:
    row["evidence"] = json.loads(row["evidence"])
    ids = row["evidence"].get("snapshot_ids", [])
    row["historical_state"] = row["state"]
    row["evidence_fresh"] = False
    row["evidence_patch_verified"] = False
    if ids:
        with store.connect() as conn:
            last = conn.execute("SELECT * FROM snapshots WHERE id=?", (ids[-1],)).fetchone()
        if last:
            row["evidence_fresh"] = with_freshness(store, dict(last))["freshness"]["fresh"]
            row["evidence_patch_verified"] = json.loads(last["payload"]).get("patch_scope") == "single_patch"
    if (not row["evidence_fresh"] and row["state"] in {"confirmed", "early"}) or (row["state"] == "confirmed" and not row["evidence_patch_verified"]):
        row["state"] = "observing"
        row["confidence"] = 0.25
        row["reason"] = "Доказательства устарели или границы патча не проверены; прежнее состояние сохранено в истории."
    return row


@router.get("/signals")
def signals(format: Literal["standard", "wild"] | None = None, rank: str | None = Query(None, max_length=40),
            period: str | None = Query(None, max_length=60), patch: str | None = Query(None, max_length=60),
            state: Literal["observing", "early", "confirmed", "rejected"] | None = None,
            kind: str | None = Query(None, max_length=40), archetype: str | None = Query(None, max_length=120),
            since: datetime | None = None, until: datetime | None = None,
            limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0, le=10000)) -> dict:
    if kind is not None and kind not in KINDS:
        raise HTTPException(422, "Unknown signal kind")
    if any(v and v.tzinfo is None for v in (since,until)) or since and until and since > until:
        raise HTTPException(422, "Use ordered timezone-aware dates")
    clauses, args = [], []
    for key, value in (("format",format),("rank",rank),("period",period),("patch",patch),("kind",kind),("archetype",archetype)):
        if value is not None:
            clauses.append(f"{key}=?")
            args.append(value)
    for op, value in ((">=",since),("<=",until)):
        if value is not None:
            clauses.append(f"detected_at {op} ?")
            args.append(value.astimezone(UTC).isoformat())
    store = Store()
    with store.connect() as conn:
        records = conn.execute("SELECT * FROM signals" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY detected_at DESC,id", args).fetchall()
    rows = [_signal_view(dict(r),store) for r in records]
    if state:
        rows = [r for r in rows if r["state"] == state]
    return {"data": rows[offset:offset+limit], "total": len(rows)}


@router.get("/signals/{signal_id}")
def signal_detail(signal_id: str) -> dict:
    store = Store()
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        events = [dict(r) for r in conn.execute("SELECT at,state,evidence FROM signal_events WHERE signal_id=? ORDER BY at", (signal_id,))]
    if not row:
        raise HTTPException(404, "Signal not found")
    return {**_signal_view(dict(row), store), "history": [{**e, "evidence": json.loads(e["evidence"])} for e in events]}


@router.get("/snapshots/{snapshot_id}")
def snapshot_detail(snapshot_id: str) -> dict:
    store = Store()
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot not found")
    return with_freshness(store, {**dict(row), "payload": json.loads(row["payload"])})
