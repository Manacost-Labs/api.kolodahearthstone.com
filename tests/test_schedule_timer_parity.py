"""Schedule inventory, ledger claims and systemd timers must describe one reality.

Regression: the BG compositions screenshot timer fired at 04:10, 10:10, 16:10
and 22:10 while its ledger-tracked schedule only planned 04:10, so every retry
run exited 1 with ``Schedule ledger claim failed: OccurrenceNotFoundError``.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.parser_control_schedule import _SCHEDULES
from app.schedule_ledger import (
    OccurrenceNotFoundError,
    claim_occurrence,
    deterministic_occurrence_id,
    ensure_schema,
    materialize_schedule_windows,
    tracked_schedule_specs,
)

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
_SIMPLE_DAILY = re.compile(r"^\*-\*-\* (\d{2}):(\d{2}):00 Europe/Warsaw$")
_SCHEDULE_ID = re.compile(r"--schedule-id\s+(\S+)")
WARSAW = ZoneInfo("Europe/Warsaw")


def _on_calendar(unit: Path) -> tuple[str, ...]:
    return tuple(
        line.split("=", 1)[1].strip()
        for line in unit.read_text(encoding="utf-8").splitlines()
        if line.startswith("OnCalendar=")
    )


def _claiming_services() -> dict[str, str]:
    """Map each ledger-claiming service's timer unit to its schedule id."""

    claims: dict[str, str] = {}
    for service in sorted(SYSTEMD.glob("hs-data-api-docker-*.service")):
        match = _SCHEDULE_ID.search(service.read_text(encoding="utf-8"))
        if match is not None:
            claims[service.with_suffix(".timer").name] = match.group(1)
    return claims


@pytest.mark.parametrize("spec", _SCHEDULES, ids=lambda spec: spec.id)
def test_schedule_spec_matches_every_timer_calendar_entry(spec) -> None:
    timer = SYSTEMD / spec.systemd_unit

    assert _on_calendar(timer) == spec.on_calendar
    simple = [_SIMPLE_DAILY.fullmatch(value) for value in spec.on_calendar]
    if spec.recurrence == "daily" and all(simple):
        assert (
            tuple(
                time(int(match.group(1)), int(match.group(2)))
                for match in simple
                if match is not None
            )
            == spec.local_times
        )


def test_every_ledger_claim_comes_from_its_own_primary_timer() -> None:
    claims = _claiming_services()
    tracked = {spec.id: spec for spec in tracked_schedule_specs()}

    assert claims
    for timer_name, schedule_id in claims.items():
        assert schedule_id in tracked, timer_name
        assert tracked[schedule_id].systemd_unit == timer_name
    recovery_units = {
        spec.systemd_unit for spec in _SCHEDULES if spec.purpose == "recovery"
    }
    assert recovery_units.isdisjoint(claims)


def test_bg_screenshot_retries_do_not_claim_primary_ledger_occurrences(
    tmp_path: Path,
) -> None:
    primary = next(
        spec
        for spec in tracked_schedule_specs()
        if spec.id == "capture-bg-compositions-screenshot"
    )
    retry = next(
        spec for spec in _SCHEDULES if spec.id == "retry-bg-compositions-screenshot"
    )
    assert retry.purpose == "recovery"
    assert retry.source_ids == primary.source_ids
    assert _claiming_services()[primary.systemd_unit] == primary.id
    assert retry.systemd_unit not in _claiming_services()

    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    ensure_schema(connection)
    materialize_schedule_windows(
        connection, now=datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    )
    connection.commit()
    connection.close()

    primary_fire = datetime(2026, 10, 20, 4, 10, tzinfo=WARSAW)
    assert claim_occurrence(
        primary.id,
        sorted(primary.source_ids),
        now=primary_fire.astimezone(UTC),
        path=path,
    ) == deterministic_occurrence_id(primary.id, primary_fire)

    # Retry slots are deliberately outside the primary obligation window; the
    # retry unit therefore runs without --schedule-id instead of failing here.
    with pytest.raises(OccurrenceNotFoundError):
        claim_occurrence(
            primary.id,
            sorted(primary.source_ids),
            now=datetime(2026, 10, 20, 10, 10, tzinfo=WARSAW).astimezone(UTC),
            path=path,
        )
