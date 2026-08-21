from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.convergence_planner import plan_once
from app.reliability_telemetry import record_terminal_results
from app.schedule_ledger import ensure_schema as ensure_schedule_schema


def _status(source_id: str, **values: object) -> dict[str, object]:
    return {"source_id": source_id, "state": "ok", **values}


def test_planner_is_off_by_default_and_does_not_create_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"

    summary = plan_once(path=path, mode="off")

    assert summary.mode == "off"
    assert not path.exists()


def test_shadow_planner_creates_idempotent_chains_only_for_nonfresh_primary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    record_terminal_results(
        "primary-provisional",
        [_status("hsguru_meta_standard_legend", provisional=True)],
        path=path,
        finished_at=now - timedelta(minutes=10),
        refresh_window_id="schedule:20260820T100000Z",
    )
    record_terminal_results(
        "primary-fresh",
        [_status("metastats_decks")],
        path=path,
        finished_at=now - timedelta(minutes=9),
    )
    record_terminal_results(
        "manual-failure",
        [_status("metastats_matchups", state="fetch_error")],
        path=path,
        finished_at=now - timedelta(minutes=8),
        attempt_purpose="manual",
    )

    first = plan_once(path=path, now=now, mode="shadow")
    repeated = plan_once(path=path, now=now, mode="shadow")

    assert first.scanned_terminal_events == 2
    assert first.scanned_missing_slots == 0
    assert first.planned_chains == 1
    assert first.planned_sources == 1
    assert first.skipped_events == 1
    assert first.cursor_advanced is True
    assert repeated.scanned_terminal_events == 0
    with sqlite3.connect(path) as connection:
        chain = connection.execute(
            """
            SELECT origin_occurrence_id, action, state
            FROM convergence_chains
            """
        ).fetchone()
        source = connection.execute(
            "SELECT source_id FROM convergence_chain_sources"
        ).fetchone()
    assert chain == (
        "schedule:20260820T100000Z",
        "retry_candidate",
        "waiting",
    )
    assert source == ("hsguru_meta_standard_legend",)


def test_shadow_planner_migrates_legacy_reliability_schema_before_querying(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE source_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                finished_at REAL NOT NULL,
                outcome TEXT NOT NULL,
                terminal_state TEXT NOT NULL,
                recorded_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO source_attempts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-provisional",
                "legacy-run",
                "hsguru_meta_standard_legend",
                (now - timedelta(minutes=10)).timestamp(),
                "provisional",
                "ok",
                (now - timedelta(minutes=10)).timestamp(),
            ),
        )

    summary = plan_once(path=path, now=now, mode="shadow")

    assert summary.scanned_terminal_events == 1
    assert summary.planned_chains == 1
    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(source_attempts)")
        }
        chain = connection.execute(
            "SELECT action, state FROM convergence_chains"
        ).fetchone()
    assert {
        "attempt_purpose",
        "refresh_window_id",
        "reason_code",
        "independently_ineligible_reason",
    }.issubset(columns)
    assert chain == ("retry_candidate", "waiting")


def test_shadow_planner_turns_verified_upstream_gap_into_unpaid_probe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    record_terminal_results(
        "upstream-pending",
        [
            _status(
                "vicious_syndicate_radars",
                serving_cached_dataset=True,
                failure_reason_code="unavailable",
                upstream_state="upstream_publication_pending",
                last_refresh_upstream_state="upstream_publication_pending",
                last_refresh_at="2026-08-20T11:30:00+00:00",
                last_refresh_upstream_readiness={
                    "latest_report_issue": "355",
                    "candidate_issue": "354",
                    "full_discovery_at": "2026-08-20T11:25:00+00:00",
                },
            )
        ],
        path=path,
        finished_at=now - timedelta(minutes=20),
        refresh_window_id="vicious:20260820T100000Z",
    )

    summary = plan_once(path=path, now=now, mode="shadow")

    assert summary.planned_chains == 1
    with sqlite3.connect(path) as connection:
        chain = connection.execute(
            """
            SELECT action, state, paid_fetch_allowed
            FROM convergence_chains
            """
        ).fetchone()
    assert chain == ("probe_upstream", "upstream_pending", 0)


def test_shadow_planner_creates_unpaid_chain_for_missing_schedule_slot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    # Initialize source_attempts without creating a terminal for this slot.
    record_terminal_results(
        "unrelated-fresh",
        [_status("metastats_decks")],
        path=path,
        finished_at=now - timedelta(minutes=5),
    )
    with sqlite3.connect(path) as connection:
        ensure_schedule_schema(connection)
        connection.execute(
            """
            INSERT INTO schedule_source_windows (
                occurrence_id, schedule_id, scheduled_for, deadline_at,
                source_id, inventory_version, cohort_hash, eligible,
                exclusion_reason, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, '', ?)
            """,
            (
                "refresh-post-patch-tierlists:20260820T090000Z",
                "refresh-post-patch-tierlists",
                (now - timedelta(hours=3)).timestamp(),
                (now - timedelta(hours=1)).timestamp(),
                "hsguru_matchups_legend",
                "test-inventory",
                "a" * 64,
                now.timestamp(),
            ),
        )
        connection.commit()

    summary = plan_once(path=path, now=now, mode="shadow")

    assert summary.scanned_missing_slots == 1
    assert summary.planned_chains == 1
    with sqlite3.connect(path) as connection:
        chain = connection.execute(
            """
            SELECT origin_occurrence_id, action, paid_fetch_allowed
            FROM convergence_chains
            """
        ).fetchone()
    assert chain == (
        "refresh-post-patch-tierlists:20260820T090000Z",
        "retry_scheduler",
        0,
    )
