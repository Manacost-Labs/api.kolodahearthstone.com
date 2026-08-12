from __future__ import annotations

import os
import re
import time
from pathlib import Path
from unittest.mock import patch


def _write_archive(path: Path, *, mtime: float) -> None:
    path.write_bytes(b"archive")
    os.utime(path, (mtime, mtime))


def test_rotation_prunes_expired_and_surplus_archives(tmp_path: Path) -> None:
    from app import refresh_log

    current = tmp_path / "refresh-events.jsonl"
    current.write_bytes(b"current log")
    now = time.time()
    for index in range(7):
        _write_archive(
            tmp_path / f"refresh-events.202608{index + 1:02d}T000000.jsonl.gz",
            mtime=now - index * 86400,
        )

    with (
        patch.object(refresh_log, "events_path", return_value=current),
        patch.object(refresh_log, "log_rotate_max_bytes", return_value=1),
        patch.object(refresh_log, "log_rotate_max_age_days", return_value=7),
        patch.object(refresh_log, "log_retention_days", return_value=14),
        patch.object(refresh_log, "log_retention_archives", return_value=5),
    ):
        refresh_log.maybe_rotate_events_log()

    archives = sorted(tmp_path.glob("refresh-events.*.jsonl.gz"))
    assert not current.exists()
    assert len(archives) == 5


def test_pruning_removes_archive_older_than_retention_even_below_count(
    tmp_path: Path,
) -> None:
    from app import refresh_log

    current = tmp_path / "refresh-events.jsonl"
    now = time.time()
    recent = tmp_path / "refresh-events.20260812T000000.jsonl.gz"
    expired = tmp_path / "refresh-events.20260701T000000.jsonl.gz"
    _write_archive(recent, mtime=now - 86400)
    _write_archive(expired, mtime=now - 30 * 86400)

    with (
        patch.object(refresh_log, "events_path", return_value=current),
        patch.object(refresh_log, "log_retention_days", return_value=14),
        patch.object(refresh_log, "log_retention_archives", return_value=5),
    ):
        removed = refresh_log._prune_rotated_events_logs(now=now)

    assert removed == 1
    assert recent.exists()
    assert not expired.exists()


def test_continuously_active_log_rotates_by_persisted_start_time(
    tmp_path: Path,
) -> None:
    from app import refresh_log

    current = tmp_path / "refresh-events.jsonl"
    current.write_bytes(b"first event\n")
    started_at = time.time()

    with (
        patch.object(refresh_log, "events_path", return_value=current),
        patch.object(refresh_log, "log_rotate_max_bytes", return_value=1_000_000),
        patch.object(refresh_log, "log_rotate_max_age_days", return_value=7),
        patch.object(refresh_log, "log_retention_days", return_value=14),
        patch.object(refresh_log, "log_retention_archives", return_value=5),
    ):
        refresh_log.maybe_rotate_events_log(now=started_at)

        eight_days_later = started_at + 8 * 86400
        with current.open("ab") as stream:
            stream.write(b"still active\n")
        os.utime(current, (eight_days_later, eight_days_later))
        refresh_log.maybe_rotate_events_log(now=eight_days_later)

    assert not current.exists()
    assert len(list(tmp_path.glob("refresh-events.*.jsonl.gz"))) == 1


def test_maintenance_prunes_archives_without_rotating_active_log(
    tmp_path: Path,
) -> None:
    from app import refresh_log

    current = tmp_path / "refresh-events.jsonl"
    current.write_bytes(b"small active log\n")
    now = time.time()
    expired = tmp_path / "refresh-events.20260701T000000.jsonl.gz"
    _write_archive(expired, mtime=now - 30 * 86400)

    with (
        patch.object(refresh_log, "events_path", return_value=current),
        patch.object(refresh_log, "log_rotate_max_bytes", return_value=1_000_000),
        patch.object(refresh_log, "log_rotate_max_age_days", return_value=7),
        patch.object(refresh_log, "log_retention_days", return_value=14),
        patch.object(refresh_log, "log_retention_archives", return_value=5),
    ):
        refresh_log.maybe_rotate_events_log(now=now)

    assert current.exists()
    assert not expired.exists()


def test_rotation_state_write_failure_is_fail_open(tmp_path: Path) -> None:
    from app import refresh_log

    current = tmp_path / "refresh-events.jsonl"
    current.write_bytes(b"small active log\n")

    with (
        patch.object(refresh_log, "events_path", return_value=current),
        patch.object(refresh_log, "log_rotate_max_bytes", return_value=1_000_000),
        patch.object(refresh_log, "log_rotate_max_age_days", return_value=7),
        patch.object(refresh_log, "log_retention_days", return_value=14),
        patch.object(refresh_log, "log_retention_archives", return_value=5),
        patch.object(
            refresh_log,
            "_write_rotation_started_at",
            side_effect=OSError("blocked"),
        ),
    ):
        refresh_log.maybe_rotate_events_log(now=time.time())

    assert current.exists()


def test_structured_log_io_failure_is_fail_open(tmp_path: Path, caplog) -> None:
    from app import refresh_log

    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    blocked_path = blocked_parent / "refresh-events.jsonl"

    with patch.object(refresh_log, "events_path", return_value=blocked_path):
        row = refresh_log.log_action("source.complete", source_id="safe-source")

    assert row["source_id"] == "safe-source"
    assert "Structured refresh log write failed: FileExistsError" in caplog.text
    assert str(blocked_path) not in caplog.text


def test_run_ids_are_process_safe_uuid_based() -> None:
    from app.refresh_log import new_run_id

    run_ids = {new_run_id() for _ in range(100)}

    assert len(run_ids) == 100
    assert all(re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{12}", value) for value in run_ids)


def test_structured_log_uses_process_lock_for_rotation_and_append(
    tmp_path: Path,
) -> None:
    from app import refresh_log

    events = tmp_path / "refresh-events.jsonl"
    entered: list[str] = []

    class RecordingLock:
        def __enter__(self):
            entered.append("enter")

        def __exit__(self, *_args):
            entered.append("exit")

    with (
        patch.object(refresh_log, "events_path", return_value=events),
        patch.object(
            refresh_log,
            "_event_log_process_lock",
            return_value=RecordingLock(),
        ),
    ):
        refresh_log.log_action("source.complete", source_id="locked-source")

    assert entered == ["enter", "exit"]
    assert events.read_text(encoding="utf-8").count("\n") == 1
