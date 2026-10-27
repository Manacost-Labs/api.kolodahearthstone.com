"""Process-local stale-while-revalidate snapshots for expensive diagnostics.

Health and overview payloads parse and validate every dataset (several
seconds of CPU). A caller inside ``ttl_seconds`` reuses the snapshot. A caller
within ``max_stale_seconds`` gets the previous snapshot at once and starts one
background refresh. Only a missing or very old snapshot is built in the
request, and concurrent callers wait for that single build.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class StaleWhileRevalidate(Generic[T]):
    def __init__(
        self,
        name: str,
        build: Callable[[], T],
        *,
        ttl_seconds: float,
        max_stale_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_stale_seconds < ttl_seconds:
            raise ValueError("max_stale_seconds must be at least ttl_seconds")
        self.name = name
        self._build = build
        self.ttl_seconds = ttl_seconds
        self.max_stale_seconds = max_stale_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._value: T | None = None
        self._has_value = False
        self._built_at = 0.0
        self._refreshing = False

    def get(self) -> T:
        return self.get_with_age()[0]

    def get_with_age(self) -> tuple[T, float]:
        """Return the snapshot and its age in seconds at the time of the call."""
        now = self._now()
        with self._lock:
            if self._has_value:
                age = max(0.0, now - self._built_at)
                if age < self.ttl_seconds:
                    return self._value, age  # type: ignore[return-value]
                if age < self.max_stale_seconds:
                    if not self._refreshing:
                        self._refreshing = True
                        try:
                            threading.Thread(
                                target=self._refresh_in_background,
                                name=f"{self.name}-refresh",
                                daemon=True,
                            ).start()
                        except RuntimeError:
                            # No thread available: a later stale read retries.
                            self._refreshing = False
                            logger.exception("%s background refresh did not start", self.name)
                    return self._value, age  # type: ignore[return-value]
            started = self._built_at
        with self._build_lock:
            with self._lock:
                # Another caller finished a build while this one waited.
                if self._has_value and self._built_at != started:
                    return self._value, max(0.0, self._now() - self._built_at)  # type: ignore[return-value]
            value = self._build()
            self._store(value)
            return value, 0.0

    def reset(self) -> None:
        with self._lock:
            self._value = None
            self._has_value = False
            self._built_at = 0.0
            self._refreshing = False

    def _store(self, value: T) -> None:
        with self._lock:
            self._value = value
            self._has_value = True
            self._built_at = self._now()

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def _refresh_in_background(self) -> None:
        try:
            with self._build_lock:
                with self._lock:
                    # A request may have rebuilt it while this refresh waited.
                    fresh = self._has_value and self._now() - self._built_at < self.ttl_seconds
                if not fresh:
                    self._store(self._build())
        except Exception:
            # The previous snapshot stays served; the next stale read retries.
            logger.exception("%s background refresh failed", self.name)
        finally:
            with self._lock:
                self._refreshing = False
