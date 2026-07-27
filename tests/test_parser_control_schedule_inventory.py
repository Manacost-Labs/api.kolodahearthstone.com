from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.parser_control import ParserControlStore
from app.parser_control_schedule import (
    SCHEDULE_INVENTORY_VERSION,
    SCHEDULE_INVENTORY_SCHEMA_VERSION,
    build_schedule_inventory,
)
from app.parser_control_registry import SECTION_BY_ID, SOURCE_TO_SECTION
from app.hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
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
    assert inventory["inventoryVersion"] == SCHEDULE_INVENTORY_VERSION
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

    assert scheduled_sources == set(SOURCE_BY_ID)
    assert scheduled_sections == set(SECTION_BY_ID)
    assert set(inventory["sources"]) == set(SOURCE_TO_SECTION)
    assert set(inventory["sections"]) == set(SECTION_BY_ID)


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
    assert _schedule(inventory, "refresh-hsguru-meta-matrix")["nextRunAt"] == (
        "2026-07-21T10:00:00+00:00"
    )
    assert _schedule(inventory, "refresh-hsreplay-card-periods")["nextRunAt"] == (
        "2026-07-21T00:35:00+00:00"
    )
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
        "2026-07-21T05:00:00+00:00"
    )


def test_expired_bounded_schedule_remains_in_inventory_without_a_next_run() -> None:
    inventory = build_schedule_inventory(
        at=datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
        include_runtime=False,
    )

    bounded = _schedule(inventory, "refresh-post-patch-tierlists")
    assert bounded["nextRunAt"] is None
    assert bounded["validUntil"] == "2026-07-27T21:20:00+02:00"
    assert bounded["isActive"] is False


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
        assert (Path("systemd") / schedule["systemdUnit"]).is_file()


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
        assert arena["nextRunAt"] == "2026-07-21T03:20:00+00:00"
        assert advanced["enabled"] is True
        assert advanced["scheduleIds"]
        assert advanced["schedule"]
        assert advanced["nextRunAt"] == "2026-07-21T03:20:00+00:00"

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
