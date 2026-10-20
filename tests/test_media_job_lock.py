"""A shared lock must cover every media pass and preserve worker privileges."""

import fcntl
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
JOB = ROOT / "panel/scripts/archive_card_history_media_job.sh"
UNIT = ROOT / "platform/systemd/kolodahs-card-history-media.service"


def test_worker_waits_for_mirror_and_holds_one_lock_across_all_passes(tmp_path):
    log = tmp_path / "calls"
    started = tmp_path / "started"
    finish = tmp_path / "finish"
    python = tmp_path / "python"
    python.write_text(
        '#!/bin/bash\nset -eu\nprintf "%s\\n" "$*" >> "$TEST_MEDIA_LOG"\n'
        'if [[ "$*" == *"--newest"* ]]; then\n'
        ' touch "$TEST_MEDIA_STARTED"\n'
        ' while [[ ! -e "$TEST_MEDIA_FINISH" ]]; do sleep 0.02; done\n'
        "fi\n"
    )
    python.chmod(0o755)
    environment = dict(os.environ)
    environment.update(
        CARD_MEDIA_PYTHON=str(python),
        CARD_MEDIA_PANEL_ROOT=str(tmp_path),
        CARD_MEDIA_STATE=str(tmp_path / "state"),
        TEST_MEDIA_LOG=str(log),
        TEST_MEDIA_STARTED=str(started),
        TEST_MEDIA_FINISH=str(finish),
    )
    with (tmp_path / "shared.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        worker = subprocess.Popen(
            ["flock", "-w", "5", str(tmp_path / "shared.lock"), "bash", str(JOB)],
            env=environment,
        )
        try:
            assert not log.exists()
            assert worker.poll() is None
            fcntl.flock(lock, fcntl.LOCK_UN)
            import time

            until = time.monotonic() + 5
            while not started.exists() and time.monotonic() < until:
                time.sleep(0.02)
            assert started.exists()
            blocked = subprocess.run(
                ["flock", "-n", str(tmp_path / "shared.lock"), "true"], check=False
            )
            assert blocked.returncode == 1
            finish.touch()
            assert worker.wait(timeout=5) == 0
        finally:
            finish.touch()
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    calls = log.read_text().splitlines()
    assert len(calls) == 3
    assert "--newest --limit 20" in calls[0]
    assert "--limit 50 --state" in calls[1]
    assert "--retry --limit 10" in calls[2]


def test_supervisor_acquires_private_lock_before_dropping_privileges():
    text = UNIT.read_text()
    assert "User=root\n" in text
    assert text.count("ExecStart=") == 1
    assert (
        "/run/hs-data-platform-sync/sync.lock /usr/sbin/runuser -u koloda -- /bin/bash"
    ) in text
    assert "/run/hs-data-platform-sync" in next(
        line for line in text.splitlines() if line.startswith("ReadWritePaths=")
    )
