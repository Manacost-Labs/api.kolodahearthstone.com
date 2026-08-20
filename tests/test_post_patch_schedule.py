from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = (
    ROOT / "systemd" / "hs-data-api-docker-refresh-post-patch-tierlists.service"
)
TIMER_PATH = ROOT / "systemd" / "hs-data-api-docker-refresh-post-patch-tierlists.timer"


def _scheduled_moments() -> list[datetime]:
    timezone = ZoneInfo("Europe/Warsaw")
    return [
        datetime(2026, 8, day, hour, 20, tzinfo=timezone)
        for day in (20, 21)
        for hour in (0, 5, 10, 15, 20)
    ]


def test_timer_has_a_recurring_schedule_with_no_gap_over_five_hours() -> None:
    timer = TIMER_PATH.read_text(encoding="utf-8")
    moments = _scheduled_moments()

    assert "OnCalendar=*-*-* 00,05,10,15,20:20:00 Europe/Warsaw" in timer
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert all(
        right - left <= timedelta(hours=5)
        for left, right in zip(moments, moments[1:])
    )


def test_timer_survives_reboots_and_contains_no_expired_patch_date() -> None:
    timer = TIMER_PATH.read_text(encoding="utf-8")

    assert "Persistent=true" in timer
    assert "2026-07" not in timer


def test_refresh_retries_failures_and_always_attempts_all_cache_busts() -> None:
    service_lines = SERVICE_PATH.read_text(encoding="utf-8").splitlines()
    service = "\n".join(service_lines)
    cache_busts = [line for line in service_lines if line.startswith("ExecStopPost=")]

    assert "python -m app.cli refresh-post-patch" in service
    assert "HS_ARENA_POST_PATCH_FROM" not in service
    assert "--source" not in service
    assert "Restart=on-failure" in service
    assert len(cache_busts) == 3
    assert all(line.startswith("ExecStopPost=-/usr/bin/curl ") for line in cache_busts)
    assert {line.rsplit("=", 1)[-1] for line in cache_busts} == {
        "hsreplay",
        "heartharena",
        "firestone",
    }


def test_compose_disables_the_legacy_date_bounded_policy() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'HS_ARENA_POST_PATCH_ENABLED: "false"' in compose
    assert "HS_ARENA_POST_PATCH_FROM:" not in compose
    assert "HS_ARENA_POST_PATCH_UNTIL:" not in compose
