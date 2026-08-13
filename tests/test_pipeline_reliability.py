from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import cli
from app.cli import _run_pipeline_command_with_telemetry
from app.parser_control import _run_pipeline_source
from app.reliability_telemetry import (
    build_reliability_report,
    classify_failure_reason,
    classify_terminal_status,
)


def test_pipeline_telemetry_normalizes_fresh_without_mutation() -> None:
    result: dict[str, object] = {"ok": True, "published": True, "rows": 12}

    with patch("app.reliability_telemetry.record_terminal_results") as record:
        returned = _run_pipeline_command_with_telemetry(
            "hsguru_fun_decks",
            lambda: result,
        )

    assert returned is result
    assert result == {"ok": True, "published": True, "rows": 12}
    run_id, statuses = record.call_args.args
    assert run_id.startswith("pipeline:hsguru_fun_decks:")
    assert statuses == [
        {
            "ok": True,
            "published": True,
            "rows": 12,
            "source_id": "hsguru_fun_decks",
            "state": "ok",
        }
    ]


def test_pipeline_telemetry_uses_a_unique_process_safe_run_id() -> None:
    with patch("app.reliability_telemetry.record_terminal_results") as record:
        for _ in range(2):
            _run_pipeline_command_with_telemetry(
                "hsguru_meta_matrix",
                lambda: {"ok": True, "published": True, "state": "ok"},
            )

    run_ids = [call.args[0] for call in record.call_args_list]
    assert len(set(run_ids)) == 2
    assert all(len(run_id.rsplit(":", 1)[-1]) == 32 for run_id in run_ids)


def test_pipeline_telemetry_persists_exactly_one_logical_attempt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reliability.sqlite3"
    with patch(
        "app.reliability_telemetry.telemetry_db_path",
        return_value=path,
    ):
        _run_pipeline_command_with_telemetry(
            "hsguru_fun_decks",
            lambda: {"ok": True, "published": True},
        )
        report = build_reliability_report(path=path)

    day = report["windows"][0]
    assert day["total_attempts"] == 1
    assert day["eligible_attempts"] == 1
    assert day["counts"]["fresh_published"] == 1


def test_pipeline_telemetry_keeps_locked_runs_out_of_the_slo_denominator() -> None:
    result: dict[str, object] = {
        "ok": True,
        "published": False,
        "state": "locked",
        "skipped": True,
        "reason": "resource_locked",
    }

    with patch("app.reliability_telemetry.record_terminal_results") as record:
        _run_pipeline_command_with_telemetry(
            "hsreplay_archetypes",
            lambda: result,
        )

    status = record.call_args.args[1][0]
    assert classify_terminal_status(status) == "skipped"


@pytest.mark.parametrize(
    "result",
    [
        {"ok": True, "diagnostic": True, "state": "ok"},
        {"ok": True, "state": "diagnostic"},
        {"ok": False, "state": "diagnostic_failed"},
    ],
)
def test_pipeline_telemetry_keeps_reported_diagnostics_out_of_slo(
    result: dict[str, object],
) -> None:
    with patch("app.reliability_telemetry.record_terminal_results") as record:
        _run_pipeline_command_with_telemetry(
            "hsreplay_archetypes",
            lambda: result,
        )

    status = record.call_args.args[1][0]
    assert status["state"] == "skipped"
    assert status["reason"] == "diagnostic_run"
    assert classify_terminal_status(status) == "skipped"


def test_pipeline_telemetry_keeps_debug_exception_out_of_slo() -> None:
    def fail() -> dict[str, object]:
        raise TimeoutError

    with (
        patch("app.reliability_telemetry.record_terminal_results") as record,
        pytest.raises(TimeoutError),
    ):
        _run_pipeline_command_with_telemetry(
            "hsreplay_battlegrounds_hero_details",
            fail,
            diagnostic=True,
        )

    status = record.call_args.args[1][0]
    assert status == {
        "source_id": "hsreplay_battlegrounds_hero_details",
        "state": "skipped",
        "skipped": True,
        "reason": "diagnostic_run",
    }


def test_bg_hero_limit_cli_marks_run_as_diagnostic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result: dict[str, object] = {
        "ok": False,
        "published": False,
        "state": "partial",
    }
    with (
        patch(
            "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
            new=AsyncMock(return_value=result),
        ),
        patch("app.reliability_telemetry.record_terminal_results") as record,
    ):
        exit_code = cli.main(["refresh-bg-hero-details", "--limit", "1"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == result
    status = record.call_args.args[1][0]
    assert status["state"] == "skipped"
    assert status["reason"] == "diagnostic_run"
    assert classify_terminal_status(status) == "skipped"


def test_pipeline_telemetry_failure_cannot_change_the_command_result() -> None:
    result: dict[str, object] = {"ok": True, "published": True, "state": "ok"}

    with patch(
        "app.reliability_telemetry.record_terminal_results",
        side_effect=OSError("telemetry unavailable"),
    ):
        returned = _run_pipeline_command_with_telemetry(
            "hsreplay_battlegrounds_hero_details",
            lambda: result,
        )

    assert returned is result


def test_pipeline_exception_is_recorded_and_then_re_raised() -> None:
    def fail() -> dict[str, object]:
        raise RuntimeError("Firecrawl response did not include screenshot")

    with (
        patch("app.reliability_telemetry.record_terminal_results") as record,
        pytest.raises(RuntimeError, match="did not include screenshot"),
    ):
        _run_pipeline_command_with_telemetry(
            "hsreplay_battlegrounds_compositions_screenshot",
            fail,
        )

    status = record.call_args.args[1][0]
    assert status == {
        "source_id": "hsreplay_battlegrounds_compositions_screenshot",
        "state": "quality_error",
        "failure_reason_code": "contract",
    }
    assert classify_terminal_status(status) == "failed"


@pytest.mark.parametrize("reason", ["upstream_5xx", "timeout", "contract"])
def test_dedicated_pipeline_preserves_explicit_bounded_failure_reason(
    reason: str,
) -> None:
    result: dict[str, object] = {
        "ok": False,
        "state": "fetch_error",
        "failure_reason_code": reason,
    }

    with patch("app.reliability_telemetry.record_terminal_results") as record:
        _run_pipeline_command_with_telemetry("hsguru_fun_decks", lambda: result)

    status = record.call_args.args[1][0]
    assert status["failure_reason_code"] == reason
    assert classify_failure_reason(status) == reason


def test_dedicated_pipeline_forwards_logical_refresh_window() -> None:
    with patch("app.reliability_telemetry.record_terminal_results") as record:
        _run_pipeline_command_with_telemetry(
            "hsguru_fun_decks",
            lambda: {"ok": True, "published": True},
            refresh_window_id="hsguru-fun-decks:2026-08-11",
        )

    assert record.call_args.kwargs == {
        "refresh_window_id": "hsguru-fun-decks:2026-08-11"
    }


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (
            {
                "ok": True,
                "image_path": "/tmp/capture.txt",
                "image_bytes": 100,
            },
            "failed",
        ),
        (
            {
                "ok": True,
                "image_path": "/tmp/capture.png",
                "image_bytes": 100,
                "image_mime": "image/png",
            },
            "fresh_published",
        ),
    ],
)
def test_screenshot_is_fresh_only_after_validated_image_capture(
    result: dict[str, object],
    outcome: str,
) -> None:
    with patch("app.reliability_telemetry.record_terminal_results") as record:
        _run_pipeline_command_with_telemetry(
            "hsreplay_battlegrounds_compositions_screenshot",
            lambda: result,
        )

    status = record.call_args.args[1][0]
    assert classify_terminal_status(status) == outcome


def test_parser_control_runs_hsguru_archetype_analysis_pipeline() -> None:
    refresh = AsyncMock(return_value={"ok": True, "state": "ok", "archetypes": 42})
    with patch(
        "app.hsguru_archetype_analysis.refresh_hsguru_archetype_analysis",
        new=refresh,
    ):
        result = asyncio.run(_run_pipeline_source("hsguru_archetype_analysis"))

    assert result["state"] == "ok"
    assert result["rows_total"] == 42


def test_parser_control_runs_bg_compositions_screenshot_pipeline() -> None:
    capture = AsyncMock(
        return_value={
            "ok": True,
            "image_path": "/tmp/capture.png",
            "image_bytes": 100,
            "image_mime": "image/png",
        }
    )
    with patch(
        "app.hsreplay_bg_screenshots.capture_compositions_screenshot",
        new=capture,
    ):
        result = asyncio.run(
            _run_pipeline_source("hsreplay_battlegrounds_compositions_screenshot")
        )

    assert result["state"] == "ok"
