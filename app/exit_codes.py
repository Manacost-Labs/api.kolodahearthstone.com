from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable process outcomes for CLI and systemd adapters."""

    OK = 0
    ERROR = 1
    USAGE = 2
    DEGRADED = 10
