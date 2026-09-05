from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from app.parser_control import ParserControlStore
from app.parser_control_registry import SECTION_BY_ID, SOURCE_TO_SECTION
from app.parser_control_schedule import (
    SCHEDULE_INVENTORY_SCHEMA_VERSION,
    SCHEDULE_INVENTORY_VERSION,
    _next_run,
    _ScheduleSpec,
    build_schedule_inventory,
)
from app.sources import SOURCE_BY_ID


def _schedule(inventory: dict[str, object], schedule_id: str) -> dict[str, object]:
    schedules = inventory["schedules"]
    assert isinstance(schedules, list)
    return next(row for row in schedules if row["id"] == schedule_id)


def test_schedule_inventory_is_versioned_and_covers_every_parser_source_and_section() -> None:
    inventory = build_schedule_inventory(
        at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        include_runtime=False,
    )

    assert inventory["schemaVersion"] == SCHEDULE_INVENTORY_SCHEMA_VERSION == 2
    assert inventory["inventoryVersion"] == SCHEDULE_INVENTORY_VERSION == "2026-08-27.1"
    assert inventory["generatedAt"] == "2026-07-21T00:00:00+00:00"
    assert inventory["timeSemantics"] == "nominal"

    schedules = inventory["schedules"]
    assert isinstance(schedules, list)
    scheduled_sources = {
        source_id
        for schedule in schedules
        for source_id in schedule["sourceIds"]
    }
    scheduled_sections = {
        section_id
        for schedule in schedules
        for section_id in schedule["sectionIds"]
    }

    assert scheduled_sources == set(SOURCE_BY_ID) - {"firestone_standard"}
    assert scheduled_sections == set(SECTION_BY_ID)
    assert set(inventory["sources"]) == set(SOURCE_TO_SECTION)
    assert set(inventory["sections"]) == set(SECTION_BY_ID)


def test_schedule_inventory_adds_firestone_standard_only_after_authorized_opt_in() -> None:
    with patch.dict(
        "os.environ",
        {"HS_FIRESTONE_STANDARD_AUTHORIZED": "true"},
        clear=True,
    ):
        inventory = build_schedule_inventory(
            at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
            include_runtime=False,
        )

    scheduled_sources = {
        source_id
        for schedule in inventory["schedules"]
        for source_id in schedule["sourceIds"]
    }
    assert "firestone_standard" in scheduled_sources
    assert inventory["sources"]["firestone_standard"]["nextRunAt"] is not None


def test_schedule_inventory_calculates_nominal_next_runs_in_utc() -> None:
    inventory = build_schedule_inventory(
        at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        include_runtime=False,
    )

    assert _schedule(inventory, "refresh-all-daily")["nextRunAt"] == (
        "2026-07-21T05:00:00+00:00"
    )
    assert _schedule(inventory, "refresh-vicious-syndicate")["nextRunAt"] == (
        "2026-07-21T00:20:00+00:00"
    )
    assert _schedule(inventory, "refresh-streamer-decks")["nextRunAt"] == (
        "2026-07-21T00:15:00+00:00"
    )
    streamer_recovery = _schedule(inventory, "recover-hsguru-streamer")
    assert streamer_recovery["purpose"] == "recovery"
    assert streamer_recovery["onCalendar"] == [
        "*-*-* *:00/10:00 Europe/Warsaw"
    ]
    assert streamer_recovery["sourceIds"] == [
        "hsguru_streamer_decks_legend_1000"
    ]
    assert streamer_recovery["nextRunAt"] == "2026-07-21T00:10:00+00:00"
    assert _schedule(inventory, "refresh-hsguru-meta-matrix")["nextRunAt"] == (
        "2026-07-21T10:00:00+00:00"
    )
    assert _schedule(inventory, "refresh-hsreplay-card-periods")["nextRunAt"] == (
        "2026-07-21T00:35:00+00:00"
    )
    screenshot = _schedule(inventory, "capture-bg-compositions-screenshot")
    assert screenshot["onCalendar"] == [
        "*-*-* 04:10:00 Europe/Warsaw",
        "*-*-* 10:10:00 Europe/Warsaw",
        "*-*-* 16:10:00 Europe/Warsaw",
        "*-*-* 22:10:00 Europe/Warsaw",
    ]
    assert screenshot["nextRunAt"] == "2026-07-21T02:10:00+00:00"
    assert set(_schedule(inventory, "refresh-hsreplay-card-periods")["sourceIds"]) == set(
        HSREPLAY_CARD_PERIOD_SOURCE_IDS
    )
    assert _schedule(inventory, "refresh-hsreplay-archetypes")["nextRunAt"] == (
        "2026-07-23T01:20:00+00:00"
    )
    assert _schedule(inventory, "refresh-post-patch-tierlists")["nextRunAt"] == (
        "2026-07-21T03:20:00+00:00"
    )

    sources = inventory["sources"]
    assert sources["vicious_syndicate_live_beta"]["nextRunAt"] == (
        "2026-07-21T00:20:00+00:00"
    )
    assert sources["hsreplay_arena_cards_advanced"]["nextRunAt"] == (
        "2026-07-21T03:20:00+00:00"
    )
    assert sources["hsguru_meta_standard_legend"]["nextRunAt"] == (
        "2026-07-21T03:20:00+00:00"
    )
    streamer = sources["hsguru_streamer_decks_legend_1000"]
    assert streamer["nextRunAt"] == "2026-07-21T00:15:00+00:00"
    assert streamer["recoveryScheduleIds"] == ["recover-hsguru-streamer"]
    assert streamer["nextRecoveryRunAt"] == "2026-07-21T00:10:00+00:00"


def test_nominal_next_run_skips_nonexistent_warsaw_dst_time() -> None:
    spec = _ScheduleSpec(
        id="dst-test",
        label="DST test",
        systemd_unit="dst-test.timer",
        on_calendar=("*-*-* 02:45:00 Europe/Warsaw",),
        source_ids=frozenset({"source"}),
        recurrence="daily",
        local_times=(time(2, 45),),
    )

    assert _next_run(spec, at=datetime(2026, 3, 29, 0, 0, tzinfo=UTC)) == datetime(
        2026,
        3,
        30,
        0,
        45,
        tzinfo=UTC,
    )


def test_post_patch_schedule_recurs_and_is_conditionally_active() -> None:
    inventory = build_schedule_inventory(
        at=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        include_runtime=False,
        publication_mode="early",
    )

    recurring = _schedule(inventory, "refresh-post-patch-tierlists")
    assert recurring["nextRunAt"] == "2026-08-20T03:20:00+00:00"
    assert recurring["validUntil"] is None
    assert recurring["requiredPublicationMode"] == "early"
    assert recurring["conditionMet"] is True
    assert recurring["isActive"] is True

    stable_inventory = build_schedule_inventory(
        at=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        include_runtime=False,
        publication_mode="stable",
    )
    inactive = _schedule(stable_inventory, "refresh-post-patch-tierlists")
    assert inactive["nextRunAt"] is None
    assert inactive["conditionMet"] is False
    assert inactive["isActive"] is False


def test_every_inventory_unit_is_a_versioned_docker_timer() -> None:
    inventory = build_schedule_inventory(
        at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        include_runtime=False,
    )

    for schedule in inventory["schedules"]:
        assert schedule["systemdUnit"].startswith("hs-data-api-docker-")
        assert schedule["systemdUnit"].endswith(".timer")
        assert schedule["onCalendar"]
        assert schedule["sourceIds"]
        assert schedule["sectionIds"]
        timer_path = Path("systemd") / schedule["systemdUnit"]
        assert timer_path.is_file()
        timer_calendars = [
            line.removeprefix("OnCalendar=")
            for line in timer_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("OnCalendar=")
        ]
        assert schedule["onCalendar"] == timer_calendars, schedule["id"]


def test_parser_control_snapshot_exposes_effective_section_and_source_schedule() -> None:
    at = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    unavailable_runtime = {
        "provider": "test",
        "checkedAt": at.isoformat(),
        "available": False,
        "status": "unavailable",
        "reason": "test",
        "timingAvailable": False,
        "units": {},
    }
    with TemporaryDirectory() as directory, patch(
        "app.parser_control_schedule._probe_systemd_timer_states",
        return_value=unavailable_runtime,
    ):
        store = ParserControlStore(Path(directory))

        snapshot = store.snapshot(at=at)
        arena = next(
            section
            for section in snapshot["sections"]
            if section["id"] == "arena-tier-list"
        )
        advanced = next(
            source
            for source in arena["sources"]
            if source["id"] == "hsreplay_arena_cards_advanced"
        )

        assert snapshot["generatedAt"] == "2026-07-21T00:00:00+00:00"
        assert snapshot["scheduleInventory"]["schemaVersion"] == 2
        assert arena["scheduleIds"]
        assert arena["schedule"]
        assert arena["nextRunAt"] == "2026-07-21T05:00:00+00:00"
        assert advanced["enabled"] is True
        assert advanced["scheduleIds"]
        assert advanced["schedule"]
        assert advanced["nextRunAt"] == "2026-07-21T05:00:00+00:00"

        disabled = store.update_sections(
            expected_revision=1,
            changes={"arena-tier-list": False},
            updated_by="admin:7",
        )
        disabled_arena = next(
            section
            for section in disabled["sections"]
            if section["id"] == "arena-tier-list"
        )

        assert disabled_arena["enabled"] is False
        assert disabled_arena["nextRunAt"] is None
        assert all(source["enabled"] is False for source in disabled_arena["sources"])
        assert all(source["nextRunAt"] is None for source in disabled_arena["sources"])
