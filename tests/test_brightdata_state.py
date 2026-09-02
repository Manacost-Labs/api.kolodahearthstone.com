from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.brightdata_state import (
    BrightDataBudgetExceeded,
    BrightDataCircuitOpen,
    BrightDataStateError,
    finish_request,
    initialize_usage_state,
    reserve_request,
)

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 300


def _initialize(
    *,
    monthly_limit: int,
    billed_requests: int = 0,
    now: datetime | None = None,
) -> None:
    initialize_usage_state(
        monthly_limit=monthly_limit,
        billed_requests=billed_requests,
        now=now,
    )


def _reserve_in_process(data_dir: str) -> str | None:
    os.environ["HS_API_DATA_DIR"] = data_dir
    try:
        return reserve_request(
            monthly_limit=3,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
        ).reservation_id
    except (BrightDataBudgetExceeded, BrightDataCircuitOpen):
        return None


def test_budget_reservation_and_completion_are_persisted(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    with patch("app.brightdata_state.usage_path", return_value=path):
        _initialize(monthly_limit=1, now=now)
        reservation = reserve_request(
            monthly_limit=1,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=now,
        )
        with pytest.raises(BrightDataBudgetExceeded, match="monthly budget exhausted"):
            reserve_request(
                monthly_limit=1,
                circuit_failure_threshold=FAILURE_THRESHOLD,
                circuit_cooldown_seconds=COOLDOWN_SECONDS,
                now=now,
            )

        snapshot = finish_request(
            reservation.reservation_id,
            monthly_limit=1,
            billed=True,
            succeeded=True,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=300,
            now=now,
        )

    assert snapshot.billed_requests == 1
    assert snapshot.remaining_requests == 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["period_id"] == "2026-08"
    assert raw["billed_requests"] == 1
    assert raw["attempts"] == 1
    assert raw["successful_requests"] == 1
    assert raw["reservations"] == {}


def test_circuit_opens_after_consecutive_provider_failures(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    with patch("app.brightdata_state.usage_path", return_value=path):
        _initialize(monthly_limit=10, now=now)
        for _ in range(2):
            reservation = reserve_request(
                monthly_limit=10,
                circuit_failure_threshold=2,
                circuit_cooldown_seconds=60,
                now=now,
            )
            finish_request(
                reservation.reservation_id,
                monthly_limit=10,
                billed=False,
                succeeded=False,
                circuit_failure_threshold=2,
                circuit_cooldown_seconds=60,
                now=now,
            )

        with pytest.raises(BrightDataCircuitOpen, match="circuit is open"):
            reserve_request(
                monthly_limit=10,
                circuit_failure_threshold=2,
                circuit_cooldown_seconds=60,
                now=now + timedelta(seconds=59),
            )

        recovered = reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=2,
            circuit_cooldown_seconds=60,
            now=now + timedelta(seconds=61),
        )
        with pytest.raises(BrightDataCircuitOpen, match="circuit is open"):
            reserve_request(
                monthly_limit=10,
                circuit_failure_threshold=2,
                circuit_cooldown_seconds=60,
                now=now + timedelta(seconds=61),
            )

        finish_request(
            recovered.reservation_id,
            monthly_limit=10,
            billed=False,
            succeeded=True,
            circuit_failure_threshold=2,
            circuit_cooldown_seconds=60,
            now=now + timedelta(seconds=61),
        )
        after_recovery = reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=2,
            circuit_cooldown_seconds=60,
            now=now + timedelta(seconds=61),
        )

    assert recovered.reservation_id
    assert after_recovery.reservation_id


def test_corrupt_budget_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text("not-json", encoding="utf-8")

    with (
        patch("app.brightdata_state.usage_path", return_value=path),
        pytest.raises(BrightDataStateError, match="usage state is unavailable"),
    ):
        reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
        )


def test_monthly_reservations_are_interprocess_safe(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"HS_API_DATA_DIR": str(tmp_path)}):
        initialize_usage_state(monthly_limit=3)
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=8, mp_context=context) as pool:
        reservations = list(pool.map(_reserve_in_process, [str(tmp_path)] * 8))

    assert len([item for item in reservations if item is not None]) == 3
    raw = json.loads(
        (tmp_path / "brightdata" / "usage.json").read_text(encoding="utf-8")
    )
    assert len(raw["reservations"]) == 3
    assert raw["billed_requests"] == 0


def test_missing_usage_state_fails_closed_until_explicit_bootstrap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "usage.json"
    with (
        patch("app.brightdata_state.usage_path", return_value=path),
        pytest.raises(BrightDataStateError, match="usage state is unavailable"),
    ):
        reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
        )

    with patch("app.brightdata_state.usage_path", return_value=path):
        snapshot = initialize_usage_state(monthly_limit=10, billed_requests=4)
        reservation = reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
        )
        with pytest.raises(BrightDataStateError, match="already initialized"):
            initialize_usage_state(monthly_limit=10)

    assert snapshot.billed_requests == 4
    assert reservation.snapshot.remaining_requests == 5


def test_initial_parallel_wave_is_limited_by_failure_threshold(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=20, now=now)
        reservations = [
            reserve_request(
                monthly_limit=20,
                circuit_failure_threshold=3,
                circuit_cooldown_seconds=300,
                now=now,
            )
            for _ in range(3)
        ]
        with pytest.raises(BrightDataCircuitOpen, match="circuit is open"):
            reserve_request(
                monthly_limit=20,
                circuit_failure_threshold=3,
                circuit_cooldown_seconds=300,
                now=now,
            )

    assert len(reservations) == 3


def test_failure_reduces_remaining_in_flight_capacity(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=20, now=now)
        first = reserve_request(
            monthly_limit=20,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=300,
            now=now,
        )
        finish_request(
            first.reservation_id,
            monthly_limit=20,
            billed=False,
            succeeded=False,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=300,
            now=now,
        )
        second = reserve_request(
            monthly_limit=20,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=300,
            now=now,
        )
        third = reserve_request(
            monthly_limit=20,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=300,
            now=now,
        )
        with pytest.raises(BrightDataCircuitOpen, match="circuit is open"):
            reserve_request(
                monthly_limit=20,
                circuit_failure_threshold=3,
                circuit_cooldown_seconds=300,
                now=now,
            )

    assert second.reservation_id != third.reservation_id


def test_stale_reservation_counts_as_failure_and_reopens_circuit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=10, now=now)
        reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=1,
            circuit_cooldown_seconds=300,
            now=now,
        )
        with pytest.raises(BrightDataCircuitOpen, match="circuit is open"):
            reserve_request(
                monthly_limit=10,
                circuit_failure_threshold=1,
                circuit_cooldown_seconds=300,
                now=now + timedelta(seconds=3601),
            )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["billed_requests"] == 1
    assert raw["consecutive_failures"] == 1
    assert raw["circuit_open_until"] is not None


def test_fractional_persisted_counters_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=10, now=now)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["billed_requests"] = 1.5
    path.write_text(json.dumps(raw), encoding="utf-8")

    with (
        patch("app.brightdata_state.usage_path", return_value=path),
        pytest.raises(BrightDataStateError, match="usage state is unavailable"),
    ):
        reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=now,
        )


@pytest.mark.parametrize("period_id", ["garbage", "2026-13", "2026-09"])
def test_invalid_or_future_period_fails_closed(
    tmp_path: Path,
    period_id: str,
) -> None:
    path = tmp_path / "usage.json"
    now = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)
    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=10, now=now)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["period_id"] = period_id
    path.write_text(json.dumps(raw), encoding="utf-8")

    with (
        patch("app.brightdata_state.usage_path", return_value=path),
        pytest.raises(BrightDataStateError, match="usage state is unavailable"),
    ):
        reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=now,
        )


def test_month_rollover_preserves_in_flight_reservations(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    august = datetime(2026, 8, 31, 23, 59, 30, tzinfo=UTC)
    september = datetime(2026, 9, 1, 0, 0, 30, tzinfo=UTC)

    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=3, now=august)
        old_reservation = reserve_request(
            monthly_limit=3,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=august,
        )
        new_reservation = reserve_request(
            monthly_limit=3,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=september,
        )
        after_old = finish_request(
            old_reservation.reservation_id,
            monthly_limit=3,
            billed=True,
            succeeded=True,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=september,
        )
        final = finish_request(
            new_reservation.reservation_id,
            monthly_limit=3,
            billed=False,
            succeeded=True,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=september,
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["period_id"] == "2026-09"
    assert after_old.billed_requests == 1
    assert after_old.reserved_requests == 1
    assert final.billed_requests == 1
    assert final.reserved_requests == 0
    assert final.remaining_requests == 2


def test_old_period_corrupt_counters_do_not_reset_on_rollover(
    tmp_path: Path,
) -> None:
    path = tmp_path / "usage.json"
    august = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)
    september = datetime(2026, 9, 1, 0, 1, tzinfo=UTC)
    with patch("app.brightdata_state.usage_path", return_value=path):
        initialize_usage_state(monthly_limit=10, now=august)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["billed_requests"] = "corrupt"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with (
        patch("app.brightdata_state.usage_path", return_value=path),
        pytest.raises(BrightDataStateError, match="usage state is unavailable"),
    ):
        reserve_request(
            monthly_limit=10,
            circuit_failure_threshold=FAILURE_THRESHOLD,
            circuit_cooldown_seconds=COOLDOWN_SECONDS,
            now=september,
        )
