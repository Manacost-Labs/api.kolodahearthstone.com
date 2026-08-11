from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
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
)
from app.sources import Source


def _status(source_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {"source_id": source_id, "state": "ok"}
    value.update(overrides)
    return value


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


def test_report_has_observed_24h_7d_and_30d_boundaries(tmp_path: Path) -> None:
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
    assert report["windows"][0]["measurement_status"] == "observed"
    assert report["windows"][1]["measurement_status"] == "observed"
    assert report["windows"][2]["measurement_status"] == "observed"
    assert report["methodology"]["scope"] == "generic_refresh_sources"
    assert report["methodology"]["completeness"] == "observed_attempts_only"
    assert "dedicated_pipeline_sources_excluded" in report["methodology"]["limitations"]
    assert report["coverage_started_at"] == (now - timedelta(days=31)).isoformat()
    assert report["windows"][0]["freshness_slo"]["objective_status"] == "meeting"


def test_empty_report_never_claims_one_hundred_percent(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    report = build_reliability_report(now=now, path=tmp_path / "missing.sqlite3")

    assert report["coverage_started_at"] is None
    for window in report["windows"]:
        assert window["eligible_attempts"] == 0
        assert window["full_fresh_rate_pct"] is None
        assert window["accepted_fresh_rate_pct"] is None
        assert window["data_available_rate_pct"] is None
        assert window["measurement_status"] == "collecting"
        assert window["freshness_slo"]["objective_status"] == "collecting"
        assert window["freshness_slo"]["error_budget_consumed_pct"] is None


def test_schema_migrates_existing_database_without_losing_rows(tmp_path: Path) -> None:
    import sqlite3

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
        ("old", "old-run", "old-source", now.timestamp(), "lkg_served", "ok", now.timestamp()),
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
