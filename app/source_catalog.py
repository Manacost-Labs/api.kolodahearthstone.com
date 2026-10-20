"""Cheap source inventory with publication selection and explicit freshness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .catalog_metadata import load_catalog_metadata
from .config import source_operationally_enabled, stale_dataset_hours
from .parser_control import (
    _is_provisional_dataset,
    effective_publication_mode,
    parser_control_store,
)
from .post_patch_policy import (
    POST_PATCH_BASELINE_LABEL,
    STABLE_PUBLICATION_BASELINE_LABEL,
)
from .sources import Source
from .storage import dataset_path, load_status


def stable_catalog_metadata(source_id: str, store: Any) -> dict[str, Any] | None:
    for label in (STABLE_PUBLICATION_BASELINE_LABEL, POST_PATCH_BASELINE_LABEL):
        baseline = load_catalog_metadata(
            store.root / "baselines" / f"{source_id}.{label}.json"
        )
        if baseline:
            return baseline
    return None


def public_catalog_metadata(source_id: str) -> dict[str, Any] | None:
    if not source_operationally_enabled(source_id):
        return None
    projection = load_catalog_metadata(dataset_path(source_id))
    if projection is None or not _is_provisional_dataset(projection):
        return projection
    if effective_publication_mode(source_id) == "early":
        return projection
    store = parser_control_store()
    baseline = stable_catalog_metadata(source_id, store)
    return None if baseline is None or _is_provisional_dataset(baseline) else baseline


def _age(timestamp: Any, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return (now - parsed.astimezone(UTC)).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def next_source_runs(now: datetime) -> dict[str, str]:
    from .parser_control_schedule import build_schedule_inventory

    result: dict[str, str] = {}
    # No subprocess/systemd call on an inventory request. Report nominal timing.
    inventory = build_schedule_inventory(at=now, include_runtime=False)
    for row in inventory.get("schedules", []):
        next_run = row.get("nextRunAt")
        if not row.get("isActive") or row.get("purpose") == "recovery" or not next_run:
            continue
        for source_id in row.get("sourceIds", []):
            result[source_id] = min(result.get(source_id, next_run), next_run)
    return result


def upstream_pending_status(source_id: str, status: dict[str, Any]) -> bool:
    from .reliability_telemetry import classify_independently_ineligible_reason

    return bool(
        source_id == "vicious_syndicate_radars"
        and status.get("serving_cached_dataset")
        and (status.get("last_refresh_upstream_state") or status.get("upstream_state"))
        == "upstream_publication_pending"
        and (
            status.get("cached_content_temporally_grandfathered") is True
            or classify_independently_ineligible_reason(status)
            == "upstream_not_published"
        )
    )


def source_summary(
    source: Source,
    *,
    now: datetime,
    next_runs: dict[str, str],
    projection: dict[str, Any] | None,
) -> dict[str, Any]:
    status = load_status(source.id) or {}
    enabled = source_operationally_enabled(source.id)
    fetched_at = projection.get("fetched_at") if projection else None
    age = _age(fetched_at, now)
    ttl = source.stale_hours or stale_dataset_hours()
    pending = upstream_pending_status(source.id, status)
    stale = age is None or age < 0 or age > ttl
    freshness = (
        "disabled"
        if not enabled
        else "upstream_pending"
        if pending
        else "missing"
        if projection is None
        else "stale"
        if stale
        else "provisional"
        if _is_provisional_dataset(projection)
        else "fresh"
    )
    from .source_diagnostics import failure_diagnostics

    return {
        "enabled": enabled,
        "freshness_state": freshness,
        "stale": stale,
        "age_hours": round(age, 2) if age is not None else None,
        "stale_after_hours": ttl,
        "last_success_at": fetched_at,
        "last_attempt_at": status.get("last_refresh_at")
        or status.get("checked_at")
        or status.get("fetched_at"),
        "next_run_at": next_runs.get(source.id) if enabled else None,
        "next_run_basis": "nominal",
        "diagnostics": failure_diagnostics(source.id, status),
        "completeness": (projection or {}).get("completeness")
        or {
            "state": "unknown",
            "population_state": "unknown",
            "expected": None,
            "received": None,
            "verified_empty": None,
            "unverified": None,
        },
    }
