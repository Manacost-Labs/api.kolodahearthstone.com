from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.recovery_condition import main, recovery_decision

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SOURCE_ID = "hsguru_streamer_decks_legend_1000"


def _lkg_status(*, age: timedelta) -> dict[str, object]:
    return {
        "source_id": SOURCE_ID,
        "state": "ok",
        "serving_cached_dataset": True,
        "last_refresh_state": "quality_error",
        "last_refresh_at": (NOW - age).isoformat(),
    }


def test_recovery_waits_five_minutes_after_lkg() -> None:
    too_early = recovery_decision(
        _lkg_status(age=timedelta(minutes=4, seconds=59)),
        now=NOW,
        min_age_seconds=300,
    )
    ready = recovery_decision(
        _lkg_status(age=timedelta(minutes=5)),
        now=NOW,
        min_age_seconds=300,
    )

    assert too_early["run"] is False
    assert too_early["reason"] == "minimum_age_not_reached"
    assert ready["run"] is True
    assert ready["reason"] == "latest_attempt_failed"


def test_recovery_skips_after_fresh_refresh() -> None:
    decision = recovery_decision(
        {
            "source_id": SOURCE_ID,
            "state": "ok",
            "serving_cached_dataset": False,
            "fetched_at": (NOW - timedelta(minutes=30)).isoformat(),
        },
        now=NOW,
        min_age_seconds=300,
    )

    assert decision["run"] is False
    assert decision["reason"] == "latest_attempt_fresh"


def test_recovery_accepts_a_direct_failure_and_fails_closed_on_bad_time() -> None:
    failed = recovery_decision(
        {
            "source_id": SOURCE_ID,
            "state": "http_error",
            "fetched_at": (NOW - timedelta(minutes=8)).isoformat(),
        },
        now=NOW,
        min_age_seconds=300,
    )
    malformed = recovery_decision(
        {
            "source_id": SOURCE_ID,
            "state": "http_error",
            "fetched_at": "not-a-timestamp",
        },
        now=NOW,
        min_age_seconds=300,
    )

    assert failed["run"] is True
    assert malformed["run"] is False
    assert malformed["reason"] == "invalid_attempt_time"


def test_recovery_accepts_an_explicit_lkg_with_a_custom_failure_state() -> None:
    status = _lkg_status(age=timedelta(minutes=6))
    status["last_refresh_state"] = "cache_sync_error"

    decision = recovery_decision(status, now=NOW, min_age_seconds=300)

    assert decision["run"] is True
    assert decision["attempt_state"] == "cache_sync_error"


def test_recovery_cli_reads_status_and_returns_condition_exit_code(
    tmp_path, monkeypatch, capsys
) -> None:
    status_dir = tmp_path / "statuses"
    status_dir.mkdir()
    (status_dir / f"{SOURCE_ID}.json").write_text(
        json.dumps(_lkg_status(age=timedelta(minutes=10))),
        encoding="utf-8",
    )
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.recovery_condition._utc_now", lambda: NOW)

    exit_code = main(["--source", SOURCE_ID, "--min-age-seconds", "300"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["run"] is True
    assert payload["source_id"] == SOURCE_ID
