from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "hsguru_streamer_decks_legend_1000"


def test_streamer_recovery_is_conditioned_and_reuses_shared_refresh() -> None:
    service = (
        ROOT / "systemd" / "hs-data-api-docker-recover-hsguru-streamer.service"
    ).read_text(encoding="utf-8")

    assert "app.recovery_condition" in service
    assert f"--source {SOURCE_ID} --min-age-seconds 300" in service
    assert f"app.cli scheduled-check --source {SOURCE_ID}" in service
    assert "python /app/scripts/firecrawl-streamer-decks.py" in service
    assert "--concurrency" not in service
    assert "HS_FETCH_BACKENDS" not in service
    assert "Restart=" not in service


def test_streamer_recovery_runs_every_ten_minutes_without_catch_up() -> None:
    timer = (
        ROOT / "systemd" / "hs-data-api-docker-recover-hsguru-streamer.timer"
    ).read_text(encoding="utf-8")

    assert "OnCalendar=*-*-* *:00/10:00 Europe/Warsaw" in timer
    assert "Persistent=false" in timer
    assert "RandomizedDelaySec=30s" in timer
