from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def test_job_run_context_tracks_progress_heartbeat_and_deadline() -> None:
    from app.job_run import JobRunContext

    started_at = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)
    clock = MutableClock(started_at)
    run = JobRunContext.start(
        run_id="hsguru-test-run",
        timeout_seconds=60,
        total_slices=2,
        clock=clock,
    )

    assert run.try_start_slice() is True
    run.finish_slice(succeeded=True)
    clock.advance(60)
    assert run.try_start_slice() is False

    assert run.snapshot() == {
        "run_id": "hsguru-test-run",
        "started_at": "2026-08-10T20:00:00+00:00",
        "deadline_at": "2026-08-10T20:01:00+00:00",
        "heartbeat_at": "2026-08-10T20:01:00+00:00",
        "timed_out": True,
        "progress": {
            "phase": "starting",
            "total_slices": 2,
            "started_slices": 1,
            "completed_slices": 1,
            "succeeded_slices": 1,
            "failed_slices": 0,
            "skipped_slices": 1,
        },
    }


def test_job_run_context_persists_throttled_progress_and_terminal_snapshot() -> None:
    from app.job_run import JobRunContext

    class RecordingWriter:
        def __init__(self) -> None:
            self.snapshots: list[dict[str, Any]] = []

        def write(self, snapshot: dict[str, Any]) -> None:
            self.snapshots.append(deepcopy(snapshot))

    started_at = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)
    clock = MutableClock(started_at)
    writer = RecordingWriter()
    run = JobRunContext.start(
        run_id="durable-heartbeat-run",
        timeout_seconds=120,
        total_slices=3,
        clock=clock,
        snapshot_writer=writer,
        heartbeat_interval_seconds=30,
    )

    assert len(writer.snapshots) == 1
    assert writer.snapshots[0]["progress"]["phase"] == "starting"

    assert run.try_start_slice() is True
    clock.advance(5)
    run.finish_slice(succeeded=True)
    assert len(writer.snapshots) == 1

    clock.advance(25)
    assert run.try_start_slice() is True
    assert len(writer.snapshots) == 2
    assert writer.snapshots[1]["heartbeat_at"] == "2026-08-10T20:00:30+00:00"
    assert writer.snapshots[1]["progress"] == {
        "phase": "starting",
        "total_slices": 3,
        "started_slices": 2,
        "completed_slices": 1,
        "succeeded_slices": 1,
        "failed_slices": 0,
        "skipped_slices": 0,
    }

    run.finish_slice(succeeded=True)
    run.finalize(phase="complete")

    assert len(writer.snapshots) == 3
    assert writer.snapshots[-1]["progress"]["phase"] == "complete"
    assert writer.snapshots[-1]["progress"]["completed_slices"] == 2


def test_finishing_a_slice_at_or_after_deadline_marks_the_run_timed_out() -> None:
    from app.job_run import JobRunContext

    started_at = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)
    clock = MutableClock(started_at)
    run = JobRunContext.start(
        run_id="request-crossed-deadline",
        timeout_seconds=10,
        total_slices=1,
        clock=clock,
    )

    assert run.try_start_slice() is True
    clock.advance(10)
    run.finish_slice(succeeded=True)

    assert run.timed_out is True
    assert run.snapshot()["timed_out"] is True
    assert run.snapshot()["progress"]["completed_slices"] == 1


def test_atomic_job_run_writer_replaces_one_snapshot_file(tmp_path) -> None:
    from app.job_run import AtomicJobRunSnapshotWriter

    path = tmp_path / "job-runs" / "hsguru_meta_matrix.json"
    writer = AtomicJobRunSnapshotWriter(path)

    writer.write({"run_id": "first", "progress": {"completed_slices": 1}})
    writer.write({"run_id": "second", "progress": {"completed_slices": 2}})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "run_id": "second",
        "progress": {"completed_slices": 2},
    }
    assert list(path.parent.glob("*.tmp")) == []


def test_snapshot_writer_failure_is_logged_once_and_never_breaks_the_job(
    caplog,
) -> None:
    from app.job_run import JobRunContext

    class FailingWriter:
        def __init__(self) -> None:
            self.calls = 0

        def write(self, _snapshot: dict[str, Any]) -> None:
            self.calls += 1
            raise RuntimeError("secret-token-must-not-appear")

    started_at = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)
    clock = MutableClock(started_at)
    writer = FailingWriter()

    with caplog.at_level(logging.WARNING, logger="app.job_run"):
        run = JobRunContext.start(
            run_id="best-effort-heartbeat-run",
            timeout_seconds=120,
            total_slices=2,
            clock=clock,
            snapshot_writer=writer,
            heartbeat_interval_seconds=30,
        )
        assert run.try_start_slice() is True
        clock.advance(35)
        run.finish_slice(succeeded=True)
        run.finalize(phase="complete")

    assert writer.calls == 1
    matching = [
        record
        for record in caplog.records
        if record.name == "app.job_run"
        and "snapshot persistence disabled" in record.getMessage()
    ]
    assert len(matching) == 1
    assert "secret-token-must-not-appear" not in caplog.text
