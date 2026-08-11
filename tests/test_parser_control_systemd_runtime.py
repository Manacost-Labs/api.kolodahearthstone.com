from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from app.parser_control import ParserControlStore
from app.parser_control_schedule import (
    _HOST_TIMER_STATE_SCHEMA_VERSION,
    _SCHEDULES,
    _failure_from_systemd_state,
    _is_handled_data_degradation,
    _probe_systemd_timer_states,
    _probe_systemd_timer_states_direct,
    build_schedule_inventory,
)
from app.systemd_timer_export import (
    build_host_timer_snapshot,
    write_host_timer_snapshot,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
UNIT = "hs-data-api-docker-refresh.timer"


def _runtime_unit(
    *,
    next_run_at: str = "2026-07-21T13:00:00+00:00",
) -> dict[str, object]:
    return {
        "available": True,
        "enabled": True,
        "active": True,
        "lastRunAt": "2026-07-21T11:00:00+00:00",
        "nextRunAt": next_run_at,
        "failure": None,
        "loadState": "loaded",
        "activeState": "active",
        "subState": "waiting",
        "unitFileState": "enabled",
        "result": "success",
        "serviceUnit": "hs-data-api-docker-refresh.service",
        "serviceActiveState": "inactive",
        "serviceResult": "success",
        "serviceExecMainCode": "exited",
        "serviceExecMainStatus": 0,
        "dataDegraded": False,
    }


def _unavailable(reason: str = "not-systemd") -> dict[str, object]:
    return {
        "provider": "systemd",
        "checkedAt": NOW.isoformat(),
        "available": False,
        "status": "unavailable",
        "reason": reason,
        "timingAvailable": False,
        "units": {},
    }


def test_direct_probe_is_allowlisted_bounded_and_does_not_use_a_shell() -> None:
    show = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "Result=success\n"
            f"Id={UNIT}\n"
            "LoadState=loaded\n"
            "ActiveState=active\n"
            "SubState=waiting\n"
            "UnitFileState=enabled\n\n"
            "Result=success\n"
            "ExecMainCode=1\n"
            "ExecMainStatus=10\n"
            "Id=hs-data-api-docker-refresh.service\n"
            "LoadState=loaded\n"
            "ActiveState=inactive\n"
            "SubState=dead\n"
            "UnitFileState=static\n"
        ),
        stderr="",
    )
    timers = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            [
                {
                    "unit": UNIT,
                    "last": 1_774_354_400_000_000,
                    "next": 1_774_361_600_000_000,
                }
            ]
        ),
        stderr="",
    )
    malicious = "; touch /tmp/parser-control-injected.timer"

    with patch(
        "app.parser_control_schedule._systemctl_binary",
        return_value="/usr/bin/systemctl",
    ), patch(
        "app.parser_control_schedule.subprocess.run",
        side_effect=[show, timers],
    ) as run:
        result = _probe_systemd_timer_states_direct(
            (UNIT, malicious),
            checked_at=NOW,
        )

    assert result["available"] is True
    assert result["status"] == "ok"
    assert result["units"][UNIT]["enabled"] is True
    assert result["units"][UNIT]["active"] is True
    assert result["units"][UNIT]["failure"] is None
    assert result["units"][UNIT]["serviceResult"] == "success"
    assert result["units"][UNIT]["serviceExecMainStatus"] == 10
    assert result["units"][UNIT]["dataDegraded"] is True
    assert len(run.call_args_list) == 2
    for call in run.call_args_list:
        command = call.args[0]
        assert command[0] == "/usr/bin/systemctl"
        assert malicious not in command
        assert call.kwargs["shell"] is False
        assert call.kwargs["timeout"] == 1.0


def test_direct_probe_timeout_is_a_graceful_unavailable_state() -> None:
    with patch(
        "app.parser_control_schedule._systemctl_binary",
        return_value="/usr/bin/systemctl",
    ), patch(
        "app.parser_control_schedule.subprocess.run",
        side_effect=subprocess.TimeoutExpired("systemctl", 1),
    ):
        result = _probe_systemd_timer_states_direct((UNIT,), checked_at=NOW)

    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert result["reason"] == "timeout"


def test_service_failure_result_is_exposed_as_the_timer_failure() -> None:
    assert _failure_from_systemd_state(
        {
            "loadState": "loaded",
            "activeState": "failed",
            "result": "exit-code",
        }
    ) == "exit-code"


def test_signal_status_ten_is_not_reported_as_handled_data_degradation() -> None:
    assert _is_handled_data_degradation(
        {"result": "signal", "execMainCode": "2", "execMainStatus": 10}
    ) is False
    assert _is_handled_data_degradation(
        {"result": "success", "execMainCode": "1", "execMainStatus": 10}
    ) is True


def test_inventory_prefers_runtime_state_and_retains_nominal_next_run() -> None:
    runtime = {
        "provider": "host-systemd",
        "checkedAt": NOW.isoformat(),
        "available": True,
        "status": "partial",
        "reason": "partial-response",
        "timingAvailable": True,
        "units": {UNIT: _runtime_unit()},
    }
    with patch(
        "app.parser_control_schedule._probe_systemd_timer_states",
        return_value=runtime,
    ):
        inventory = build_schedule_inventory(at=NOW)

    schedule = next(row for row in inventory["schedules"] if row["systemdUnit"] == UNIT)
    assert inventory["runtimeTimerStateIncluded"] is True
    assert inventory["timeSemantics"] == "mixed"
    assert inventory["runtimeTimerState"]["provider"] == "host-systemd"
    assert schedule["nominalNextRunAt"] == "2026-07-22T05:00:00+00:00"
    assert schedule["nextRunAt"] == "2026-07-21T13:00:00+00:00"
    assert schedule["nextRunAtSource"] == "runtime"
    assert schedule["enabled"] is True
    assert schedule["active"] is True
    assert schedule["lastRunAt"] == "2026-07-21T11:00:00+00:00"
    assert schedule["failure"] is None
    assert schedule["dataDegraded"] is False


def test_inventory_exposes_handled_data_degradation() -> None:
    degraded_unit = {
        **_runtime_unit(),
        "serviceExecMainStatus": 10,
        "dataDegraded": True,
    }
    runtime = {
        "provider": "host-systemd",
        "checkedAt": NOW.isoformat(),
        "available": True,
        "status": "ok",
        "reason": None,
        "timingAvailable": True,
        "units": {UNIT: degraded_unit},
    }
    with patch(
        "app.parser_control_schedule._probe_systemd_timer_states",
        return_value=runtime,
    ):
        inventory = build_schedule_inventory(at=NOW)

    schedule = next(row for row in inventory["schedules"] if row["systemdUnit"] == UNIT)
    assert schedule["serviceResult"] == "success"
    assert schedule["serviceExecMainStatus"] == 10
    assert schedule["dataDegraded"] is True


def test_fresh_host_snapshot_is_used_without_container_systemctl() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        payload = {
            "schemaVersion": _HOST_TIMER_STATE_SCHEMA_VERSION,
            "provider": "host-systemd",
            "generatedAt": NOW.isoformat(),
            "status": "partial",
            "timingAvailable": True,
            "units": {UNIT: _runtime_unit()},
        }
        (root / "parser-control-systemd.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        with patch("app.parser_control_schedule.data_dir", return_value=root), patch(
            "app.parser_control_schedule._probe_systemd_timer_states_direct"
        ) as direct:
            result = _probe_systemd_timer_states((UNIT,), checked_at=NOW)

    assert result["provider"] == "host-systemd"
    assert result["available"] is True
    assert result["status"] == "ok"
    assert result["units"][UNIT]["nextRunAt"] == "2026-07-21T13:00:00+00:00"
    direct.assert_not_called()


def test_parser_control_snapshot_exposes_fresh_host_runtime_health() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        units = {
            spec.systemd_unit: {
                **_runtime_unit(),
                "serviceUnit": f"{spec.systemd_unit.removesuffix('.timer')}.service",
            }
            for spec in _SCHEDULES
        }
        (root / "parser-control-systemd.json").write_text(
            json.dumps(
                {
                    "schemaVersion": _HOST_TIMER_STATE_SCHEMA_VERSION,
                    "provider": "host-systemd",
                    "generatedAt": NOW.isoformat(),
                    "status": "ok",
                    "timingAvailable": True,
                    "units": units,
                }
            ),
            encoding="utf-8",
        )
        with patch("app.parser_control_schedule.data_dir", return_value=root):
            snapshot = ParserControlStore(root).snapshot(at=NOW)

    inventory = snapshot["scheduleInventory"]
    schedule = next(row for row in inventory["schedules"] if row["systemdUnit"] == UNIT)
    assert inventory["runtimeTimerStateIncluded"] is True
    assert inventory["timeSemantics"] == "runtime"
    assert inventory["runtimeTimerState"]["provider"] == "host-systemd"
    assert schedule["runtimeStateAvailable"] is True
    assert schedule["enabled"] is True
    assert schedule["active"] is True
    assert schedule["nextRunAtSource"] == "runtime"


def test_host_snapshot_without_timer_timestamps_keeps_nominal_next_run() -> None:
    runtime = {
        "provider": "host-systemd",
        "checkedAt": NOW.isoformat(),
        "available": True,
        "status": "partial",
        "reason": "partial-response",
        "timingAvailable": False,
        "units": {UNIT: _runtime_unit(next_run_at="2026-07-21T13:00:00+00:00")},
    }
    with patch(
        "app.parser_control_schedule._probe_systemd_timer_states",
        return_value=runtime,
    ):
        inventory = build_schedule_inventory(at=NOW)

    schedule = next(row for row in inventory["schedules"] if row["systemdUnit"] == UNIT)
    assert inventory["runtimeTimerStateIncluded"] is True
    assert inventory["timeSemantics"] == "mixed"
    assert schedule["nextRunAt"] == schedule["nominalNextRunAt"]
    assert schedule["nextRunAtSource"] == "nominal"


def test_stale_host_snapshot_and_missing_bus_never_claim_runtime_state() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        payload = {
            "schemaVersion": _HOST_TIMER_STATE_SCHEMA_VERSION,
            "provider": "host-systemd",
            "generatedAt": (NOW - timedelta(minutes=4)).isoformat(),
            "status": "ok",
            "timingAvailable": True,
            "units": {UNIT: _runtime_unit()},
        }
        (root / "parser-control-systemd.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        with patch("app.parser_control_schedule.data_dir", return_value=root), patch(
            "app.parser_control_schedule._probe_systemd_timer_states_direct",
            return_value=_unavailable(),
        ):
            result = _probe_systemd_timer_states((UNIT,), checked_at=NOW)

    assert result["available"] is False
    assert result["reason"] == "host-snapshot-stale"
    assert result["directReason"] == "not-systemd"


def test_host_snapshot_symlink_is_not_followed() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        outside = root / "outside.json"
        outside.write_text(
            json.dumps(
                {
                    "schemaVersion": _HOST_TIMER_STATE_SCHEMA_VERSION,
                    "provider": "host-systemd",
                    "generatedAt": NOW.isoformat(),
                    "timingAvailable": True,
                    "units": {UNIT: _runtime_unit()},
                }
            ),
            encoding="utf-8",
        )
        (root / "parser-control-systemd.json").symlink_to(outside)
        with patch("app.parser_control_schedule.data_dir", return_value=root), patch(
            "app.parser_control_schedule._probe_systemd_timer_states_direct",
            return_value=_unavailable(),
        ):
            result = _probe_systemd_timer_states((UNIT,), checked_at=NOW)

    assert result["available"] is False
    assert result["reason"] == "host-snapshot-unreadable"


def test_exporter_writes_an_atomic_host_snapshot() -> None:
    runtime = {
        "provider": "systemd",
        "checkedAt": NOW.isoformat(),
        "available": True,
        "status": "partial",
        "reason": "partial-response",
        "timingAvailable": True,
        "units": {UNIT: _runtime_unit()},
    }
    with patch(
        "app.systemd_timer_export._probe_systemd_timer_states_direct",
        return_value=runtime,
    ):
        payload = build_host_timer_snapshot(at=NOW)

    with TemporaryDirectory() as directory:
        output = Path(directory) / "parser-control-systemd.json"
        write_host_timer_snapshot(output, payload)
        saved = json.loads(output.read_text(encoding="utf-8"))
        leftovers = list(output.parent.glob(".*.tmp"))

    assert saved["schemaVersion"] == _HOST_TIMER_STATE_SCHEMA_VERSION
    assert saved["provider"] == "host-systemd"
    assert saved["timingAvailable"] is True
    assert saved["units"][UNIT]["active"] is True
    assert leftovers == []


def test_exporter_does_not_replace_last_good_state_when_systemd_is_unavailable() -> None:
    with patch(
        "app.systemd_timer_export._probe_systemd_timer_states_direct",
        return_value=_unavailable("timeout"),
    ), pytest.raises(RuntimeError, match="timeout"):
        build_host_timer_snapshot(at=NOW)


def test_host_export_systemd_unit_is_hardened_and_runs_every_minute() -> None:
    root = Path(__file__).resolve().parents[1] / "systemd"
    service = (root / "hs-data-api-docker-export-timer-state.service").read_text(
        encoding="utf-8"
    )
    timer = (root / "hs-data-api-docker-export-timer-state.timer").read_text(
        encoding="utf-8"
    )

    assert "ExecStart=/usr/bin/python3 -m app.systemd_timer_export" in service
    assert "User=debian" in service
    assert "Group=debian" in service
    assert "NoNewPrivileges=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "InaccessiblePaths=-/run/docker.sock" in service
    assert "ProtectSystem=strict" in service
    assert "ReadWritePaths=/srv/hs-data-api/data" in service
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
