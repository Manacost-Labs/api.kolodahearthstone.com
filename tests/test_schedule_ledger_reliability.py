from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.reliability_telemetry import build_reliability_report, record_terminal_results

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _create_ledger_tables(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schedule_source_windows (
                occurrence_id TEXT NOT NULL,
                schedule_id TEXT NOT NULL,
                scheduled_for REAL NOT NULL,
                deadline_at REAL NOT NULL,
                source_id TEXT NOT NULL,
                inventory_version TEXT NOT NULL,
                cohort_hash TEXT NOT NULL,
                eligible INTEGER NOT NULL,
                exclusion_reason TEXT NOT NULL,
                recorded_at REAL NOT NULL,
                PRIMARY KEY (occurrence_id, source_id),
                UNIQUE (schedule_id, scheduled_for, source_id)
            );
            CREATE TABLE schedule_ledger_state (
                singleton INTEGER PRIMARY KEY,
                coverage_started_at REAL NOT NULL,
                materialized_through REAL NOT NULL,
                inventory_version TEXT NOT NULL,
                cohort_hash TEXT NOT NULL,
                tracked_schedule_count INTEGER NOT NULL,
                catalog_schedule_count INTEGER NOT NULL,
                catalog_source_count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )


def _insert_state(path: Path, *, tracked: int = 2, catalog: int = 14) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO schedule_ledger_state VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime(2026, 8, 1, tzinfo=UTC).timestamp(),
                datetime(2026, 8, 16, tzinfo=UTC).timestamp(),
                "test-v1",
                "a" * 64,
                tracked,
                catalog,
                3,
                NOW.timestamp(),
            ),
        )


def _insert_slot(
    path: Path,
    *,
    occurrence_id: str,
    source_id: str,
    scheduled_for: datetime,
    deadline_at: datetime,
    eligible: bool = True,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO schedule_source_windows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurrence_id,
                "refresh-all-daily",
                scheduled_for.timestamp(),
                deadline_at.timestamp(),
                source_id,
                "test-v1",
                "a" * 64,
                int(eligible),
                "" if eligible else "section_disabled",
                NOW.timestamp(),
            ),
        )


def test_report_exposes_missing_scheduled_runs_without_claiming_full_coverage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "run-on-time",
        [{"source_id": "fresh-source", "state": "ok"}],
        finished_at=datetime(2026, 8, 14, 10, 30, tzinfo=UTC),
        refresh_window_id="slot-on-time",
        path=path,
    )
    _create_ledger_tables(path)
    _insert_state(path)
    _insert_slot(
        path,
        occurrence_id="slot-on-time",
        source_id="fresh-source",
        scheduled_for=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
        deadline_at=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
    )
    _insert_slot(
        path,
        occurrence_id="slot-missing",
        source_id="missing-source",
        scheduled_for=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        deadline_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )

    scheduled = build_reliability_report(now=NOW, path=path)["windows"][0][
        "scheduled_reliability"
    ]

    assert scheduled["ledger_status"] == "partial"
    assert scheduled["measurement_status"] == "collecting"
    assert scheduled["schedule_coverage_ratio"] == 0.1429
    assert scheduled["temporal_coverage_ratio"] == 1.0
    assert scheduled["expected_slots"] == 2
    assert scheduled["eligible_slots"] == 2
    assert scheduled["due_slots"] == 2
    assert scheduled["on_time_fresh"] == 1
    assert scheduled["on_time_nonfresh"] == 0
    assert scheduled["late"] == 0
    assert scheduled["missing"] == 1
    assert scheduled["on_time_fresh_rate_pct"] == 50.0
    assert scheduled["objective_status"] == "collecting"


def test_schedule_slot_states_are_exclusive_and_exclusions_are_auditable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "run-late",
        [{"source_id": "late-source", "state": "ok"}],
        finished_at=datetime(2026, 8, 14, 11, 30, tzinfo=UTC),
        refresh_window_id="slot-late",
        path=path,
    )
    record_terminal_results(
        "run-failed",
        [{"source_id": "failed-source", "state": "fetch_error"}],
        finished_at=datetime(2026, 8, 14, 10, 30, tzinfo=UTC),
        refresh_window_id="slot-failed",
        path=path,
    )
    _create_ledger_tables(path)
    _insert_state(path, tracked=14, catalog=14)
    for occurrence_id, source_id in (
        ("slot-late", "late-source"),
        ("slot-failed", "failed-source"),
    ):
        _insert_slot(
            path,
            occurrence_id=occurrence_id,
            source_id=source_id,
            scheduled_for=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
            deadline_at=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
        )
    _insert_slot(
        path,
        occurrence_id="slot-disabled",
        source_id="disabled-source",
        scheduled_for=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        deadline_at=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
        eligible=False,
    )
    _insert_slot(
        path,
        occurrence_id="slot-pending",
        source_id="pending-source",
        scheduled_for=datetime(2026, 8, 14, 11, 30, tzinfo=UTC),
        deadline_at=datetime(2026, 8, 14, 12, 30, tzinfo=UTC),
    )

    scheduled = build_reliability_report(now=NOW, path=path)["windows"][0][
        "scheduled_reliability"
    ]

    assert scheduled["ledger_status"] == "covered"
    assert scheduled["measurement_status"] == "observed"
    assert scheduled["expected_slots"] == 4
    assert scheduled["eligible_slots"] == 3
    assert scheduled["excluded_slots"] == 1
    assert scheduled["pending_slots"] == 1
    assert scheduled["due_slots"] == 2
    assert scheduled["on_time_fresh"] == 0
    assert scheduled["on_time_nonfresh"] == 1
    assert scheduled["late"] == 1
    assert scheduled["missing"] == 0
    assert scheduled["on_time_fresh_rate_pct"] == 0.0
    assert scheduled["objective_status"] == "breached"


def test_empty_report_marks_schedule_ledger_as_absent(tmp_path: Path) -> None:
    scheduled = build_reliability_report(
        now=NOW,
        path=tmp_path / "missing.sqlite3",
    )["windows"][0]["scheduled_reliability"]

    assert scheduled["ledger_status"] == "absent"
    assert scheduled["measurement_status"] == "collecting"
    assert scheduled["schedule_coverage_ratio"] == 0.0
    assert scheduled["temporal_coverage_ratio"] == 0.0
    assert scheduled["expected_slots"] == 0
    assert scheduled["on_time_fresh_rate_pct"] is None
    assert scheduled["objective_status"] == "collecting"
