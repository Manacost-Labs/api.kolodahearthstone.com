"""Invariants of the health snapshot cache, as a caller observes them.

The build is the external cost (parsing every dataset); here it returns the
clock reading it was built at, so a caller can tell how old an answer is.
Background refreshes run on real threads and are joined before each check.
"""

from __future__ import annotations

import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.stale_while_revalidate import StaleWhileRevalidate

TTL = 15.0
MAX_STALE = 600.0


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def join_refreshes(name: str) -> None:
    for thread in threading.enumerate():
        if thread.name == f"{name}-refresh":
            thread.join(5)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=0, max_value=900, allow_nan=False), min_size=1, max_size=25))
def test_answers_are_never_older_than_the_stale_limit_and_staleness_never_outlives_one_read(
    steps: list[float],
) -> None:
    clock = Clock()

    def build() -> float:
        return clock.now

    cache = StaleWhileRevalidate("prop", build, ttl_seconds=TTL, max_stale_seconds=MAX_STALE, clock=clock)
    previous: float | None = None
    for step in steps:
        clock.now += step
        value, age = cache.get_with_age()
        join_refreshes("prop")

        # The answer is the build it claims to be, and never beyond the limit.
        assert value == pytest.approx(clock.now - age)
        assert age < MAX_STALE
        # A snapshot younger than the fresh window is reused, not rebuilt.
        if previous is not None and clock.now - previous < TTL:
            assert value == previous
        if age >= TTL:
            # One stale answer is enough: the next caller gets a fresh one.
            assert cache.get_with_age()[1] < TTL
        else:
            # A fresh answer starts no rebuild behind it.
            assert cache.get() == value
        previous = cache.get()


def test_failed_background_refresh_keeps_the_last_answer_and_is_retried() -> None:
    clock = Clock()
    results: list[object] = ["first", RuntimeError("upstream file vanished"), "third"]

    def build() -> object:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    cache = StaleWhileRevalidate("retry", build, ttl_seconds=TTL, max_stale_seconds=MAX_STALE, clock=clock)
    assert cache.get() == "first"
    clock.now = 1020.0

    assert cache.get() == "first"
    join_refreshes("retry")
    assert cache.get() == "first"
    join_refreshes("retry")
    assert cache.get_with_age() == ("third", 0.0)


def test_concurrent_first_callers_wait_for_one_build() -> None:
    started = threading.Event()
    release = threading.Event()
    builds: list[str] = []

    def build() -> str:
        builds.append("build")
        started.set()
        release.wait(5)
        return "diagnostics"

    cache = StaleWhileRevalidate("cold", build, ttl_seconds=TTL, max_stale_seconds=MAX_STALE)
    answers: list[str] = []
    first = threading.Thread(target=lambda: answers.append(cache.get()))
    first.start()
    started.wait(5)
    second = threading.Thread(target=lambda: answers.append(cache.get()))
    second.start()
    release.set()
    first.join(5)
    second.join(5)

    assert answers == ["diagnostics", "diagnostics"]
    assert builds == ["build"]


def test_stale_limit_must_cover_the_fresh_window() -> None:
    with pytest.raises(ValueError):
        StaleWhileRevalidate("bad", lambda: 1, ttl_seconds=15.0, max_stale_seconds=5.0)
