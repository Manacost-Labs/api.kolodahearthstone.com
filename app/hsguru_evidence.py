"""Bounded acquisition evidence, not proof of upstream freshness or completeness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_STATES = frozenset(
    {
        "complete",
        "sparse_valid",
        "cached",
        "error",
        "source_no_data",
        "upstream_card_tallies_missing",
        "missing",
    }
)


def _timestamp(value: Any, now: datetime) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > now:
            return None
        return (
            parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
    except (ValueError, OverflowError):
        return None


def hsguru_data_evidence(
    dataset: dict[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Summarize only trusted fields; never substitute a last attempt for dataset time.

    Component timestamps are local observations. Even complete component states
    cannot prove a complete upstream catalogue, selected patch, or data age.
    """
    now = now or datetime.now(UTC)
    data = (dataset or {}).get("data")
    structured = data.get("structured") if isinstance(data, dict) else None
    structured = structured if isinstance(structured, dict) else {}
    report: dict[str, Any] = {
        "schema_version": 1,
        "has_dataset": dataset is not None,
        "collection": {
            "fetched_at": _timestamp((dataset or {}).get("fetched_at"), now)
        },
        "upstream": {
            "status": "unknown",
            "as_of": None,
            "reason": "adapter_has_no_verified_upstream_timestamp",
        },
        "coverage": {
            "status": "unknown",
            "scope": "not_measured",
            "observed_archetypes": 0,
        },
        "components": [],
    }
    rows = structured.get("archetypes")
    if (
        structured.get("type") != "hsguru_archetype_analysis"
        or not isinstance(rows, list)
        or not rows
    ):
        return report
    partial = False
    for name in ("matchups", "card_stats"):
        counts: dict[str, int] = {}
        checked: list[str] = []
        updated: list[str] = []
        for row in rows:
            row = row if isinstance(row, dict) else {}
            components = row.get("components")
            # A present but malformed nested component must not promote legacy data.
            if "components" in row:
                component = (
                    components.get(name) if isinstance(components, dict) else None
                )
                component = component if isinstance(component, dict) else {}
            else:
                component = {
                    key: row.get(f"{name}_{key}")
                    for key in ("state", "checked_at", "updated_at")
                }
            state = component.get("state")
            state = state if isinstance(state, str) and state in _STATES else "unknown"
            counts[state] = counts.get(state, 0) + 1
            checked_at = _timestamp(component.get("checked_at"), now)
            updated_at = _timestamp(component.get("updated_at"), now)
            if checked_at:
                checked.append(checked_at)
            if updated_at:
                updated.append(updated_at)
            partial |= (
                state not in {"complete", "sparse_valid"}
                or not checked_at
                or not updated_at
            )
        report["components"].append(
            {
                "name": name,
                "entities_total": len(rows),
                "state_counts": counts,
                "oldest_checked_at": min(checked) if checked else None,
                "oldest_updated_at": min(updated) if updated else None,
                "missing_checked_at_count": len(rows) - len(checked),
                "missing_updated_at_count": len(rows) - len(updated),
            }
        )
    report["coverage"] = {
        "status": "partial" if partial else "reported",
        "scope": "observed_archetype_components",
        "observed_archetypes": len(rows),
    }
    return report
