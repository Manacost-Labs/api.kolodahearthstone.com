from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.parser_control_schedule import _ScheduleSpec
from app.schedule_ledger import (
    FUTURE_HORIZON,
    SourceEligibility,
    SourceSetMismatchError,
    build_current_eligibility,
    claim_occurrence,
    deterministic_occurrence_id,
    ensure_schema,
    enumerate_nominal_occurrences,
    materialize_schedule_windows,
    reconcile_schedule_ledger,
    tracked_schedule_specs,
)
from app.sources import SOURCE_BY_ID


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def test_schema_contains_occurrence_and_coverage_contract() -> None:
    connection = _connection()

    window_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(schedule_source_windows)")
    }
    state_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(schedule_ledger_state)")
    }

    assert window_columns == {
        "occurrence_id",
        "schedule_id",
        "scheduled_for",
        "deadline_at",
        "source_id",
        "inventory_version",
        "cohort_hash",
        "eligible",
        "exclusion_reason",
        "recorded_at",
    }
    assert state_columns == {
        "singleton",
        "coverage_started_at",
        "materialized_through",
        "inventory_version",
        "cohort_hash",
        "tracked_schedule_count",
        "catalog_schedule_count",
        "catalog_source_count",
        "updated_at",
    }


def test_occurrence_id_is_deterministic_in_utc() -> None:
    warsaw = ZoneInfo("Europe/Warsaw")
    local = datetime(2026, 8, 14, 18, 0, tzinfo=warsaw)

    assert deterministic_occurrence_id("refresh-api-daily", local) == (
        deterministic_occurrence_id(
            "refresh-api-daily", datetime(2026, 8, 14, 16, 0, tzinfo=UTC)
        )
    )


def test_nominal_enumeration_skips_nonexistent_dst_local_time() -> None:
    spec = _ScheduleSpec(
        id="dst-test",
        label="DST test",
        systemd_unit="dst-test.timer",
        on_calendar=("*-*-* 02:45:00 Europe/Warsaw",),
        source_ids=frozenset({"source"}),
        recurrence="daily",
        local_times=(time(2, 45),),
    )

    occurrences = enumerate_nominal_occurrences(
        spec,
        start=datetime(2026, 3, 28, 0, 0, tzinfo=UTC),
        end=datetime(2026, 3, 31, 0, 0, tzinfo=UTC),
    )

    assert [
        value.astimezone(ZoneInfo("Europe/Warsaw")).isoformat() for value in occurrences
    ] == [
        "2026-03-28T02:45:00+01:00",
        "2026-03-30T02:45:00+02:00",
    ]


def test_first_materialization_starts_now_and_reaches_48_hours() -> None:
    connection = _connection()
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)

    result = materialize_schedule_windows(connection, now=now)

    scheduled = [
        datetime.fromtimestamp(row[0], tz=UTC)
        for row in connection.execute(
            "SELECT DISTINCT scheduled_for FROM schedule_source_windows ORDER BY scheduled_for"
        )
    ]
    assert scheduled
    assert min(scheduled) >= now
    assert max(scheduled) < now + FUTURE_HORIZON
    assert result.coverage_started_at == now
    state = connection.execute("SELECT * FROM schedule_ledger_state").fetchone()
    assert datetime.fromtimestamp(state["materialized_through"], tz=UTC) == (
        now + FUTURE_HORIZON
    )
    assert state["tracked_schedule_count"] == 2
    assert state["catalog_schedule_count"] > state["tracked_schedule_count"]
    assert state["catalog_source_count"] == len(SOURCE_BY_ID)


def test_disabled_source_decision_is_bounded_and_materialization_is_idempotent() -> (
    None
):
    connection = _connection()
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    api_spec = next(
        spec for spec in tracked_schedule_specs() if spec.id == "refresh-api-daily"
    )
    disabled_source = min(api_spec.source_ids)
    overrides = {disabled_source: SourceEligibility(False, " disabled   " + "x" * 200)}

    first = materialize_schedule_windows(
        connection,
        now=now,
        eligibility_by_source=overrides,
    )
    row = connection.execute(
        """
        SELECT eligible, exclusion_reason
        FROM schedule_source_windows
        WHERE schedule_id = ? AND source_id = ?
        ORDER BY scheduled_for
        LIMIT 1
        """,
        (api_spec.id, disabled_source),
    ).fetchone()
    before_count = connection.execute(
        "SELECT COUNT(*) FROM schedule_source_windows"
    ).fetchone()[0]
    second = materialize_schedule_windows(
        connection,
        now=now,
        eligibility_by_source=overrides,
    )

    assert row["eligible"] == 0
    assert row["exclusion_reason"].startswith("disabled x")
    assert len(row["exclusion_reason"]) == 96
    assert first.inserted_source_windows == before_count
    assert second.inserted_source_windows == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM schedule_source_windows").fetchone()[0]
        == before_count
    )


def test_materialization_gap_starts_a_new_coverage_cohort_without_backfill() -> None:
    connection = _connection()
    first_now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    first = materialize_schedule_windows(connection, now=first_now)
    resumed_at = first_now + FUTURE_HORIZON + timedelta(minutes=1)

    resumed = materialize_schedule_windows(connection, now=resumed_at)

    assert resumed.coverage_started_at == resumed_at
    assert resumed.cohort_hash != first.cohort_hash
    assert (
        connection.execute(
            """
            SELECT COUNT(*) FROM schedule_source_windows
            WHERE cohort_hash = ? AND scheduled_for < ?
            """,
            (resumed.cohort_hash, resumed_at.timestamp()),
        ).fetchone()[0]
        == 0
    )


def test_changed_eligibility_updates_future_rows_but_keeps_due_decision() -> None:
    connection = _connection()
    api_spec = next(
        spec for spec in tracked_schedule_specs() if spec.id == "refresh-api-daily"
    )
    source_id = min(api_spec.source_ids)
    materialize_schedule_windows(
        connection,
        now=datetime(2026, 8, 14, 15, 59, tzinfo=UTC),
        eligibility_by_source={source_id: SourceEligibility(True)},
    )

    materialize_schedule_windows(
        connection,
        now=datetime(2026, 8, 14, 16, 0, tzinfo=UTC),
        eligibility_by_source={source_id: SourceEligibility(False, "section-disabled")},
    )

    decisions = connection.execute(
        """
        SELECT scheduled_for, eligible, exclusion_reason
        FROM schedule_source_windows
        WHERE schedule_id = ? AND source_id = ?
        ORDER BY scheduled_for
        LIMIT 2
        """,
        (api_spec.id, source_id),
    ).fetchall()
    assert decisions[0]["eligible"] == 1
    assert decisions[0]["exclusion_reason"] == ""
    assert decisions[1]["eligible"] == 0
    assert decisions[1]["exclusion_reason"] == "section-disabled"


def test_claim_returns_due_occurrence_for_exact_static_scope(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    ensure_schema(connection)
    api_spec = next(
        spec for spec in tracked_schedule_specs() if spec.id == "refresh-api-daily"
    )
    disabled_source = min(api_spec.source_ids)
    materialize_schedule_windows(
        connection,
        now=datetime(2026, 8, 14, 15, 59, tzinfo=UTC),
        eligibility_by_source={
            disabled_source: SourceEligibility(False, "section-disabled")
        },
    )
    connection.commit()
    connection.close()

    claimed = claim_occurrence(
        api_spec.id,
        api_spec.source_ids,
        now=datetime(2026, 8, 14, 16, 1, tzinfo=UTC),
        path=path,
    )

    assert claimed == deterministic_occurrence_id(
        api_spec.id,
        datetime(2026, 8, 14, 16, 0, tzinfo=UTC),
    )


def test_claim_rejects_a_source_scope_that_differs_from_persisted_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    ensure_schema(connection)
    api_spec = next(
        spec for spec in tracked_schedule_specs() if spec.id == "refresh-api-daily"
    )
    materialize_schedule_windows(
        connection,
        now=datetime(2026, 8, 14, 15, 59, tzinfo=UTC),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SourceSetMismatchError):
        claim_occurrence(
            api_spec.id,
            set(api_spec.source_ids) - {min(api_spec.source_ids)},
            now=datetime(2026, 8, 14, 16, 1, tzinfo=UTC),
            path=path,
        )


def test_claim_does_not_create_a_retroactive_occurrence(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    ensure_schema(connection)
    connection.commit()
    connection.close()
    api_spec = next(
        spec for spec in tracked_schedule_specs() if spec.id == "refresh-api-daily"
    )

    with pytest.raises(LookupError):
        claim_occurrence(
            api_spec.id,
            api_spec.source_ids,
            now=datetime(2026, 8, 14, 16, 1, tzinfo=UTC),
            path=path,
        )


def test_current_eligibility_distinguishes_section_disabled_sources() -> None:
    specs = tracked_schedule_specs()
    source_ids = sorted({source_id for spec in specs for source_id in spec.source_ids})
    disabled_source = next(
        source_id for source_id in source_ids if source_id != "firestone_standard"
    )
    enabled = [source_id for source_id in source_ids if source_id != disabled_source]

    with patch("app.parser_control.filter_scheduled_source_ids", return_value=enabled):
        decisions = build_current_eligibility()

    assert decisions[disabled_source] == SourceEligibility(
        eligible=False,
        exclusion_reason="section-disabled",
    )


def test_reconcile_returns_bounded_aggregate_and_persists_state(tmp_path: Path) -> None:
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    path = tmp_path / "ledger.sqlite3"

    aggregate = reconcile_schedule_ledger(now=now, path=path)
    catalog_schedule_count = cast(int, aggregate["catalog_schedule_count"])
    expected = cast(int, aggregate["expected_source_windows"])
    eligible = cast(int, aggregate["eligible_source_windows"])
    excluded = cast(int, aggregate["excluded_source_windows"])

    assert aggregate["tracked_schedule_count"] == 2
    assert catalog_schedule_count > 2
    assert aggregate["catalog_source_count"] == len(SOURCE_BY_ID)
    assert aggregate["materialized_through"] == (now + timedelta(hours=48)).isoformat()
    assert expected > 0
    assert eligible + excluded == expected
