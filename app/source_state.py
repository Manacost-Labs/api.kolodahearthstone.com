"""Typed source-state enum (Phase 4).

``SourceState`` is a str-enum: members compare equal to the raw strings
("ok", "fetch_error", ...) and serialize to JSON as those strings, so the
wire format of status files, datasets, API responses and log events is
unchanged byte-for-byte.

NOTE on level sets: ``ERROR_STATES``/``WARN_STATES`` are the canonical mapping
used by ``refresh_log._level_for``. ``quality_error`` is a WARN state there
(not an error), while an exhausted job deadline is an ERROR state.
"""

from __future__ import annotations

from enum import Enum


class SourceState(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FETCH_ERROR = "fetch_error"
    HTTP_ERROR = "http_error"
    BLOCKED_BY_PROTECTION = "blocked_by_protection"
    PROXY_REQUIRED = "proxy_required"
    QUALITY_ERROR = "quality_error"
    TIMED_OUT = "timed_out"
    NEVER_FETCHED = "never_fetched"

    def __str__(self) -> str:  # keep f-strings emitting the raw value
        return str(self.value)


EFFECTIVE_OK_CACHED = "ok_cached"  # effective_state value, not a source state

# error/warn split matches refresh_log._level_for (see module docstring).
ERROR_STATES: frozenset[str] = frozenset(
    {
        SourceState.FETCH_ERROR,
        SourceState.HTTP_ERROR,
        SourceState.BLOCKED_BY_PROTECTION,
        SourceState.PROXY_REQUIRED,
        SourceState.TIMED_OUT,
    }
)
WARN_STATES: frozenset[str] = frozenset(
    {
        SourceState.QUALITY_ERROR,
        SourceState.PARTIAL,
    }
)

# Every failure state of a fetch attempt (used e.g. by the stale-data alert in
# fetcher._maybe_stale_data_alert, which historically DOES include quality_error).
FAILURE_STATES: frozenset[str] = ERROR_STATES | {SourceState.QUALITY_ERROR}
