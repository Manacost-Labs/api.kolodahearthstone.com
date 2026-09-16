from __future__ import annotations

from datetime import UTC, datetime

from .models import Settings, timestamp
from .store import Store


def evaluate(snapshot: dict | None, *, latest_observation: dict | None = None,
             settings: Settings | None = None, now: datetime | None = None) -> dict:
    settings = settings or Settings.from_env()
    if not snapshot:
        return {"state": "missing", "fresh": False, "age_hours": None, "reasons": ["no_valid_snapshot"]}
    now = now or datetime.now(UTC)
    age = (now - timestamp(snapshot["fetched_at"])).total_seconds() / 3600
    reasons = []
    if age < 0:
        reasons.append("future_timestamp")
    if age > snapshot["expected_hours"]:
        reasons.append("expected_interval_exceeded")
    if snapshot["coverage"] < settings.coverage_min:
        reasons.append("low_coverage")
    if latest_observation and not latest_observation["valid"] and timestamp(latest_observation["checked_at"]) >= timestamp(snapshot["fetched_at"]):
        reasons.extend(["latest_source_failure", latest_observation["reason"]])
    return {"state": "cached" if "latest_source_failure" in reasons else "stale" if reasons else "fresh",
            "fresh": not reasons, "age_hours": round(age, 2), "expected_hours": snapshot["expected_hours"], "reasons": reasons}


def with_freshness(store: Store, snapshot: dict, *, now: datetime | None = None) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM observations WHERE source_id=? AND format=? AND rank=? AND period=? ORDER BY checked_at DESC,id DESC LIMIT 1", tuple(snapshot[k] for k in ("source_id", "format", "rank", "period"))).fetchone()
    return {**snapshot, "freshness": evaluate(snapshot, latest_observation=dict(row) if row else None, now=now)}
