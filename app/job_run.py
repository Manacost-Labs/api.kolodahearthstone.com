from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


class JobRunSnapshotWriter(Protocol):
    def write(self, snapshot: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class AtomicJobRunSnapshotWriter:
    path: Path

    @classmethod
    def for_job(cls, job_name: str) -> AtomicJobRunSnapshotWriter:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        if not job_name or any(character not in allowed for character in job_name):
            raise ValueError("Invalid job-run snapshot name")
        from .storage import root_dir

        return cls(root_dir() / "job-runs" / f"{job_name}.json")

    def write(self, snapshot: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(snapshot, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o644)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clock_value(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("JobRunContext clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


@dataclass(slots=True)
class JobRunContext:
    """In-memory lifecycle and progress state for one bounded parser run."""

    run_id: str
    started_at: datetime
    deadline_at: datetime
    heartbeat_at: datetime
    total_slices: int
    phase: str = "starting"
    started_slices: int = 0
    completed_slices: int = 0
    succeeded_slices: int = 0
    failed_slices: int = 0
    skipped_slices: int = 0
    timed_out: bool = False
    _clock: Clock = field(default=_utc_now, repr=False, compare=False)
    _snapshot_writer: JobRunSnapshotWriter | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _heartbeat_interval_seconds: float = field(
        default=30,
        repr=False,
        compare=False,
    )
    _last_snapshot_at: datetime | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _snapshot_writer_failed: bool = field(
        default=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def start(
        cls,
        *,
        timeout_seconds: float,
        total_slices: int,
        run_id: str | None = None,
        clock: Clock = _utc_now,
        snapshot_writer: JobRunSnapshotWriter | None = None,
        heartbeat_interval_seconds: float = 30,
    ) -> JobRunContext:
        if timeout_seconds <= 0:
            raise ValueError("JobRunContext timeout_seconds must be positive")
        if total_slices < 0:
            raise ValueError("JobRunContext total_slices must be non-negative")
        if not 30 <= heartbeat_interval_seconds <= 60:
            raise ValueError(
                "JobRunContext heartbeat interval must be between 30 and 60 seconds"
            )
        normalized_run_id = str(run_id or uuid4().hex).strip()
        if not normalized_run_id:
            raise ValueError("JobRunContext run_id must not be empty")
        started_at = _clock_value(clock)
        context = cls(
            run_id=normalized_run_id,
            started_at=started_at,
            deadline_at=started_at + timedelta(seconds=float(timeout_seconds)),
            heartbeat_at=started_at,
            total_slices=total_slices,
            _clock=clock,
            _snapshot_writer=snapshot_writer,
            _heartbeat_interval_seconds=float(heartbeat_interval_seconds),
        )
        context._persist_snapshot(force=True)
        return context

    def set_total_slices(self, total_slices: int) -> None:
        accounted = self.started_slices + self.skipped_slices
        if total_slices < accounted:
            raise ValueError("JobRunContext total cannot be below accounted slices")
        self.total_slices = total_slices
        self.heartbeat()

    def heartbeat(self, *, phase: str | None = None) -> None:
        if phase is not None:
            self.phase = phase
        self.heartbeat_at = _clock_value(self._clock)
        self._persist_snapshot()

    def deadline_reached(self) -> bool:
        return _clock_value(self._clock) >= self.deadline_at

    def remaining_seconds(self) -> float:
        remaining = self.deadline_at - _clock_value(self._clock)
        return max(0.0, remaining.total_seconds())

    def try_start_slice(self) -> bool:
        self.heartbeat_at = _clock_value(self._clock)
        if self.heartbeat_at >= self.deadline_at:
            self.timed_out = True
            self.skipped_slices += 1
            self._persist_snapshot()
            return False
        self.started_slices += 1
        self._persist_snapshot()
        return True

    def mark_timed_out(self) -> None:
        self.timed_out = True
        self.heartbeat()

    def finish_slice(self, *, succeeded: bool) -> None:
        if self.completed_slices >= self.started_slices:
            raise RuntimeError("Cannot finish a slice that was not started")
        self.completed_slices += 1
        if succeeded:
            self.succeeded_slices += 1
        else:
            self.failed_slices += 1
        self.heartbeat_at = _clock_value(self._clock)
        if self.heartbeat_at >= self.deadline_at:
            self.timed_out = True
        self._persist_snapshot()

    def finalize(self, *, phase: str) -> None:
        self.phase = phase
        self.heartbeat_at = _clock_value(self._clock)
        self._persist_snapshot(force=True)

    def _persist_snapshot(self, *, force: bool = False) -> None:
        if self._snapshot_writer is None or self._snapshot_writer_failed:
            return
        due = (
            self._last_snapshot_at is None
            or (self.heartbeat_at - self._last_snapshot_at).total_seconds()
            >= self._heartbeat_interval_seconds
        )
        if not force and not due:
            return
        try:
            self._snapshot_writer.write(self.snapshot())
        except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
            self._snapshot_writer_failed = True
            logger.warning(
                "Job-run snapshot persistence disabled after %s",
                type(exc).__name__,
            )
            return
        self._last_snapshot_at = self.heartbeat_at

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "deadline_at": self.deadline_at.isoformat(),
            "heartbeat_at": self.heartbeat_at.isoformat(),
            "timed_out": self.timed_out,
            "progress": {
                "phase": self.phase,
                "total_slices": self.total_slices,
                "started_slices": self.started_slices,
                "completed_slices": self.completed_slices,
                "succeeded_slices": self.succeeded_slices,
                "failed_slices": self.failed_slices,
                "skipped_slices": self.skipped_slices,
            },
        }


async def run_periodic_heartbeat(
    context: JobRunContext,
    *,
    interval_seconds: float = 30,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Persist liveness even while the active provider coroutine is blocked."""
    if not 30 <= interval_seconds <= 60:
        raise ValueError("Heartbeat interval must be between 30 and 60 seconds")
    while True:
        await sleep(interval_seconds)
        context.heartbeat()
