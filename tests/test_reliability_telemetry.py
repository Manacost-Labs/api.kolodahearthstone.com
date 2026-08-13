from __future__ import annotations

import asyncio
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.fetcher import _refresh_sources_unlocked, _run_tier_serial_browser
from app.reliability_telemetry import (
    build_reliability_report,
    classify_failure_reason,
    classify_terminal_status,
    record_terminal_results,
    reliability_cache_revision,
    update_terminal_ai_results,
)
from app.sources import SOURCE_BY_ID, Source


def _status(source_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {"source_id": source_id, "state": "ok"}
    value.update(overrides)
    return value


def _record_process_attempt(args: tuple[str, int, float]) -> int:
    path, index, finished_at = args
    return record_terminal_results(
        f"process-run-{index}",
        [_status(f"process-source-{index}")],
        finished_at=datetime.fromtimestamp(finished_at, tz=UTC),
        path=Path(path),
    )


def test_terminal_statuses_have_stable_honest_outcomes() -> None:
    assert classify_terminal_status(_status("fresh")) == "fresh_published"
    assert classify_terminal_status(_status("early", provisional=True)) == "provisional"
    assert (
        classify_terminal_status(_status("cached", serving_cached_dataset=True))
        == "lkg_served"
    )
    assert (
        classify_terminal_status(_status("failed", state="quality_error")) == "failed"
    )
    assert classify_terminal_status(_status("late", state="timed_out")) == "timed_out"
    assert classify_terminal_status(_status("debug", diagnostic=True)) == "skipped"
    assert classify_terminal_status(_status("debug", state="diagnostic")) == "skipped"
    assert (
        classify_terminal_status(
            _status("locked", state="locked", skipped=True, reason="resource_locked")
        )
        == "skipped"
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (_status("fresh"), "none"),
        (
            _status(
                "proxy",
                serving_cached_dataset=True,
                last_refresh_failure_class="proxy_402",
            ),
            "proxy_payment",
        ),
        (_status("auth", state="fetch_error", http_status=401), "authentication"),
        (_status("rate", state="fetch_error", http_status=429), "rate_limited"),
        (_status("blocked", state="fetch_error", http_status=403), "access_blocked"),
        (_status("server", state="fetch_error", http_status=502), "upstream_5xx"),
        (_status("late", state="timed_out"), "timeout"),
        (
            _status(
                "contract",
                state="quality_error",
                detail="source contract failed: too few rows",
            ),
            "contract",
        ),
        (
            _status(
                "regression",
                serving_cached_dataset=True,
                last_refresh_error="Dataset regression: metric count dropped",
            ),
            "regression",
        ),
        (
            _status(
                "ai",
                serving_cached_dataset=True,
                latest_ai_review={"quarantine": True},
            ),
            "ai_quarantine",
        ),
        (
            _status(
                "explicit",
                state="fetch_error",
                failure_reason_code="preflight",
            ),
            "preflight",
        ),
    ],
)
def test_failure_reasons_are_bounded(status: dict[str, object], reason: str) -> None:
    assert classify_failure_reason(status) == reason


def test_recovery_attempts_share_one_final_refresh_window_slo_outcome(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    window_id = "hsguru-analysis:2026-08-11"
    record_terminal_results(
        "scheduled-run",
        [
            _status(
                "pipeline-source",
                state="fetch_error",
                failure_reason_code="upstream_5xx",
            )
        ],
        finished_at=now - timedelta(hours=2),
        path=path,
        refresh_window_id=window_id,
    )
    record_terminal_results(
        "recovery-run",
        [_status("pipeline-source")],
        finished_at=now - timedelta(hours=1),
        path=path,
        refresh_window_id=window_id,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["physical_attempts"] == 2
    assert day["total_attempts"] == 1
    assert day["observed_eligible_attempts"] == 1
    assert day["missing_terminal_windows"] == 0
    assert day["eligible_attempts"] == 1
    assert day["counts"]["fresh_published"] == 1
    assert day["counts"]["failed"] == 0
    assert day["failure_reasons"]["upstream_5xx"] == 0
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT refresh_window_id) FROM source_attempts"
        ).fetchone()
    assert stored == (2, 1)


def test_incomplete_run_adds_only_its_absent_terminal_to_slo_denominator(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "incomplete-run",
        [_status("fresh")],
        finished_at=now - timedelta(hours=1),
        path=path,
        coverage_scope="full",
        expected_source_count=2,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["physical_attempts"] == 1
    assert day["total_attempts"] == 1
    assert day["observed_eligible_attempts"] == 1
    assert day["missing_terminal_windows"] == 1
    assert day["eligible_attempts"] == 2
    assert day["full_fresh_rate_pct"] == 50.0
    assert day["data_available_rate_pct"] == 50.0
    assert day["freshness_slo"]["bad_attempts"] == 1
    assert day["availability_slo"]["bad_attempts"] == 1
    assert day["coverage_ratio"] == 0.0


def test_cross_run_recovery_fills_missing_source_in_same_refresh_window(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    window_id = "pipeline:2026-08-11"
    record_terminal_results(
        "scheduled-incomplete",
        [_status("source-a")],
        finished_at=now - timedelta(hours=2),
        path=path,
        expected_source_count=2,
        refresh_window_id=window_id,
    )
    record_terminal_results(
        "targeted-recovery",
        [_status("source-b")],
        finished_at=now - timedelta(hours=1),
        path=path,
        refresh_window_id=window_id,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["physical_attempts"] == 2
    assert day["total_attempts"] == 2
    assert day["observed_eligible_attempts"] == 2
    assert day["missing_terminal_windows"] == 0
    assert day["eligible_attempts"] == 2
    assert day["full_fresh_rate_pct"] == 100.0
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT refresh_window_id FROM refresh_runs ORDER BY run_id"
        ).fetchall()
    assert stored == [(window_id,), (window_id,)]


def test_skipped_terminal_is_excluded_but_not_misclassified_as_missing(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "skipped-helper-run",
        [_status("helper", state="locked", skipped=True)],
        finished_at=now - timedelta(hours=1),
        path=path,
        expected_source_count=1,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["counts"]["skipped"] == 1
    assert day["observed_eligible_attempts"] == 0
    assert day["missing_terminal_windows"] == 0
    assert day["eligible_attempts"] == 0
    assert day["full_fresh_rate_pct"] is None


def test_missing_terminal_windows_are_scoped_to_every_report_window(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "schema-seed",
        [_status("seed", state="locked", skipped=True)],
        finished_at=now - timedelta(days=31),
        path=path,
    )
    connection = sqlite3.connect(path)
    for run_id, age in (
        ("missing-day", timedelta(hours=2)),
        ("missing-week", timedelta(days=2)),
        ("missing-month", timedelta(days=10)),
    ):
        finished_at = (now - age).timestamp()
        connection.execute(
            """
            INSERT INTO refresh_runs (
                run_id,
                finished_at,
                scope,
                cohort_hash,
                expected_sources,
                observed_sources,
                recorded_at
            ) VALUES (?, ?, 'partial', '', 1, 0, ?)
            """,
            (run_id, finished_at, finished_at),
        )
    connection.commit()
    connection.close()

    windows = build_reliability_report(now=now, path=path)["windows"]

    assert [window["observed_eligible_attempts"] for window in windows] == [0, 0, 0]
    assert [window["missing_terminal_windows"] for window in windows] == [1, 2, 3]
    assert [window["eligible_attempts"] for window in windows] == [1, 2, 3]
    assert [window["full_fresh_rate_pct"] for window in windows] == [0.0, 0.0, 0.0]


def test_skipped_recovery_does_not_erase_a_refresh_window_failure(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "failed-run",
        [
            _status(
                "pipeline-source",
                state="timed_out",
                failure_reason_code="timeout",
            )
        ],
        finished_at=now - timedelta(hours=2),
        path=path,
        refresh_window_id="pipeline:2026-08-11",
    )
    record_terminal_results(
        "locked-recovery",
        [_status("pipeline-source", state="locked", skipped=True)],
        finished_at=now - timedelta(hours=1),
        path=path,
        refresh_window_id="pipeline:2026-08-11",
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["physical_attempts"] == 2
    assert day["total_attempts"] == 1
    assert day["eligible_attempts"] == 1
    assert day["counts"]["timed_out"] == 1
    assert day["counts"]["skipped"] == 0
    assert day["failure_reasons"]["timeout"] == 1


def test_attempts_without_refresh_window_remain_independent(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    for index in range(2):
        record_terminal_results(
            f"legacy-run-{index}",
            [_status("same-source")],
            finished_at=now - timedelta(minutes=index),
            path=path,
        )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["physical_attempts"] == 2
    assert day["total_attempts"] == 2
    assert day["counts"]["fresh_published"] == 2


def test_report_uses_one_logical_attempt_and_excludes_skips(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    results = [
        _status("fresh"),
        _status("early", provisional=True),
        _status(
            "cached",
            serving_cached_dataset=True,
            last_refresh_failure_class="proxy_402",
        ),
        _status(
            "failed",
            state="quality_error",
            detail="Dataset regression: metric count dropped",
        ),
        _status("late", state="timed_out"),
        _status("locked", state="locked", skipped=True, reason="resource_locked"),
    ]

    record_terminal_results("run-1", results, finished_at=now, path=path)
    # A retried writer must not add a second logical attempt for the same run/source.
    record_terminal_results("run-1", results, finished_at=now, path=path)
    report = build_reliability_report(now=now, path=path)

    day = report["windows"][0]
    assert day["window"] == "24h"
    assert day["counts"] == {
        "fresh_published": 1,
        "provisional": 1,
        "lkg_served": 1,
        "failed": 1,
        "timed_out": 1,
        "skipped": 1,
    }
    assert day["total_attempts"] == 6
    assert day["eligible_attempts"] == 5
    assert day["full_fresh_rate_pct"] == 20.0
    assert day["accepted_fresh_rate_pct"] == 40.0
    assert day["data_available_rate_pct"] == 60.0
    assert day["measurement_status"] == "collecting"
    assert day["failure_reasons"]["proxy_payment"] == 1
    assert day["failure_reasons"]["regression"] == 1
    assert day["failure_reasons"]["timeout"] == 1
    assert sum(day["failure_reasons"].values()) == 3
    assert day["freshness_slo"] == {
        "target_rate_pct": 99.0,
        "objective_status": "collecting",
        "good_attempts": 1,
        "bad_attempts": 4,
        "allowed_bad_attempts": 0.05,
        "bad_attempts_over_budget": 4,
        "error_budget_remaining_attempts": -3.95,
        "error_budget_consumed_pct": 8000.0,
    }
    assert day["availability_slo"]["good_attempts"] == 3
    assert day["ai_quality"]["candidate_review"]["completed"] == 0
    assert day["ai_quality"]["failure_diagnosis"]["all_problem_attempts"] == 3
    assert day["ai_quality"]["calibration"]["status"] == "not_calibrated"


def test_diagnostic_attempt_is_recorded_but_excluded_from_slo(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "debug-run",
        [_status("debug", state="diagnostic", diagnostic=True)],
        finished_at=now,
        path=path,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["total_attempts"] == 1
    assert day["counts"]["skipped"] == 1
    assert day["eligible_attempts"] == 0
    assert day["full_fresh_rate_pct"] is None
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT outcome, terminal_state FROM source_attempts"
        ).fetchone()
    assert stored == ("skipped", "diagnostic")


def test_sparse_attempt_history_never_claims_complete_window_coverage(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    for run_id, age in (
        ("inside-day", timedelta(hours=23)),
        ("inside-week", timedelta(hours=25)),
        ("inside-month", timedelta(days=8)),
        ("outside-month", timedelta(days=31)),
    ):
        record_terminal_results(
            run_id,
            [_status(run_id)],
            finished_at=now - age,
            path=path,
        )

    report = build_reliability_report(now=now, path=path)

    assert [window["window"] for window in report["windows"]] == ["24h", "7d", "30d"]
    assert [window["eligible_attempts"] for window in report["windows"]] == [1, 2, 3]
    assert report["windows"][0]["measurement_status"] == "collecting"
    assert report["windows"][1]["measurement_status"] == "collecting"
    assert report["windows"][2]["measurement_status"] == "collecting"
    assert report["methodology"]["scope"] == "observed_scrape_and_pipeline_sources"
    assert (
        report["methodology"]["completeness"]
        == "observed_attempts_plus_recorded_run_deficits"
    )
    assert (
        "entirely_missing_scheduled_runs_not_detectable_until_ledger"
        in report["methodology"]["limitations"]
    )
    assert report["coverage_started_at"] is None
    assert report["windows"][0]["freshness_slo"]["objective_status"] == "collecting"


def test_complete_scrape_coverage_keeps_combined_slo_collecting_without_ledger(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    for day in range(30):
        record_terminal_results(
            f"full-{day}",
            [_status(f"source-{day}")],
            finished_at=now - timedelta(hours=12, days=day),
            path=path,
            coverage_scope="full",
            expected_source_count=1,
        )

    report = build_reliability_report(now=now, path=path)

    assert [window["eligible_attempts"] for window in report["windows"]] == [1, 7, 30]
    assert [window["coverage_ratio"] for window in report["windows"]] == [1.0, 1.0, 1.0]
    assert all(
        window["measurement_status"] == "collecting" for window in report["windows"]
    )
    assert all(
        window["freshness_slo"]["objective_status"] == "collecting"
        for window in report["windows"]
    )
    assert report["methodology"]["coverage_scope"] == "generic_scrape_sources_only"
    assert (
        report["methodology"]["combined_slo_readiness"]
        == "collecting_pipeline_schedule_ledger"
    )


def test_monthly_slo_stays_collecting_when_daily_coverage_has_gaps(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    for run_id, age in (
        ("old-edge", timedelta(days=30)),
        ("today", timedelta(hours=1)),
    ):
        record_terminal_results(
            run_id,
            [_status(run_id)],
            finished_at=now - age,
            path=path,
            coverage_scope="full",
            expected_source_count=1,
        )

    month = build_reliability_report(now=now, path=path)["windows"][2]

    assert month["full_fresh_rate_pct"] == 100.0
    assert month["coverage_ratio"] < 0.1
    assert month["measurement_status"] == "collecting"
    assert month["freshness_slo"]["objective_status"] == "collecting"


def test_skipped_full_runs_never_satisfy_monthly_coverage(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    for day in range(30):
        record_terminal_results(
            f"skipped-full-{day}",
            [_status(f"locked-{day}", state="locked", skipped=True)],
            finished_at=now - timedelta(hours=12, days=day),
            path=path,
            coverage_scope="full",
            expected_source_count=1,
        )
    record_terminal_results(
        "successful-partial",
        [_status("fresh")],
        finished_at=now - timedelta(hours=1),
        path=path,
    )

    month = build_reliability_report(now=now, path=path)["windows"][2]

    assert month["full_fresh_rate_pct"] == 100.0
    assert month["coverage_ratio"] == 0.0
    assert month["measurement_status"] == "collecting"
    assert month["freshness_slo"]["objective_status"] == "collecting"


def test_bucket_boundaries_cannot_hide_a_missed_daily_refresh(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    window_start = now - timedelta(days=30)
    offsets = [timedelta(minutes=1), timedelta(hours=47, minutes=59)]
    offsets.extend(timedelta(days=day, hours=12) for day in range(2, 30))
    for index, offset in enumerate(offsets):
        record_terminal_results(
            f"boundary-{index}",
            [_status(f"source-{index}")],
            finished_at=window_start + offset,
            path=path,
            coverage_scope="full",
            expected_source_count=1,
        )

    month = build_reliability_report(now=now, path=path)["windows"][2]

    assert month["coverage_ratio"] < 1.0
    assert month["measurement_status"] == "collecting"
    assert month["freshness_slo"]["objective_status"] == "collecting"


def test_registry_cohort_change_resets_monthly_coverage(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    old_cohort = "a" * 64
    current_cohort = "b" * 64
    with patch(
        "app.reliability_telemetry.canonical_scrape_cohort_hash",
        return_value=old_cohort,
    ):
        for day in range(30):
            record_terminal_results(
                f"old-cohort-{day}",
                [_status(f"old-source-{day}")],
                finished_at=now - timedelta(hours=12, days=day),
                path=path,
                coverage_scope="full",
                expected_source_count=1,
            )
    with patch(
        "app.reliability_telemetry.canonical_scrape_cohort_hash",
        return_value=current_cohort,
    ):
        record_terminal_results(
            "current-cohort",
            [_status("current-source")],
            finished_at=now - timedelta(hours=1),
            path=path,
            coverage_scope="full",
            expected_source_count=1,
        )
        report = build_reliability_report(now=now, path=path)

    month = report["windows"][2]
    assert report["coverage_cohort_hash"] == current_cohort
    assert month["full_fresh_rate_pct"] == 100.0
    assert month["coverage_ratio"] < 0.1
    assert month["measurement_status"] == "collecting"


def test_duplicate_run_id_cannot_move_old_full_refresh_into_current_window(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "immutable-run",
        [_status("same-source")],
        finished_at=now - timedelta(days=10),
        path=path,
        coverage_scope="full",
        expected_source_count=1,
    )
    inserted = record_terminal_results(
        "immutable-run",
        [_status("same-source")],
        finished_at=now - timedelta(hours=1),
        path=path,
        coverage_scope="full",
        expected_source_count=1,
    )
    record_terminal_results(
        "current-partial",
        [_status("fresh-partial")],
        finished_at=now - timedelta(minutes=30),
        path=path,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert inserted == 0
    assert day["eligible_attempts"] == 1
    assert day["coverage_ratio"] == 0.0
    assert day["measurement_status"] == "collecting"


def test_same_run_retry_can_complete_observed_sources_without_moving_time(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "retry-run",
        [_status("source-a")],
        finished_at=now - timedelta(hours=12),
        path=path,
        coverage_scope="full",
        expected_source_count=2,
    )
    before = build_reliability_report(now=now, path=path)["windows"][0]
    record_terminal_results(
        "retry-run",
        [_status("source-a"), _status("source-b")],
        finished_at=now - timedelta(hours=12),
        path=path,
        coverage_scope="full",
        expected_source_count=2,
    )
    after = build_reliability_report(now=now, path=path)["windows"][0]

    assert before["coverage_ratio"] == 0.0
    assert before["observed_eligible_attempts"] == 1
    assert before["missing_terminal_windows"] == 1
    assert before["eligible_attempts"] == 2
    assert after["coverage_ratio"] == 1.0
    assert after["observed_eligible_attempts"] == 2
    assert after["missing_terminal_windows"] == 0
    assert after["eligible_attempts"] == 2


def test_empty_report_never_claims_one_hundred_percent(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    report = build_reliability_report(now=now, path=tmp_path / "missing.sqlite3")

    assert report["coverage_started_at"] is None
    for window in report["windows"]:
        assert window["observed_eligible_attempts"] == 0
        assert window["missing_terminal_windows"] == 0
        assert window["eligible_attempts"] == 0
        assert window["full_fresh_rate_pct"] is None
        assert window["accepted_fresh_rate_pct"] is None
        assert window["data_available_rate_pct"] is None
        assert window["measurement_status"] == "collecting"
        assert window["freshness_slo"]["objective_status"] == "collecting"
        assert window["freshness_slo"]["error_budget_consumed_pct"] is None


def test_schema_migrates_existing_database_without_losing_rows(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    connection = sqlite3.connect(path)
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
            "old",
            "old-run",
            "old-source",
            now.timestamp(),
            "lkg_served",
            "ok",
            now.timestamp(),
        ),
    )
    connection.commit()
    connection.close()

    record_terminal_results(
        "new-run",
        [
            _status(
                "new-source",
                serving_cached_dataset=True,
                last_refresh_failure_class="proxy_402",
            )
        ],
        finished_at=now,
        path=path,
    )

    day = build_reliability_report(now=now, path=path)["windows"][0]
    assert day["counts"]["lkg_served"] == 2
    assert day["failure_reasons"]["proxy_payment"] == 1
    assert day["failure_reasons"]["unknown"] == 1
    assert day["physical_attempts"] == 2
    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(source_attempts)")
        }
        legacy_window = connection.execute(
            "SELECT refresh_window_id FROM source_attempts WHERE attempt_id = 'old'"
        ).fetchone()
    assert "refresh_window_id" in columns
    assert legacy_window == ("",)


def test_schema_migration_backfills_refresh_run_window_from_attempts(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    window_id = "pipeline:2026-08-11"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE source_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            refresh_window_id TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL,
            finished_at REAL NOT NULL,
            outcome TEXT NOT NULL,
            terminal_state TEXT NOT NULL,
            recorded_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO source_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "old-attempt",
            "old-run",
            window_id,
            "old-source",
            now.timestamp(),
            "fresh_published",
            "ok",
            now.timestamp(),
        ),
    )
    connection.execute(
        """
        CREATE TABLE refresh_runs (
            run_id TEXT PRIMARY KEY,
            finished_at REAL NOT NULL,
            scope TEXT NOT NULL,
            cohort_hash TEXT NOT NULL DEFAULT '',
            expected_sources INTEGER NOT NULL,
            observed_sources INTEGER NOT NULL,
            recorded_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO refresh_runs VALUES (?, ?, 'partial', '', 1, 1, ?)",
        ("old-run", now.timestamp(), now.timestamp()),
    )
    connection.commit()
    connection.close()

    day = build_reliability_report(now=now, path=path)["windows"][0]

    assert day["missing_terminal_windows"] == 0
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT refresh_window_id FROM refresh_runs WHERE run_id = 'old-run'"
        ).fetchone()
    assert stored == (window_id,)


def test_schema_migration_is_serialized_across_processes(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE source_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            finished_at REAL NOT NULL,
            outcome TEXT NOT NULL,
            terminal_state TEXT NOT NULL,
            reason_code TEXT NOT NULL DEFAULT 'unknown',
            recorded_at REAL NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    tasks = [(str(path), index, now.timestamp()) for index in range(12)]
    with ProcessPoolExecutor(
        max_workers=6,
        mp_context=get_context("fork"),
    ) as pool:
        inserted = list(pool.map(_record_process_attempt, tasks))

    assert inserted == [1] * len(tasks)
    report = build_reliability_report(now=now, path=path)
    assert report["windows"][0]["eligible_attempts"] == len(tasks)


def test_ai_quality_reports_coverage_but_never_claims_unlabeled_accuracy(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "ai-run",
        [
            _status(
                "fresh-reviewed",
                ai_review={
                    "state": "ok",
                    "verdict": "pass",
                    "quarantine": False,
                },
            ),
            _status(
                "fresh-ai-error",
                ai_review={"state": "error", "error_type": "transport_error"},
            ),
            _status(
                "lkg-diagnosed",
                serving_cached_dataset=True,
                last_refresh_error="Dataset regression",
                latest_ai_diagnosis={
                    "state": "ok",
                    "classification": "anomalous",
                    "failure_domain": "regression",
                },
            ),
            _status(
                "lkg-undiagnosed",
                serving_cached_dataset=True,
                last_refresh_error="upstream unavailable",
            ),
        ],
        finished_at=now,
        path=path,
    )

    ai_quality = build_reliability_report(now=now, path=path)["windows"][0][
        "ai_quality"
    ]
    assert ai_quality["candidate_review"] == {
        "all_parser_attempts": 4,
        "attempted": 2,
        "completed": 1,
        "errors": 1,
        "coverage_of_all_parser_attempts_pct": 25.0,
        "valid_response_rate_pct": 50.0,
        "verdicts": {"pass": 1, "fail": 0, "uncertain": 0},
        "quarantined": 0,
    }
    assert ai_quality["failure_diagnosis"]["all_problem_attempts"] == 2
    assert ai_quality["failure_diagnosis"]["completed"] == 1
    assert (
        ai_quality["failure_diagnosis"]["coverage_of_all_problem_attempts_pct"] == 50.0
    )
    assert ai_quality["failure_diagnosis"]["classifications"]["anomalous"] == 1
    assert ai_quality["failure_diagnosis"]["failure_domains"]["regression"] == 1
    assert ai_quality["calibration"] == {
        "status": "not_calibrated",
        "human_labeled_examples": 0,
        "precision_pct": None,
        "recall_pct": None,
        "false_positive_rate_pct": None,
        "limitation": "human_labels_not_collected",
    }


def test_deferred_ai_fields_update_existing_terminal_attempt(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    status = _status("fresh-reviewed-later")
    record_terminal_results(
        "deferred-ai-run",
        [status],
        finished_at=now,
        path=path,
    )
    status["ai_review"] = {
        "state": "ok",
        "verdict": "pass",
        "quarantine": False,
    }

    updated = update_terminal_ai_results(
        "deferred-ai-run",
        [status],
        path=path,
    )
    candidate = build_reliability_report(now=now, path=path)["windows"][0][
        "ai_quality"
    ]["candidate_review"]

    assert updated == 1
    assert candidate["completed"] == 1
    assert candidate["verdicts"]["pass"] == 1


def test_concurrent_process_style_writers_do_not_lose_attempts(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"

    def write(index: int) -> None:
        record_terminal_results(
            f"run-{index}",
            [_status(f"source-{index}")],
            finished_at=now,
            path=path,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))

    report = build_reliability_report(now=now, path=path)
    assert report["windows"][0]["eligible_attempts"] == 40


def test_cache_revision_changes_when_new_attempt_is_recorded(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    assert reliability_cache_revision(path=path) == "not-collected"

    record_terminal_results("run-1", [_status("source-1")], finished_at=now, path=path)
    first = reliability_cache_revision(path=path)
    record_terminal_results("run-2", [_status("source-2")], finished_at=now, path=path)
    second = reliability_cache_revision(path=path)

    assert first != "not-collected"
    assert second != first


def test_public_report_remains_readable_during_concurrent_writer(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "initial-full",
        [_status("source")],
        finished_at=now - timedelta(hours=1),
        path=path,
        coverage_scope="full",
        expected_source_count=1,
    )
    writer = sqlite3.connect(path, timeout=1.0, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        report = build_reliability_report(now=now, path=path)
        elapsed = time.monotonic() - started
    finally:
        writer.rollback()
        writer.close()

    assert report["windows"][0]["eligible_attempts"] == 1
    assert elapsed < 1.0


def test_public_report_has_only_bounded_aggregate_fields(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    path = tmp_path / "reliability.sqlite3"
    record_terminal_results(
        "run-secret",
        [
            _status(
                "source-public",
                url="https://example.test/private?token=secret",
                detail="cookie=secret",
                error="secret stack",
            )
        ],
        finished_at=now,
        path=path,
    )

    serialized = str(build_reliability_report(now=now, path=path))

    assert "token=secret" not in serialized
    assert "cookie=secret" not in serialized
    assert "secret stack" not in serialized
    assert "run-secret" not in serialized
    assert "source-public" not in serialized


def test_preflight_failure_records_terminal_failures_for_selected_sources() -> None:
    source_id = "hsguru_meta_standard_legend"
    with (
        patch("app.refresh_context.begin_refresh_run"),
        patch("app.refresh_context.end_refresh_run"),
        patch(
            "app.preflight.ensure_refresh_preflight",
            new=AsyncMock(side_effect=RuntimeError("preflight unavailable")),
        ),
        patch("app.fetcher.log_action"),
        patch("app.fetcher._record_reliability_results_best_effort") as record,
        pytest.raises(RuntimeError, match="preflight unavailable"),
    ):
        asyncio.run(_refresh_sources_unlocked([source_id]))

    record.assert_called_once()
    run_id, results = record.call_args.args
    assert isinstance(run_id, str) and run_id
    assert results == [
        {
            "source_id": source_id,
            "state": "fetch_error",
            "failure_reason_code": "preflight",
        }
    ]


def test_disabled_sections_cannot_be_recorded_as_a_full_refresh() -> None:
    selected_sources = [
        SOURCE_BY_ID["hsreplay_battlegrounds_heroes"],
        SOURCE_BY_ID["hearthstone_decks"],
    ]
    with (
        patch("app.fetcher.SOURCES", selected_sources),
        patch("app.refresh_context.begin_refresh_run"),
        patch("app.refresh_context.end_refresh_run"),
        patch(
            "app.parser_control.enabled_section_ids",
            return_value={"battlegrounds-heroes"},
        ),
        patch(
            "app.preflight.ensure_refresh_preflight",
            new=AsyncMock(side_effect=RuntimeError("preflight unavailable")),
        ),
        patch("app.fetcher.log_action"),
        patch("app.fetcher._record_reliability_results_best_effort") as record,
        pytest.raises(RuntimeError, match="preflight unavailable"),
    ):
        asyncio.run(_refresh_sources_unlocked(respect_section_controls=True))

    record.assert_called_once()
    _run_id, results = record.call_args.args
    assert [result["source_id"] for result in results] == [
        "hsreplay_battlegrounds_heroes"
    ]
    assert record.call_args.kwargs == {
        "coverage_scope": "partial",
        "expected_source_count": 1,
    }


def test_prefetch_failure_records_terminal_failures_and_ends_context() -> None:
    source_id = "firestone_battlegrounds_comps"
    with (
        patch("app.refresh_context.begin_refresh_run"),
        patch("app.refresh_context.end_refresh_run") as end_refresh,
        patch(
            "app.preflight.ensure_refresh_preflight",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.cards_index.prefetch_hearthstonejson_async",
            new=AsyncMock(side_effect=RuntimeError("card index unavailable")),
        ),
        patch("app.fetcher.log_action"),
        patch("app.fetcher._record_reliability_results_best_effort") as record,
        pytest.raises(RuntimeError, match="card index unavailable"),
    ):
        asyncio.run(_refresh_sources_unlocked([source_id]))

    record.assert_called_once()
    _run_id, results = record.call_args.args
    assert results == [
        {
            "source_id": source_id,
            "state": "fetch_error",
            "failure_reason_code": "dependency",
        }
    ]
    end_refresh.assert_called_once()


def test_serial_browser_phase_keeps_completed_results_after_one_source_raises() -> None:
    sources = [
        Source(
            id=f"serial-source-{index}",
            url=f"https://example.test/{index}",
            site="example",
            category="test",
        )
        for index in range(3)
    ]

    async def fetch(_client: object, source: Source) -> dict[str, object]:
        if source.id == "serial-source-1":
            raise RuntimeError("one source failed")
        return {"source_id": source.id, "state": "ok"}

    with (
        patch("app.fetcher.fetch_source", side_effect=fetch),
        patch(
            "app.fetcher._save_failure_status",
            side_effect=lambda _source, status: status,
        ),
    ):
        results = asyncio.run(
            _run_tier_serial_browser(
                sources,
                phase="browser-test",
                client=None,
                proxy_info={},
                use_flaresolverr=False,
                apply_delay=False,
            )
        )

    assert [result["source_id"] for result in results] == [
        "serial-source-0",
        "serial-source-1",
        "serial-source-2",
    ]
    assert [result["state"] for result in results] == ["ok", "fetch_error", "ok"]
