from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .config import data_dir, source_operationally_enabled
from .hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from .parser_control_registry import SOURCE_TO_SECTION
from .post_patch_policy import EARLY_SOURCE_IDS
from .source_tiers import LIGHT_API_IDS, MEDIUM_API_IDS
from .sources import SOURCE_BY_ID

SCHEDULE_INVENTORY_SCHEMA_VERSION = 2
SCHEDULE_INVENTORY_VERSION = "2026-08-27.1"
SCHEDULE_TIMEZONE = "Europe/Warsaw"

_SYSTEMCTL_SEARCH_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_SYSTEMCTL_TIMEOUT_SECONDS = 1.0
_SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.timer$")
_HOST_TIMER_STATE_FILENAME = "parser-control-systemd.json"
_HOST_TIMER_STATE_SCHEMA_VERSION = 1
_HOST_TIMER_STATE_MAX_BYTES = 128 * 1024
_HOST_TIMER_STATE_MAX_AGE = timedelta(minutes=3)
_HOST_TIMER_STATE_FUTURE_TOLERANCE = timedelta(seconds=30)
_SYSTEMD_SHOW_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "Result",
    "ExecMainCode",
    "ExecMainStatus",
)


Recurrence = Literal["daily", "weekly", "odd-month-days", "explicit"]
SchedulePurpose = Literal["primary", "recovery"]


@dataclass(frozen=True)
class _ScheduleSpec:
    id: str
    label: str
    systemd_unit: str
    on_calendar: tuple[str, ...]
    source_ids: frozenset[str]
    recurrence: Recurrence
    local_times: tuple[time, ...] = ()
    weekdays: tuple[int, ...] = ()
    explicit_local_datetimes: tuple[datetime, ...] = ()
    purpose: SchedulePurpose = "primary"
    required_publication_mode: str | None = None


def _times(*values: tuple[int, int]) -> tuple[time, ...]:
    return tuple(time(hour, minute) for hour, minute in values)


_SCRAPE_SOURCE_IDS = frozenset(
    source_id for source_id, source in SOURCE_BY_ID.items() if source.kind == "scrape"
)


_SCHEDULES: tuple[_ScheduleSpec, ...] = (
    _ScheduleSpec(
        id="refresh-all-daily",
        label="Ежедневно в 07:00",
        systemd_unit="hs-data-api-docker-refresh.timer",
        on_calendar=("*-*-* 07:00:00 Europe/Warsaw",),
        source_ids=_SCRAPE_SOURCE_IDS,
        recurrence="daily",
        local_times=_times((7, 0)),
    ),
    _ScheduleSpec(
        id="refresh-api-daily",
        label="Ежедневно в 18:00",
        systemd_unit="hs-data-api-docker-refresh-api.timer",
        on_calendar=("*-*-* 18:00:00 Europe/Warsaw",),
        source_ids=frozenset(LIGHT_API_IDS | MEDIUM_API_IDS),
        recurrence="daily",
        local_times=_times((18, 0)),
    ),
    _ScheduleSpec(
        id="refresh-fun-decks-standard",
        label="Каждые 2 часа в :45 — фановые Standard",
        systemd_unit="hs-data-api-docker-refresh-fun-decks-standard.timer",
        on_calendar=("*-*-* 00/2:45:00 Europe/Warsaw",),
        source_ids=frozenset({"hsguru_fun_decks"}),
        recurrence="daily",
        local_times=_times(*((hour, 45) for hour in range(0, 24, 2))),
    ),
    _ScheduleSpec(
        id="refresh-streamer-decks",
        label="Каждый час в :15",
        systemd_unit="hs-data-api-docker-firecrawl-streamer.timer",
        on_calendar=("*-*-* *:15:00 Europe/Warsaw",),
        source_ids=frozenset({"hsguru_streamer_decks_legend_1000"}),
        recurrence="daily",
        local_times=_times(*((hour, 15) for hour in range(24))),
    ),
    _ScheduleSpec(
        id="recover-hsguru-streamer",
        label="Каждые 10 минут — восстановление после неудачного обновления",
        systemd_unit="hs-data-api-docker-recover-hsguru-streamer.timer",
        on_calendar=("*-*-* *:00/10:00 Europe/Warsaw",),
        source_ids=frozenset({"hsguru_streamer_decks_legend_1000"}),
        recurrence="daily",
        local_times=_times(
            *((hour, minute) for hour in range(24) for minute in range(0, 60, 10))
        ),
        purpose="recovery",
    ),
    _ScheduleSpec(
        id="refresh-hsguru-meta-matrix",
        label="Каждые 12 часов",
        systemd_unit="hs-data-api-docker-refresh-hsguru-meta-matrix.timer",
        on_calendar=(
            "*-*-* 00:00:00 Europe/Warsaw",
            "*-*-* 12:00:00 Europe/Warsaw",
        ),
        source_ids=frozenset({"hsguru_meta_matrix"}),
        recurrence="daily",
        local_times=_times((0, 0), (12, 0)),
    ),
    _ScheduleSpec(
        id="refresh-hsguru-archetype-analysis",
        label="Ежедневно в 03:20",
        systemd_unit="hs-data-api-docker-refresh-hsguru-archetype-analysis.timer",
        on_calendar=("*-*-* 03:20:00 Europe/Warsaw",),
        source_ids=frozenset({"hsguru_archetype_analysis"}),
        recurrence="daily",
        local_times=_times((3, 20)),
    ),
    _ScheduleSpec(
        id="recover-hsguru-archetype-analysis",
        label="Каждый час в :45 — восстановление checkpoint",
        systemd_unit="hs-data-api-docker-recover-hsguru-archetype-analysis.timer",
        on_calendar=("*-*-* *:45:00 Europe/Warsaw",),
        source_ids=frozenset({"hsguru_archetype_analysis"}),
        recurrence="daily",
        local_times=_times(*((hour, 45) for hour in range(24))),
        purpose="recovery",
    ),
    _ScheduleSpec(
        id="capture-bg-compositions-screenshot",
        label="Каждые 6 часов в 04:10, 10:10, 16:10 и 22:10",
        systemd_unit="hs-data-api-docker-bg-compositions-screenshot.timer",
        on_calendar=(
            "*-*-* 04:10:00 Europe/Warsaw",
            "*-*-* 10:10:00 Europe/Warsaw",
            "*-*-* 16:10:00 Europe/Warsaw",
            "*-*-* 22:10:00 Europe/Warsaw",
        ),
        source_ids=frozenset(
            {"hsreplay_battlegrounds_compositions_screenshot"}
        ),
        recurrence="daily",
        local_times=_times((4, 10), (10, 10), (16, 10), (22, 10)),
    ),
    _ScheduleSpec(
        id="refresh-hsreplay-meta-firecrawl",
        label="Ежедневно в 03:05",
        systemd_unit="hs-data-api-docker-refresh-hsreplay-meta-firecrawl.timer",
        on_calendar=("*-*-* 03:05:00 Europe/Warsaw",),
        source_ids=frozenset(
            {
                "hsreplay_meta_top_1000_legend_1d_firecrawl",
                "hsreplay_meta_legend_1d_firecrawl",
                "hsreplay_meta_diamond_4to1_1d_firecrawl",
            }
        ),
        recurrence="daily",
        local_times=_times((3, 5)),
    ),
    _ScheduleSpec(
        id="refresh-hsreplay-card-periods",
        label="Каждые 4 часа в :35",
        systemd_unit="hs-data-api-docker-refresh-hsreplay-card-periods.timer",
        on_calendar=("*-*-* 00,04,08,12,16,20:35:00 UTC",),
        source_ids=frozenset(HSREPLAY_CARD_PERIOD_SOURCE_IDS),
        recurrence="daily",
        local_times=_times(
            (2, 35),
            (6, 35),
            (10, 35),
            (14, 35),
            (18, 35),
            (22, 35),
        ),
    ),
    _ScheduleSpec(
        id="refresh-hsreplay-arena-classes-firecrawl",
        label="По нечётным дням месяца в 03:35",
        systemd_unit="hs-data-api-docker-refresh-hsreplay-arena-classes-firecrawl.timer",
        on_calendar=("*-*-1/2 03:35:00 Europe/Warsaw",),
        source_ids=frozenset({"hsreplay_arena_class_pages_firecrawl"}),
        recurrence="odd-month-days",
        local_times=_times((3, 35)),
    ),
    _ScheduleSpec(
        id="refresh-vicious-syndicate",
        label="Каждые 2 часа в :20",
        systemd_unit="hs-data-api-docker-refresh-vicious-syndicate.timer",
        on_calendar=("*-*-* 00/2:20:00 Europe/Warsaw",),
        source_ids=frozenset(
            {"vicious_syndicate_live_beta", "vicious_syndicate_radars"}
        ),
        recurrence="daily",
        local_times=_times(*((hour, 20) for hour in range(0, 24, 2))),
    ),
    _ScheduleSpec(
        id="refresh-hsreplay-archetypes",
        label="По понедельникам и четвергам в 03:20",
        systemd_unit="hs-data-api-docker-refresh-hsreplay-archetypes.timer",
        on_calendar=("Mon,Thu *-*-* 03:20:00 Europe/Warsaw",),
        source_ids=frozenset({"hsreplay_archetypes"}),
        recurrence="weekly",
        local_times=_times((3, 20)),
        weekdays=(0, 3),
    ),
    _ScheduleSpec(
        id="refresh-bg-minions-db",
        label="По понедельникам и четвергам в 03:45",
        systemd_unit="hs-data-api-docker-refresh-bg-minions-db.timer",
        on_calendar=("Mon,Thu *-*-* 03:45:00 Europe/Warsaw",),
        source_ids=frozenset({"hsreplay_battlegrounds_minions"}),
        recurrence="weekly",
        local_times=_times((3, 45)),
        weekdays=(0, 3),
    ),
    _ScheduleSpec(
        id="refresh-bg-hero-details",
        label="По понедельникам и четвергам в 04:35",
        systemd_unit="hs-data-api-docker-refresh-bg-hero-details.timer",
        on_calendar=("Mon,Thu *-*-* 04:35:00 Europe/Warsaw",),
        source_ids=frozenset({"hsreplay_battlegrounds_hero_details"}),
        recurrence="weekly",
        local_times=_times((4, 35)),
        weekdays=(0, 3),
    ),
    _ScheduleSpec(
        id="refresh-post-patch-tierlists",
        label="До каждых 5 часов в активном post-patch режиме",
        systemd_unit="hs-data-api-docker-refresh-post-patch-tierlists.timer",
        on_calendar=("*-*-* 00,05,10,15,20:20:00 Europe/Warsaw",),
        source_ids=EARLY_SOURCE_IDS,
        recurrence="daily",
        local_times=_times((0, 20), (5, 20), (10, 20), (15, 20), (20, 20)),
        required_publication_mode="early",
    ),
)


def _safe_systemd_state(value: Any) -> str | None:
    state = str(value or "").strip()
    if not state or len(state) > 64:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", state):
        return None
    return state


def _systemctl_binary() -> str | None:
    return shutil.which("systemctl", path=_SYSTEMCTL_SEARCH_PATH)


def _run_systemctl(
    binary: str,
    arguments: list[str],
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    try:
        completed = subprocess.run(
            [binary, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_SECONDS,
            shell=False,
            close_fds=True,
            env={
                "PATH": _SYSTEMCTL_SEARCH_PATH,
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError:
        return None, "not-installed"
    except OSError:
        return None, "unavailable"
    return completed, None


def _parse_systemctl_show(
    output: str,
    *,
    allowed_units: frozenset[str],
) -> dict[str, dict[str, str | None]]:
    parsed: dict[str, dict[str, str | None]] = {}
    for block in re.split(r"\n\s*\n", output.strip()):
        properties: dict[str, str] = {}
        for line in block.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in _SYSTEMD_SHOW_PROPERTIES:
                properties[key] = value.strip()
        unit = properties.get("Id")
        if unit not in allowed_units:
            continue
        parsed[unit] = {
            "loadState": _safe_systemd_state(properties.get("LoadState")),
            "activeState": _safe_systemd_state(properties.get("ActiveState")),
            "subState": _safe_systemd_state(properties.get("SubState")),
            "unitFileState": _safe_systemd_state(properties.get("UnitFileState")),
            "result": _safe_systemd_state(properties.get("Result")),
            "execMainCode": _safe_systemd_state(properties.get("ExecMainCode")),
            "execMainStatus": _safe_non_negative_int(
                properties.get("ExecMainStatus")
            ),
        }
    return parsed


def _timestamp_from_systemd_microseconds(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        microseconds = int(value)
    except (TypeError, ValueError):
        return None
    if microseconds <= 0:
        return None
    try:
        seconds, remainder = divmod(microseconds, 1_000_000)
        return datetime.fromtimestamp(seconds, tz=UTC).replace(
            microsecond=remainder
        ).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _safe_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_systemctl_timer_times(
    output: str,
    *,
    allowed_units: frozenset[str],
) -> dict[str, dict[str, str | None]] | None:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, list):
        return None

    parsed: dict[str, dict[str, str | None]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        unit = item.get("unit")
        if unit not in allowed_units:
            continue
        parsed[str(unit)] = {
            "lastRunAt": _timestamp_from_systemd_microseconds(item.get("last")),
            "nextRunAt": _timestamp_from_systemd_microseconds(item.get("next")),
        }
    return parsed


def _enabled_from_unit_file_state(state: str | None) -> bool | None:
    if state in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}:
        return True
    if state in {"disabled", "masked", "masked-runtime"}:
        return False
    return None


def _active_from_active_state(state: str | None) -> bool | None:
    if state == "active":
        return True
    if state in {"inactive", "failed", "deactivating"}:
        return False
    return None


def _failure_from_systemd_state(properties: dict[str, str | None]) -> str | None:
    load_state = properties.get("loadState")
    active_state = properties.get("activeState")
    result = properties.get("result")
    if load_state == "not-found":
        return "unit-not-found"
    if result and result != "success":
        return result
    if active_state == "failed":
        return "failed"
    return None


def _is_handled_data_degradation(properties: dict[str, Any]) -> bool:
    """Return true only for a normal systemd exit accepted via SuccessExitStatus."""
    exec_code = str(properties.get("execMainCode") or "").strip().lower()
    return (
        properties.get("result") == "success"
        and exec_code in {"1", "exited"}
        and properties.get("execMainStatus") == 10
    )


def _probe_systemd_timer_states_direct(
    units: tuple[str, ...],
    *,
    checked_at: datetime,
) -> dict[str, Any]:
    known_units = frozenset(spec.systemd_unit for spec in _SCHEDULES)
    requested_units = tuple(
        sorted(
            {
                unit
                for unit in units
                if unit in known_units and _SYSTEMD_UNIT_RE.fullmatch(unit)
            }
        )
    )
    service_by_timer = {
        unit: f"{unit.removesuffix('.timer')}.service" for unit in requested_units
    }
    base = {
        "provider": "systemd",
        "checkedAt": _as_utc(checked_at).isoformat(),
        "available": False,
        "status": "unavailable",
        "reason": None,
        "timingAvailable": False,
        "units": {},
    }
    if not requested_units:
        base["reason"] = "no-units"
        return base

    binary = _systemctl_binary()
    if not binary:
        base["reason"] = "not-installed"
        return base

    show, show_error = _run_systemctl(
        binary,
        [
            "show",
            "--no-pager",
            f"--property={','.join(_SYSTEMD_SHOW_PROPERTIES)}",
            *sorted((*requested_units, *service_by_timer.values())),
        ],
    )
    if show is None:
        base["reason"] = show_error or "unavailable"
        return base

    allowed_units = frozenset((*requested_units, *service_by_timer.values()))
    properties_by_unit = _parse_systemctl_show(
        show.stdout,
        allowed_units=allowed_units,
    )
    if not properties_by_unit:
        base["reason"] = "not-systemd" if show.returncode else "invalid-response"
        return base

    timers, timers_error = _run_systemctl(
        binary,
        [
            "list-timers",
            "--all",
            "--no-pager",
            "--output=json",
            *requested_units,
        ],
    )
    timer_times = (
        _parse_systemctl_timer_times(timers.stdout, allowed_units=allowed_units)
        if timers is not None and timers.returncode == 0
        else None
    )
    if timers is not None and timers.returncode != 0:
        timers_error = "command-failed"

    runtime_units: dict[str, dict[str, Any]] = {}
    for unit in requested_units:
        properties = properties_by_unit.get(unit)
        if properties is None:
            runtime_units[unit] = {
                "available": False,
                "enabled": None,
                "active": None,
                "lastRunAt": None,
                "nextRunAt": None,
                "failure": "unit-unavailable",
                "loadState": None,
                "activeState": None,
                "subState": None,
                "unitFileState": None,
                "result": None,
                "serviceUnit": service_by_timer[unit],
                "serviceActiveState": None,
                "serviceResult": None,
                "serviceExecMainCode": None,
                "serviceExecMainStatus": None,
                "dataDegraded": None,
            }
            continue
        timing = timer_times.get(unit, {}) if timer_times is not None else {}
        service_properties = properties_by_unit.get(service_by_timer[unit]) or {}
        runtime_units[unit] = {
            "available": True,
            "enabled": _enabled_from_unit_file_state(properties.get("unitFileState")),
            "active": _active_from_active_state(properties.get("activeState")),
            "lastRunAt": timing.get("lastRunAt"),
            "nextRunAt": timing.get("nextRunAt"),
            "failure": (
                _failure_from_systemd_state(service_properties)
                or _failure_from_systemd_state(properties)
            ),
            "serviceUnit": service_by_timer[unit],
            "serviceActiveState": service_properties.get("activeState"),
            "serviceResult": service_properties.get("result"),
            "serviceExecMainCode": service_properties.get("execMainCode"),
            "serviceExecMainStatus": service_properties.get("execMainStatus"),
            "dataDegraded": _is_handled_data_degradation(service_properties),
            **properties,
        }

    complete = all(
        unit in properties_by_unit and service_by_timer[unit] in properties_by_unit
        for unit in requested_units
    )
    timing_available = timer_times is not None
    base.update(
        {
            "available": True,
            "status": "ok" if complete and timing_available else "partial",
            "reason": (
                None
                if complete and timing_available
                else timers_error or "partial-response"
            ),
            "timingAvailable": timing_available,
            "units": runtime_units,
        }
    )
    return base


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


def _sanitize_runtime_unit(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    def optional_bool(key: str) -> bool | None:
        candidate = value.get(key)
        return candidate if isinstance(candidate, bool) else None

    def optional_timestamp(key: str) -> str | None:
        candidate = _parse_iso_datetime(value.get(key))
        return candidate.isoformat() if candidate is not None else None

    properties = {
        "loadState": _safe_systemd_state(value.get("loadState")),
        "activeState": _safe_systemd_state(value.get("activeState")),
        "subState": _safe_systemd_state(value.get("subState")),
        "unitFileState": _safe_systemd_state(value.get("unitFileState")),
        "result": _safe_systemd_state(value.get("result")),
        "serviceActiveState": _safe_systemd_state(value.get("serviceActiveState")),
        "serviceResult": _safe_systemd_state(value.get("serviceResult")),
        "serviceExecMainCode": _safe_systemd_state(
            value.get("serviceExecMainCode")
        ),
        "serviceExecMainStatus": _safe_non_negative_int(
            value.get("serviceExecMainStatus")
        ),
    }
    return {
        "available": value.get("available") is True,
        "enabled": optional_bool("enabled"),
        "active": optional_bool("active"),
        "lastRunAt": optional_timestamp("lastRunAt"),
        "nextRunAt": optional_timestamp("nextRunAt"),
        "failure": _safe_systemd_state(value.get("failure")),
        "serviceUnit": _safe_systemd_state(value.get("serviceUnit")),
        "dataDegraded": optional_bool("dataDegraded"),
        **properties,
    }


def _host_timer_state_path() -> Path:
    return data_dir() / _HOST_TIMER_STATE_FILENAME


def _read_host_systemd_timer_snapshot(
    units: tuple[str, ...],
    *,
    checked_at: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    path = _host_timer_state_path()
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                return None, "host-snapshot-invalid"
            raw_bytes = handle.read(_HOST_TIMER_STATE_MAX_BYTES + 1)
        if len(raw_bytes) > _HOST_TIMER_STATE_MAX_BYTES:
            return None, "host-snapshot-invalid"
        raw = raw_bytes.decode("utf-8")
    except FileNotFoundError:
        return None, "host-snapshot-missing"
    except (OSError, UnicodeDecodeError):
        return None, "host-snapshot-unreadable"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, "host-snapshot-invalid"
    if not isinstance(payload, dict):
        return None, "host-snapshot-invalid"
    if payload.get("schemaVersion") != _HOST_TIMER_STATE_SCHEMA_VERSION:
        return None, "host-snapshot-schema"
    if payload.get("provider") != "host-systemd":
        return None, "host-snapshot-invalid"

    generated_at = _parse_iso_datetime(payload.get("generatedAt"))
    if generated_at is None:
        return None, "host-snapshot-invalid"
    age = _as_utc(checked_at) - generated_at
    if age > _HOST_TIMER_STATE_MAX_AGE:
        return None, "host-snapshot-stale"
    if age < -_HOST_TIMER_STATE_FUTURE_TOLERANCE:
        return None, "host-snapshot-future"

    requested_units = frozenset(units)
    raw_units = payload.get("units")
    if not isinstance(raw_units, dict):
        return None, "host-snapshot-invalid"
    runtime_units: dict[str, dict[str, Any]] = {}
    for unit, raw_unit in raw_units.items():
        if unit not in requested_units or not _SYSTEMD_UNIT_RE.fullmatch(str(unit)):
            continue
        sanitized = _sanitize_runtime_unit(raw_unit)
        if sanitized is not None:
            sanitized["serviceUnit"] = f"{str(unit).removesuffix('.timer')}.service"
            runtime_units[str(unit)] = sanitized

    available_units = sum(
        1 for runtime in runtime_units.values() if runtime.get("available") is True
    )
    complete = available_units == len(requested_units)
    timing_available = payload.get("timingAvailable") is True
    return (
        {
            "provider": "host-systemd",
            "checkedAt": generated_at.isoformat(),
            "snapshotAgeSeconds": max(0, round(age.total_seconds(), 3)),
            "available": available_units > 0,
            "status": "ok" if complete and timing_available else "partial",
            "reason": None if complete and timing_available else "partial-response",
            "timingAvailable": timing_available,
            "units": runtime_units,
        },
        None,
    )


def _probe_systemd_timer_states(
    units: tuple[str, ...],
    *,
    checked_at: datetime,
) -> dict[str, Any]:
    host_snapshot, host_error = _read_host_systemd_timer_snapshot(
        units,
        checked_at=checked_at,
    )
    if host_snapshot is not None and host_snapshot.get("available") is True:
        return host_snapshot

    direct = _probe_systemd_timer_states_direct(units, checked_at=checked_at)
    if direct.get("available") is True:
        return direct
    direct_reason = direct.get("reason")
    if host_snapshot is not None:
        direct["reason"] = host_snapshot.get("reason") or "host-snapshot-unavailable"
    elif host_error:
        direct["reason"] = host_error
    if direct_reason and direct_reason != direct.get("reason"):
        direct["directReason"] = direct_reason
    return direct


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _next_run(spec: _ScheduleSpec, *, at: datetime) -> datetime | None:
    moment = _as_utc(at)
    if spec.recurrence == "explicit":
        return next(
            (
                candidate.astimezone(UTC)
                for candidate in spec.explicit_local_datetimes
                if candidate.astimezone(UTC) > moment
            ),
            None,
        )

    timezone = ZoneInfo(SCHEDULE_TIMEZONE)
    local_day = moment.astimezone(timezone).date()
    scan_days = 15 if spec.recurrence == "weekly" else 40
    for offset in range(scan_days):
        candidate_day = local_day + timedelta(days=offset)
        if spec.recurrence == "weekly" and candidate_day.weekday() not in spec.weekdays:
            continue
        if spec.recurrence == "odd-month-days" and candidate_day.day % 2 == 0:
            continue
        for local_time in spec.local_times:
            naive_candidate = datetime.combine(candidate_day, local_time)
            candidate = naive_candidate.replace(tzinfo=timezone, fold=0)
            candidate_utc = candidate.astimezone(UTC)
            round_trip = candidate_utc.astimezone(timezone).replace(tzinfo=None)
            if round_trip != naive_candidate:
                continue
            if candidate_utc > moment:
                return candidate_utc
    return None


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _summary(schedule_rows: list[dict[str, Any]]) -> str:
    labels = [str(row["label"]) for row in schedule_rows]
    if not labels:
        return "Расписание не задано"
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} · ещё расписаний: {len(labels) - 1}"


def _combined_view(schedule_rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary_rows = [row for row in schedule_rows if row.get("purpose") == "primary"]
    recovery_rows = [
        row for row in schedule_rows if row.get("purpose") == "recovery"
    ]
    next_runs = [
        str(row["nextRunAt"])
        for row in primary_rows
        if row.get("nextRunAt") is not None
    ]
    recovery_next_runs = [
        str(row["nextRunAt"])
        for row in recovery_rows
        if row.get("nextRunAt") is not None
    ]
    return {
        "scheduleIds": [str(row["id"]) for row in schedule_rows],
        "schedule": _summary(primary_rows),
        "nextRunAt": min(next_runs) if next_runs else None,
        "recoveryScheduleIds": [str(row["id"]) for row in recovery_rows],
        "recoverySchedule": _summary(recovery_rows) if recovery_rows else None,
        "nextRecoveryRunAt": (
            min(recovery_next_runs) if recovery_next_runs else None
        ),
    }


def build_schedule_inventory(
    *,
    at: datetime | None = None,
    include_runtime: bool = True,
    publication_mode: str | None = None,
) -> dict[str, Any]:
    """Return versioned nominal schedules enriched with bounded runtime health.

    Runtime state is read from an atomically exported host snapshot first. A
    direct, allow-listed ``systemctl`` probe is a bounded fallback for installs
    where the API itself runs on the systemd host. Containers without either
    source keep the nominal contract and explicitly report runtime unavailable.
    """

    moment = _as_utc(at or datetime.now(UTC))
    systemd_units = tuple(spec.systemd_unit for spec in _SCHEDULES)
    runtime_probe = (
        _probe_systemd_timer_states(systemd_units, checked_at=moment)
        if include_runtime
        else {
            "provider": "disabled",
            "checkedAt": moment.isoformat(),
            "available": False,
            "status": "unavailable",
            "reason": "runtime-probe-disabled",
            "timingAvailable": False,
            "units": {},
        }
    )
    runtime_units = runtime_probe.get("units")
    if not isinstance(runtime_units, dict):
        runtime_units = {}
    runtime_included = runtime_probe.get("available") is True
    timing_available = runtime_probe.get("timingAvailable") is True
    runtime_complete = runtime_probe.get("status") == "ok"
    schedule_rows: list[dict[str, Any]] = []
    for spec in _SCHEDULES:
        condition_met = (
            spec.required_publication_mode is None
            or publication_mode is None
            or publication_mode == spec.required_publication_mode
        )
        source_ids = sorted(
            source_id
            for source_id in spec.source_ids
            if source_operationally_enabled(source_id)
        )
        nominal_next_run = _next_run(spec, at=moment) if condition_met else None
        runtime = runtime_units.get(spec.systemd_unit)
        if not isinstance(runtime, dict):
            runtime = {}
        runtime_available = runtime.get("available") is True
        use_runtime_timing = condition_met and runtime_available and timing_available
        runtime_next_run = (
            _parse_iso_datetime(runtime.get("nextRunAt"))
            if use_runtime_timing
            else None
        )
        effective_next_run = (
            runtime_next_run if use_runtime_timing else nominal_next_run
        )
        last_explicit = (
            spec.explicit_local_datetimes[-1]
            if spec.explicit_local_datetimes
            else None
        )
        schedule_rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "purpose": spec.purpose,
                "requiredPublicationMode": spec.required_publication_mode,
                "conditionMet": condition_met,
                "systemdUnit": spec.systemd_unit,
                "onCalendar": list(spec.on_calendar),
                "timezone": SCHEDULE_TIMEZONE,
                "sourceIds": source_ids,
                "sectionIds": sorted(
                    {SOURCE_TO_SECTION[source_id] for source_id in source_ids}
                ),
                "nominalNextRunAt": _iso(nominal_next_run),
                "nextRunAt": _iso(effective_next_run),
                "nextRunAtSource": "runtime" if use_runtime_timing else "nominal",
                "validUntil": _iso(last_explicit),
                "isActive": condition_met and nominal_next_run is not None,
                "runtimeStateAvailable": runtime_available,
                "enabled": runtime.get("enabled") if runtime_available else None,
                "active": runtime.get("active") if runtime_available else None,
                "lastRunAt": runtime.get("lastRunAt") if runtime_available else None,
                "failure": runtime.get("failure") if runtime_available else None,
                "loadState": runtime.get("loadState") if runtime_available else None,
                "activeState": runtime.get("activeState") if runtime_available else None,
                "subState": runtime.get("subState") if runtime_available else None,
                "unitFileState": (
                    runtime.get("unitFileState") if runtime_available else None
                ),
                "result": runtime.get("result") if runtime_available else None,
                "serviceUnit": (
                    runtime.get("serviceUnit") if runtime_available else None
                ),
                "serviceActiveState": (
                    runtime.get("serviceActiveState") if runtime_available else None
                ),
                "serviceResult": (
                    runtime.get("serviceResult") if runtime_available else None
                ),
                "serviceExecMainCode": (
                    runtime.get("serviceExecMainCode")
                    if runtime_available
                    else None
                ),
                "serviceExecMainStatus": (
                    runtime.get("serviceExecMainStatus")
                    if runtime_available
                    else None
                ),
                "dataDegraded": (
                    runtime.get("dataDegraded") if runtime_available else None
                ),
            }
        )

    source_rows: dict[str, dict[str, Any]] = {}
    for source_id in SOURCE_BY_ID:
        rows = [row for row in schedule_rows if source_id in row["sourceIds"]]
        source_rows[source_id] = _combined_view(rows)

    section_rows: dict[str, dict[str, Any]] = {}
    for section_id in sorted(set(SOURCE_TO_SECTION.values())):
        rows = [row for row in schedule_rows if section_id in row["sectionIds"]]
        section_rows[section_id] = _combined_view(rows)

    return {
        "schemaVersion": SCHEDULE_INVENTORY_SCHEMA_VERSION,
        "inventoryVersion": SCHEDULE_INVENTORY_VERSION,
        "generatedAt": moment.isoformat(),
        "timeSemantics": (
            "runtime"
            if runtime_included and timing_available and runtime_complete
            else "mixed" if runtime_included else "nominal"
        ),
        "runtimeTimerStateIncluded": runtime_included,
        "runtimeTimerState": {
            key: value
            for key, value in runtime_probe.items()
            if key != "units"
        },
        "schedules": schedule_rows,
        "sources": source_rows,
        "sections": section_rows,
    }
