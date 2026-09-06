"""Bounded, sanitized acquisition snapshots for the read-only admin panel."""

from datetime import UTC, datetime, timedelta

from .acquisition_coverage import DETAIL_STATES, coverage_report, source_view
from .sources import SOURCE_BY_ID, SOURCES

MAX_REPORT_BYTES = 1024 * 1024
MAX_SAFE_INTEGER = 2**53 - 1


def _count(value: object, maximum: int = MAX_SAFE_INTEGER) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _timestamp(value: object, now: datetime) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid observation timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > now + timedelta(seconds=5):
            raise ValueError("invalid observation timestamp")
    except (ValueError, OverflowError):
        raise ValueError("invalid observation timestamp") from None
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _coverage(raw: object) -> dict:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("source_id"), str)
        or raw["source_id"] not in SOURCE_BY_ID
    ):
        raise ValueError("unknown acquisition source")
    if any(
        type(raw.get(key)) is not bool for key in ("listing_complete", "view_confirmed")
    ):
        raise ValueError("invalid acquisition evidence flags")
    source = SOURCE_BY_ID[raw["source_id"]]
    view = source_view(
        source, snapshot_id=raw.get("snapshot_id"), patch_id=raw.get("patch_id")
    )
    result = coverage_report(view)
    if any(
        raw.get(key) != result[key]
        for key in ("site", "kind", "scope_id", "query", "fragment")
    ):
        raise ValueError("mismatched acquisition scope")
    found = raw.get("found")
    if found is None:
        if any(raw.get(key) != result[key] for key in result):
            raise ValueError("contradictory unknown coverage")
        return result
    expected, counts = raw.get("expected_count"), raw.get("details")
    if (
        not _count(found, 100_000)
        or (expected is not None and (not _count(expected) or expected < found))
        or not isinstance(counts, dict)
        or set(counts) != set(DETAIL_STATES)
        or any(not _count(value, found) for value in counts.values())
        or sum(counts.values()) != found
        or type(raw.get("listing_complete")) is not bool
        or type(raw.get("view_confirmed")) is not bool
        or (raw["listing_complete"] and expected is not None and found != expected)
    ):
        raise ValueError("inconsistent acquisition counts")
    unresolved = found - counts["succeeded"] - counts["absent"]
    status = (
        "complete_for_view"
        if raw["listing_complete"] and raw["view_confirmed"] and unresolved == 0
        else "partial"
    )
    percent = round(100 * found / expected, 2) if expected else None
    if (
        raw.get("status") != status
        or not _count(raw.get("unresolved_details"), found)
        or raw["unresolved_details"] != unresolved
        or raw.get("listing_percent") != percent
        or isinstance(raw.get("listing_percent"), bool)
    ):
        raise ValueError("contradictory acquisition coverage")
    result.update(
        status=status,
        found=found,
        expected_count=expected,
        details=dict(counts),
        unresolved_details=unresolved,
        listing_percent=percent,
        listing_complete=raw["listing_complete"],
        view_confirmed=raw["view_confirmed"],
    )
    return result


def build_panel_snapshot(
    observations: list[dict], *, now: datetime | None = None
) -> dict:
    """Consume trusted adapters' reports; never read queues, payloads or network.

    Each observation wraps coverage_report or bg_detail_report with observed_at.
    Only declared fields survive export; omitted catalog sources stay unknown.
    """
    now = now or datetime.now(UTC)
    if (
        now.tzinfo is None
        or not isinstance(observations, list)
        or len(observations) > len(SOURCES)
    ):
        raise ValueError("invalid acquisition snapshot")
    observed = {}
    for item in observations:
        if not isinstance(item, dict):
            raise TypeError("invalid acquisition observation")
        report = _coverage(item.get("coverage"))
        source_id = report["source_id"]
        if source_id in observed:
            raise ValueError("duplicate source observation")
        credits, unknown = item.get("credits_spent"), item.get("unknown_cost_attempts")
        if any(value is not None and not _count(value) for value in (credits, unknown)):
            raise ValueError("invalid request cost evidence")
        observed[source_id] = {
            "coverage": report,
            "observed_at": _timestamp(item.get("observed_at"), now),
            "credits_spent": credits,
            "unknown_cost_attempts": unknown,
        }
    rows = []
    for source in SOURCES:
        rows.append(
            {
                "source_id": source.id,
                "site": source.site,
                "kind": source.kind,
                "description": source.description,
                **observed.get(
                    source.id,
                    {
                        "coverage": coverage_report(
                            source_view(source, snapshot_id="not-observed")
                        ),
                        "observed_at": None,
                        "credits_spent": None,
                        "unknown_cost_attempts": None,
                    },
                ),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": now.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "sources": rows,
    }
