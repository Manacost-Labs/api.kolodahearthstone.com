from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self, TextIO

from .config import data_dir


class ResourceLocked(RuntimeError):
    """Raised when another process already owns a requested resource."""

    def __init__(self, resource_id: str, owner: dict[str, Any] | None = None) -> None:
        self.resource_id = resource_id
        self.owner = dict(owner or {})
        super().__init__(f"Resource is already locked: {resource_id}")

    def as_outcome(self) -> dict[str, Any]:
        return {
            "state": "locked",
            "skipped": True,
            "reason": "resource_locked",
            "locked_resource": self.resource_id,
            "owner": self.owner,
        }


class ResourceLockSet:
    """Acquire a deterministic, non-blocking set of persistent flock files."""

    def __init__(
        self,
        resource_ids: list[str] | tuple[str, ...],
        *,
        lock_dir: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.resource_ids = tuple(sorted(set(resource_ids)))
        self.lock_dir = lock_dir if lock_dir is not None else data_dir() / ".locks"
        self.metadata = dict(metadata or {})
        self.paths = {
            resource_id: self.lock_dir / _lock_filename(resource_id)
            for resource_id in self.resource_ids
        }
        self._handles: list[TextIO] = []

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_args: object) -> None:
        self.release()

    def acquire(self) -> ResourceLockSet:
        if self._handles:
            raise RuntimeError("Resource lock set is already acquired")
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            for resource_id in self.resource_ids:
                handle = self.paths[resource_id].open("a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    owner = _read_metadata(handle)
                    handle.close()
                    raise ResourceLocked(resource_id, owner) from exc

                self._handles.append(handle)
                payload = {
                    **self.metadata,
                    "pid": os.getpid(),
                    "resource_id": resource_id,
                    "acquired_at": datetime.now(UTC).isoformat(),
                }
                handle.seek(0)
                handle.truncate()
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
            return self
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        while self._handles:
            handle = self._handles.pop()
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _lock_filename(resource_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", resource_id).strip(".-")[:64]
    digest = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:12]
    return f"{slug or 'resource'}.{digest}.lock"


def _read_metadata(handle: TextIO) -> dict[str, Any]:
    try:
        handle.seek(0)
        value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
