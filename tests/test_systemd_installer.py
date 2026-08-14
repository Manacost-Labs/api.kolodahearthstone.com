from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docker_systemd_installer_covers_every_timer(tmp_path: Path) -> None:
    staged_systemd = tmp_path / "systemd"
    calls_file = tmp_path / "systemctl-calls"
    fake_systemctl = tmp_path / "systemctl"
    fake_systemctl.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$SYSTEMCTL_CALLS_FILE"\n',
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    env = {
        **os.environ,
        "INSTALL_DIR": "/custom/hs-api",
        "SYSTEMD_DIR": str(staged_systemd),
        "SYSTEMCTL_BIN": str(fake_systemctl),
        "SYSTEMCTL_CALLS_FILE": str(calls_file),
    }

    subprocess.run(
        ["bash", str(ROOT / "scripts/install-docker-systemd.sh")],
        check=True,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    expected_timers = sorted(
        path.name for path in (ROOT / "systemd").glob("hs-data-api-docker-*.timer")
    )
    assert "hs-data-api-docker-rebuild-hsreplay-index.timer" in expected_timers
    assert "hs-data-api-docker-refresh-vicious-syndicate.timer" in expected_timers
    installed_timers = sorted(
        path.name for path in staged_systemd.glob("hs-data-api-docker-*.timer")
    )
    calls = calls_file.read_text(encoding="utf-8").splitlines()
    enabled_timers = sorted(
        line.removeprefix("enable --now ")
        for line in calls
        if line.startswith("enable --now ")
    )
    assert installed_timers == expected_timers
    assert enabled_timers == expected_timers
    assert calls[0] == "daemon-reload"
    assert (staged_systemd / "hs-data-api-docker.service").is_file()
    rebuild_service = (
        staged_systemd / "hs-data-api-docker-rebuild-hsreplay-index.service"
    )
    assert rebuild_service.is_file()
    assert "rebuild-hsreplay-index" in rebuild_service.read_text(encoding="utf-8")
    assert "/custom/hs-api/docker-compose.yml" in (
        staged_systemd / "hs-data-api-docker-refresh-bg-hero-details.service"
    ).read_text(encoding="utf-8")
    exporter_service = staged_systemd / "hs-data-api-docker-export-timer-state.service"
    assert exporter_service.is_file()
    exporter_text = exporter_service.read_text(encoding="utf-8")
    assert "WorkingDirectory=/custom/hs-api" in exporter_text
    assert "ReadWritePaths=/custom/hs-api/data" in exporter_text
    assert "python3 -m app.systemd_timer_export" in exporter_text
    vicious_service = (
        staged_systemd / "hs-data-api-docker-refresh-vicious-syndicate.service"
    )
    assert vicious_service.is_file()
    assert "vicious_syndicate_live_beta" in vicious_service.read_text(encoding="utf-8")
    assert "vicious_syndicate_radars" in vicious_service.read_text(encoding="utf-8")

    freshness_service = staged_systemd / "hs-data-api-docker-freshness-check.service"
    freshness_text = freshness_service.read_text(encoding="utf-8")
    assert (
        "freshness-check --since-hours 48 --alert --exit-mode execution"
        in freshness_text
    )
    assert "SuccessExitStatus=10" in freshness_text

    scheduled_refresh_services = [
        path
        for path in staged_systemd.glob("hs-data-api-docker-*.service")
        if "python -m app.cli refresh " in path.read_text(encoding="utf-8")
        and "--scheduled" in path.read_text(encoding="utf-8")
    ]
    assert scheduled_refresh_services
    for service in scheduled_refresh_services:
        assert "SuccessExitStatus=10" in service.read_text(encoding="utf-8")

    streamer_service = staged_systemd / "hs-data-api-docker-firecrawl-streamer.service"
    assert "SuccessExitStatus=10" in streamer_service.read_text(encoding="utf-8")

    for service_name in (
        "hs-data-api-docker-refresh-hsguru-meta-matrix.service",
        "hs-data-api-docker-refresh-hsguru-archetype-analysis.service",
        "hs-data-api-docker-refresh-hsguru-deck-catalog.service",
    ):
        service_text = (staged_systemd / service_name).read_text(encoding="utf-8")
        assert "SuccessExitStatus=10" in service_text

    archetype_service = (
        staged_systemd / "hs-data-api-docker-refresh-hsguru-archetype-analysis.service"
    )
    assert (
        "refresh-hsguru-archetype-analysis --scheduled"
        in archetype_service.read_text(encoding="utf-8")
    )
    assert "TimeoutStartSec=2h" in archetype_service.read_text(encoding="utf-8")

    recovery_service = (
        staged_systemd / "hs-data-api-docker-recover-hsguru-archetype-analysis.service"
    )
    recovery_text = recovery_service.read_text(encoding="utf-8")
    assert "--scheduled --recover-checkpoint --concurrency 2" in recovery_text
    assert "SuccessExitStatus=10" in recovery_text
    assert "Restart=" not in recovery_text

    recovery_timer = (
        staged_systemd / "hs-data-api-docker-recover-hsguru-archetype-analysis.timer"
    )
    recovery_timer_text = recovery_timer.read_text(encoding="utf-8")
    assert "RandomizedDelaySec=5min" in recovery_timer_text
    assert "Persistent=false" in recovery_timer_text


def test_legacy_refresh_units_rely_on_route_aware_internal_preflight() -> None:
    for service_name in (
        "hs-data-api-refresh.service",
        "hs-data-api-refresh-protected.service",
        "hs-data-api-refresh-api.service",
    ):
        service_text = (ROOT / "systemd" / service_name).read_text(encoding="utf-8")
        assert "app.cli preflight --strict" not in service_text
        assert "ensure-flaresolverr.sh" not in service_text


def test_api_refresh_units_use_one_aggregating_command() -> None:
    for service_name in (
        "hs-data-api-refresh-api.service",
        "hs-data-api-docker-refresh-api.service",
    ):
        service_text = (ROOT / "systemd" / service_name).read_text(encoding="utf-8")
        exec_start_lines = [
            line for line in service_text.splitlines() if line.startswith("ExecStart=")
        ]

        assert len(exec_start_lines) == 1
        assert "python -m app.cli refresh-api-tiers" in exec_start_lines[0]
        assert "--tier light_api" not in service_text
        assert "--tier medium_api" not in service_text
        assert "SuccessExitStatus=10" in service_text


def test_primary_docker_refreshes_use_durable_schedule_occurrences() -> None:
    full_refresh = (
        ROOT / "systemd" / "hs-data-api-docker-refresh.service"
    ).read_text(encoding="utf-8")
    api_refresh = (
        ROOT / "systemd" / "hs-data-api-docker-refresh-api.service"
    ).read_text(encoding="utf-8")

    assert "--schedule-id refresh-all-daily" in full_refresh
    assert "--schedule-id refresh-api-daily" in api_refresh


def test_schedule_ledger_reconciler_materializes_48_hours_every_five_minutes() -> None:
    service = (
        ROOT / "systemd" / "hs-data-api-docker-reconcile-schedule-ledger.service"
    ).read_text(encoding="utf-8")
    timer = (
        ROOT / "systemd" / "hs-data-api-docker-reconcile-schedule-ledger.timer"
    ).read_text(encoding="utf-8")

    assert "reconcile-schedule-ledger --horizon-hours 48" in service
    assert "OnCalendar=*-*-* *:00/5:00 UTC" in timer
    assert "AccuracySec=30s" in timer
    assert "Persistent=true" in timer


def test_docker_bg_hero_details_accepts_degraded_exit_code() -> None:
    service_text = (
        ROOT / "systemd" / "hs-data-api-docker-refresh-bg-hero-details.service"
    ).read_text(encoding="utf-8")

    assert "refresh-bg-hero-details --scheduled" in service_text
    assert "SuccessExitStatus=10" in service_text


def test_docker_partial_safe_pipelines_accept_degraded_exit_code() -> None:
    for service_name, command in (
        (
            "hs-data-api-docker-refresh-hsreplay-archetypes.service",
            "refresh-hsreplay-archetypes --scheduled",
        ),
        (
            "hs-data-api-docker-refresh-fun-decks-standard.service",
            "refresh-fun-decks --scheduled --format standard",
        ),
    ):
        service_text = (ROOT / "systemd" / service_name).read_text(encoding="utf-8")

        assert command in service_text
        assert "SuccessExitStatus=10" in service_text


def test_scrape_do_map_and_daily_audit_schedules_are_canonical() -> None:
    systemd_dir = ROOT / "systemd"
    map_service = (
        systemd_dir / "hs-data-api-docker-firecrawl-hsreplay-map.service"
    ).read_text(encoding="utf-8")
    assert "python -m app.cli scrape-do-map-hsreplay" in map_service
    assert "TimeoutStartSec=35min" in map_service

    archetype_service = (
        systemd_dir / "hs-data-api-docker-refresh-hsreplay-archetypes.service"
    ).read_text(encoding="utf-8")
    assert "After=" in archetype_service
    assert "hs-data-api-docker-firecrawl-hsreplay-map.service" in archetype_service

    streamer_timer = (
        systemd_dir / "hs-data-api-docker-firecrawl-streamer.timer"
    ).read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* *:15:00 Europe/Warsaw" in streamer_timer

    patch_service = (
        systemd_dir / "hs-data-api-docker-refresh-patches.service"
    ).read_text(encoding="utf-8")
    assert "SuccessExitStatus=10" in patch_service
    assert "TimeoutStartSec=35min" in patch_service

    deck_catalog_timer = (
        systemd_dir / "hs-data-api-docker-refresh-hsguru-deck-catalog.timer"
    ).read_text(encoding="utf-8")
    assert deck_catalog_timer.count("OnCalendar=") == 1
    assert "OnCalendar=*-*-* 00:40:00 Europe/Warsaw" in deck_catalog_timer

    deck_catalog_service = (
        systemd_dir / "hs-data-api-docker-refresh-hsguru-deck-catalog.service"
    ).read_text(encoding="utf-8")
    assert (
        "hs-data-api-docker-refresh-hsguru-meta-matrix.service" in deck_catalog_service
    )
    assert "TimeoutStartSec=45min" in deck_catalog_service

    patch_timer = (systemd_dir / "hs-data-api-docker-refresh-patches.timer").read_text(
        encoding="utf-8"
    )
    audit_timer = (
        systemd_dir / "hs-data-api-docker-game-change-audit.timer"
    ).read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 10:20:00 Europe/Warsaw" in patch_timer
    assert "OnCalendar=*-*-* 11:15:00 Europe/Warsaw" in audit_timer
