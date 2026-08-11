"""Durable, privacy-safe reliability telemetry for logical source refreshes.

This module deliberately records one terminal outcome per source and refresh
run. Provider retries, URLs, response bodies, exception messages, and secrets
never enter this database or its public aggregate report.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .source_state import SourceState
from .storage import root_dir

OUTCOME_TIMED_OUT = str(SourceState.TIMED_OUT)
OUTCOMES = (
    "fresh_published",
    "provisional",
    "lkg_served",
    "failed",
    OUTCOME_TIMED_OUT,
    "skipped",
)
ELIGIBLE_OUTCOMES = OUTCOMES[:-1]
WINDOWS = (
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
)
METHODOLOGY_VERSION = "logical-source-observed-v1"

_schema_lock = threading.Lock()


def telemetry_db_path() -> Path:
    return root_dir() / "parser-telemetry.sqlite3"


def reliability_cache_revision(*, path: Path | None = None) -> str:
    """Return a cheap revision that changes for SQLite or WAL writes."""

    resolved_path = path or telemetry_db_path()
    fingerprints: list[str] = []
    for candidate in (
        resolved_path,
        Path(f"{resolved_path}-wal"),
    ):
        try:
            stat = candidate.stat()
        except OSError:
            continue
        fingerprints.append(f"{candidate.name}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(fingerprints) or "not-collected"


def classify_terminal_status(status: Mapping[str, object]) -> str:
    """Map a terminal source status to one stable, bounded outcome."""

    state = str(status.get("state") or "").strip().lower()
    if status.get("skipped") is True or state in {"locked", "skipped"}:
        return "skipped"
    if state == SourceState.TIMED_OUT:
        return OUTCOME_TIMED_OUT
    if status.get("serving_cached_dataset") is True:
        return "lkg_served"
    if state == "ok" and status.get("provisional") is True:
        return "provisional"
    if state == "ok":
        return "fresh_published"
    return "failed"


def record_terminal_results(
    run_id: str,
    results: Iterable[Mapping[str, object]],
    *,
    finished_at: datetime | None = None,
    path: Path | None = None,
) -> int:
    """Persist terminal source results atomically and idempotently.

    A deterministic key for ``run_id + source_id`` prevents a telemetry retry
    from inflating the denominator. Only bounded operational fields are stored.
    """

    completed_at = _as_utc(finished_at or datetime.now(UTC))
    completed_epoch = completed_at.timestamp()
    recorded_epoch = datetime.now(UTC).timestamp()
    normalized_run_id = str(run_id).strip()[:160]
    if not normalized_run_id:
        raise ValueError("run_id is required")

    rows: list[tuple[str, str, str, float, str, str, float]] = []
    seen_sources: set[str] = set()
    for result in results:
        source_id = str(result.get("source_id") or "").strip()[:160]
        if not source_id or source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        outcome = classify_terminal_status(result)
        terminal_state = _bounded_terminal_state(result.get("state"))
        attempt_id = hashlib.sha256(
            f"{normalized_run_id}\0{source_id}".encode()
        ).hexdigest()
        rows.append(
            (
                attempt_id,
                normalized_run_id,
                source_id,
                completed_epoch,
                outcome,
                terminal_state,
                recorded_epoch,
            )
        )

    if not rows:
        return 0

    resolved_path = path or telemetry_db_path()
    connection = _connect(resolved_path)
    inserted = 0
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO source_attempts (
                    attempt_id,
                    run_id,
                    source_id,
                    finished_at,
                    outcome,
                    terminal_state,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            inserted += max(0, cursor.rowcount)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted


def build_reliability_report(
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return aggregate reliability windows without per-source information."""

    report_at = _as_utc(now or datetime.now(UTC))
    resolved_path = path or telemetry_db_path()
    if not resolved_path.exists():
        return _empty_report(report_at)

    connection = _connect(resolved_path)
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN")
        coverage_row = connection.execute(
            "SELECT MIN(finished_at) FROM source_attempts"
        ).fetchone()
        coverage_epoch = (
            float(coverage_row[0])
            if coverage_row is not None and coverage_row[0] is not None
            else None
        )
        windows = [
            _query_window(
                connection,
                label=label,
                duration=duration,
                report_at=report_at,
                coverage_epoch=coverage_epoch,
            )
            for label, duration in WINDOWS
        ]
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "methodology": _methodology(),
        "generated_at": report_at.isoformat(),
        "coverage_started_at": _iso_from_epoch(coverage_epoch),
        "windows": windows,
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    # The in-process lock avoids competing PRAGMA journal-mode transitions.
    # SQLite's own lock plus busy_timeout protects the same initialization when
    # multiple parser processes start concurrently.
    with _schema_lock:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                finished_at REAL NOT NULL,
                outcome TEXT NOT NULL CHECK (
                    outcome IN (
                        'fresh_published',
                        'provisional',
                        'lkg_served',
                        'failed',
                        'timed_out',
                        'skipped'
                    )
                ),
                terminal_state TEXT NOT NULL,
                recorded_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS source_attempts_finished_at_idx
            ON source_attempts (finished_at)
            """
        )


def _query_window(
    connection: sqlite3.Connection,
    *,
    label: str,
    duration: timedelta,
    report_at: datetime,
    coverage_epoch: float | None,
) -> dict[str, Any]:
    from_at = report_at - duration
    rows = connection.execute(
        """
        SELECT outcome, COUNT(*)
        FROM source_attempts
        WHERE finished_at >= ? AND finished_at <= ?
        GROUP BY outcome
        """,
        (from_at.timestamp(), report_at.timestamp()),
    ).fetchall()
    counts = {outcome: 0 for outcome in OUTCOMES}
    for outcome, count in rows:
        if outcome in counts:
            counts[str(outcome)] = int(count)

    total_attempts = sum(counts.values())
    eligible_attempts = sum(counts[outcome] for outcome in ELIGIBLE_OUTCOMES)
    full_fresh = counts["fresh_published"]
    accepted_fresh = full_fresh + counts["provisional"]
    data_available = accepted_fresh + counts["lkg_served"]
    coverage_ratio = _coverage_ratio(
        coverage_epoch=coverage_epoch,
        from_at=from_at,
        report_at=report_at,
    )
    return {
        "window": label,
        "from_at": from_at.isoformat(),
        "to_at": report_at.isoformat(),
        "measurement_status": "observed" if coverage_ratio >= 1.0 else "collecting",
        "coverage_ratio": coverage_ratio,
        "total_attempts": total_attempts,
        "eligible_attempts": eligible_attempts,
        "counts": counts,
        "full_fresh_rate_pct": _percentage(full_fresh, eligible_attempts),
        "accepted_fresh_rate_pct": _percentage(accepted_fresh, eligible_attempts),
        "data_available_rate_pct": _percentage(data_available, eligible_attempts),
    }


def _empty_report(report_at: datetime) -> dict[str, Any]:
    return {
        "methodology": _methodology(),
        "generated_at": report_at.isoformat(),
        "coverage_started_at": None,
        "windows": [
            {
                "window": label,
                "from_at": (report_at - duration).isoformat(),
                "to_at": report_at.isoformat(),
                "measurement_status": "collecting",
                "coverage_ratio": 0.0,
                "total_attempts": 0,
                "eligible_attempts": 0,
                "counts": {outcome: 0 for outcome in OUTCOMES},
                "full_fresh_rate_pct": None,
                "accepted_fresh_rate_pct": None,
                "data_available_rate_pct": None,
            }
            for label, duration in WINDOWS
        ],
    }


def _methodology() -> dict[str, Any]:
    return {
        "version": METHODOLOGY_VERSION,
        "unit": "one terminal outcome per source in a refresh run",
        "scope": "generic_refresh_sources",
        "completeness": "observed_attempts_only",
        "limitations": [
            "dedicated_pipeline_sources_excluded",
            "best_effort_write_gaps_not_detectable",
        ],
        "eligible_outcomes": list(ELIGIBLE_OUTCOMES),
        "excluded_outcomes": ["skipped"],
    }


def _bounded_terminal_state(value: object) -> str:
    state = str(value or "").strip().lower()
    allowed = {str(source_state) for source_state in SourceState} | {
        "locked",
        "skipped",
    }
    return state if state in allowed else "unknown_error"


def _coverage_ratio(
    *,
    coverage_epoch: float | None,
    from_at: datetime,
    report_at: datetime,
) -> float:
    if coverage_epoch is None:
        return 0.0
    coverage_at = datetime.fromtimestamp(coverage_epoch, tz=UTC)
    observed_seconds = max(0.0, (report_at - coverage_at).total_seconds())
    window_seconds = max(1.0, (report_at - from_at).total_seconds())
    return round(min(1.0, observed_seconds / window_seconds), 4)


def _percentage(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 2)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()
