from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from urllib.parse import parse_qs, urlparse

from .models import Settings, now_iso, timestamp
from .store import Store

_scope: ContextVar[dict | None] = ContextVar("meta_lab_acquisition", default=None)


class BudgetExceeded(RuntimeError):
    pass


@contextmanager
def acquisition_scope(source_id: str, *, job_class: str = "critical", run_id: str | None = None):
    token = _scope.set({"source_id": source_id, "job_class": job_class, "run_id": run_id or uuid.uuid4().hex})
    try:
        yield _scope.get()
    finally:
        _scope.reset(token)


def source_scope(function):
    """Attach actual shared-provider source IDs, preserving a caller's job policy."""
    @wraps(function)
    def wrapped(source, *args, **kwargs):
        if _scope.get() is not None:
            return function(source, *args, **kwargs)
        with acquisition_scope(source.id):
            return function(source, *args, **kwargs)
    return wrapped


def opening_balance(store: Store, month: str, credits: int) -> None:
    datetime.strptime(month, "%Y-%m")
    if credits < 0:
        raise ValueError("Opening credits must be non-negative")
    with store.connect(write=True) as conn:
        conn.execute("INSERT INTO lab_meta(key,value) VALUES(?,?)", ("opening:"+month,str(credits)))


def _opening(conn, start: str) -> tuple[float, bool]:
    balance = conn.execute("SELECT value FROM lab_meta WHERE key=?", ("opening:"+start[:7],)).fetchone()
    tracking = conn.execute("SELECT value FROM lab_meta WHERE key='tracking_started_at'").fetchone()
    return (float(balance[0]) if balance else 0), bool(balance or tracking and timestamp(tracking[0]) <= timestamp(start))


def budget_state(spent: float, settings: Settings) -> str:
    if spent >= settings.hard:
        return "hard_limit_reached"
    if spent >= settings.throttle:
        return "deep_analysis_throttled"
    if spent >= settings.soft:
        return "soft_alert"
    return "normal"


def allowed(job_class: str, spent: float, settings: Settings) -> bool:
    return job_class == "critical" or spent < (settings.throttle if job_class in {"detail", "confirmation"} else settings.hard)


def _month(now: datetime) -> tuple[str, str]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def begin_attempt(url: str, *, store: Store | None = None, settings: Settings | None = None) -> str | None:
    settings = settings or Settings.from_env()
    parsed = urlparse(url)
    if not settings.enabled or parsed.hostname not in {"hsguru.com", "www.hsguru.com"}:
        return None
    store = store or Store()
    query = parse_qs(parsed.query)
    scope = _scope.get() or {}
    page = parsed.path.strip("/").split("/")[0] or "unknown"
    job = scope.get("job_class", "critical")
    identity = uuid.uuid4().hex
    started = now_iso()
    start, end = _month(timestamp(started))
    with store.connect(write=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        spent = conn.execute("SELECT COALESCE(SUM(COALESCE(credits, reserved)),0) FROM attempts WHERE started_at>=? AND started_at<?", (start, end)).fetchone()[0]
        opening, complete = _opening(conn, start)
        spent += opening
        if job != "critical" and not complete:
            raise BudgetExceeded("Opening monthly usage must be recorded before non-critical collection")
        if not allowed(job, spent + settings.reserve, settings):
            raise BudgetExceeded("HSGuru non-critical budget exhausted")
        conn.execute("INSERT OR IGNORE INTO lab_meta VALUES ('tracking_started_at', ?)", (started,))
        conn.execute("INSERT INTO attempts (id,run_id,source_id,page_type,format,rank,period,started_at,job_class,reserved,cost_basis,outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            identity, scope.get("run_id", identity), scope.get("source_id", f"hsguru:{page}"), page,
            {"1": "wild", "2": "standard"}.get(query.get("format", [""])[0], "unknown"),
            query.get("rank", ["unknown"])[0], query.get("period", ["unknown"])[0],
            started, job, settings.reserve, "unknown", "pending",
        ))
    return identity


def finish_attempt(identity: str | None, *, credits: float | None, basis: str,
                   duration_ms: float, outcome: str, http_status: int | None = None,
                   store: Store | None = None) -> None:
    if identity is None:
        return
    with (store or Store()).connect(write=True) as conn:
        conn.execute("UPDATE attempts SET finished_at=?,credits=?,cost_basis=?,duration_ms=?,outcome=?,http_status=? WHERE id=?", (
            now_iso(), credits, basis, duration_ms, outcome, http_status, identity,
        ))


def validate_attempt(identity: str | None, valid: bool, store: Store | None = None,
                     *, empty: bool = False) -> None:
    """Record the content verdict; ``empty`` marks a correct page without rows."""
    if identity:
        verdict = "valid" if valid else "empty" if empty else "invalid"
        with (store or Store()).connect(write=True) as conn:
            conn.execute("UPDATE attempts SET validation=? WHERE id=?", (verdict, identity))


def summary(store: Store, settings: Settings, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    start, end = _month(now)
    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM attempts WHERE started_at>=? AND started_at<?", (start, end))]
        tracking = conn.execute("SELECT value FROM lab_meta WHERE key='tracking_started_at'").fetchone()
        opening, complete = _opening(conn, start)
        snapshots = conn.execute("SELECT COUNT(*) FROM snapshots WHERE fetched_at>=? AND fetched_at<?", (start,end)).fetchone()[0]
        confirmed = conn.execute("SELECT COUNT(DISTINCT signal_id) FROM signal_events WHERE state='confirmed' AND at>=? AND at<?", (start,end)).fetchone()[0]
    reported = sum(r["credits"] or 0 for r in rows if r["cost_basis"] == "reported")
    estimated = sum(r["credits"] or 0 for r in rows if r["cost_basis"] == "estimated")
    reserved = sum(r["reserved"] for r in rows if r["credits"] is None)
    spent = opening + reported + estimated
    by_source, by_page = {}, {}
    for r in rows:
        cost = r["credits"] if r["credits"] is not None else r["reserved"]
        by_source[r["source_id"]] = by_source.get(r["source_id"], 0) + cost
        by_page[r["page_type"]] = by_page.get(r["page_type"], 0) + cost
    tracked_from = max(timestamp(start), timestamp(tracking[0])) if tracking else now
    hours = max(0, (now - tracked_from).total_seconds() / 3600)
    remaining_hours = max(0, (timestamp(end)-now).total_seconds()/3600)
    forecast = round(spent + reserved + (reported + estimated + reserved) / hours * remaining_hours) if hours >= 24 else None
    return {
        "state": budget_state(spent + reserved, settings), "month": start[:7],
        "monthly_spent_hsguru": spent, "reported_credits": reported,
        "estimated_credits": estimated, "reserved_or_unknown_credits": reserved,
        "budget_exposure": spent + reserved, "forecast_month_end": forecast,
        "tracking_started_at": tracking[0] if tracking else None,
        "month_complete": complete, "opening_credits": opening,
        "noncritical_collection_ready": complete,
        "cost_by_source": by_source, "cost_by_page_type": by_page,
        "cost_per_valid_snapshot": spent / snapshots if snapshots else None,
        "cost_per_confirmed_signal": spent / confirmed if confirmed else None,
        "requests": len(rows), "unknown_cost_requests": sum(r["credits"] is None for r in rows),
        "limits": {"soft": settings.soft, "throttle": settings.throttle, "hard": settings.hard},
        "critical_jobs_continue": True,
    }
