from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Per-refresh-run in-memory cache for HSReplay JSON API responses.
_hsreplay_json_cache: ContextVar[dict[str, Any] | None] = ContextVar(
    "hsreplay_json_cache", default=None
)


@dataclass
class _ParsesUnixPaidRequestBudget:
    used: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_parsesunix_paid_request_budget: ContextVar[_ParsesUnixPaidRequestBudget | None] = (
    ContextVar("parsesunix_paid_request_budget", default=None)
)

_direct_only_candidate_confirmation: ContextVar[bool] = ContextVar(
    "direct_only_candidate_confirmation",
    default=False,
)


@contextmanager
def direct_only_candidate_confirmation() -> Iterator[None]:
    """Restrict one recovery run to the free, unproxied HSReplay origin."""

    token = _direct_only_candidate_confirmation.set(True)
    try:
        yield
    finally:
        _direct_only_candidate_confirmation.reset(token)


def is_direct_only_candidate_confirmation() -> bool:
    return _direct_only_candidate_confirmation.get()


def begin_refresh_run() -> None:
    _hsreplay_json_cache.set({})
    _parsesunix_paid_request_budget.set(_ParsesUnixPaidRequestBudget())


def end_refresh_run() -> None:
    _hsreplay_json_cache.set(None)
    _parsesunix_paid_request_budget.set(None)


def reserve_parsesunix_paid_request(limit: int) -> bool:
    """Atomically reserve one provider call inside the current refresh only."""

    budget = _parsesunix_paid_request_budget.get()
    if budget is None or limit <= 0:
        return False
    with budget.lock:
        if budget.used >= limit:
            return False
        budget.used += 1
        return True


def parsesunix_paid_requests_used() -> int | None:
    budget = _parsesunix_paid_request_budget.get()
    if budget is None:
        return None
    with budget.lock:
        return budget.used


def get_hsreplay_json_cache() -> dict[str, Any] | None:
    return _hsreplay_json_cache.get()


def get_cached_hsreplay_json(key: str) -> dict[str, Any] | None:
    cache = _hsreplay_json_cache.get()
    if not cache:
        return None
    entry = cache.get(key)
    if isinstance(entry, dict):
        return entry
    return None


def set_cached_hsreplay_json(key: str, payload: dict[str, Any]) -> None:
    cache = _hsreplay_json_cache.get()
    if cache is None:
        return
    cache[key] = payload
