from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar, cast

from .config import data_dir

_STATE_VERSION = 1
_STALE_RESERVATION_SECONDS = 3600
_PERIOD_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$")
_T = TypeVar("_T")


class BrightDataStateError(RuntimeError):
    pass


class BrightDataBudgetExceeded(BrightDataStateError):
    pass


class BrightDataCircuitOpen(BrightDataStateError):
    pass


@dataclass(frozen=True)
class BrightDataUsageSnapshot:
    billed_requests: int
    reserved_requests: int
    remaining_requests: int
    consecutive_failures: int
    circuit_open_until: str | None


@dataclass(frozen=True)
class BrightDataReservation:
    reservation_id: str
    snapshot: BrightDataUsageSnapshot


@dataclass
class _UsageState:
    period_id: str
    updated_at: str
    monthly_limit: int
    billed_requests: int
    attempts: int
    successful_requests: int
    consecutive_failures: int
    circuit_open_until: str | None
    reservations: dict[str, str]

    def to_wire(self) -> dict[str, object]:
        return {
            "version": _STATE_VERSION,
            "period_id": self.period_id,
            "updated_at": self.updated_at,
            "monthly_limit": self.monthly_limit,
            "billed_requests": self.billed_requests,
            "attempts": self.attempts,
            "successful_requests": self.successful_requests,
            "consecutive_failures": self.consecutive_failures,
            "circuit_open_until": self.circuit_open_until,
            "reservations": dict(self.reservations),
        }


def usage_path() -> Path:
    return data_dir() / "brightdata" / "usage.json"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_now(value: datetime | None) -> datetime:
    moment = value or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _period_id(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def _parse_period(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = _PERIOD_RE.fullmatch(value)
    if match is None:
        return None
    return int(match.group("year")), int(match.group("month"))


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _empty_state(moment: datetime, monthly_limit: int) -> _UsageState:
    return _UsageState(
        period_id=_period_id(moment),
        updated_at=_iso(moment),
        monthly_limit=monthly_limit,
        billed_requests=0,
        attempts=0,
        successful_requests=0,
        consecutive_failures=0,
        circuit_open_until=None,
        reservations={},
    )


def _nonnegative_int(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool):
        raise BrightDataStateError("Bright Data usage state is unavailable")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
    else:
        raise BrightDataStateError("Bright Data usage state is unavailable")
    if parsed < 0:
        raise BrightDataStateError("Bright Data usage state is unavailable")
    return parsed


def _decode_state(path: Path) -> dict[str, object]:
    try:
        decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        raise BrightDataStateError("Bright Data usage state is unavailable") from None
    if not isinstance(decoded, dict):
        raise BrightDataStateError("Bright Data usage state is unavailable")
    return cast(dict[str, object], decoded)


def _load_state(
    path: Path,
    moment: datetime,
    monthly_limit: int,
    *,
    rollover: bool,
) -> _UsageState:
    if not path.exists():
        raise BrightDataStateError("Bright Data usage state is unavailable")
    raw = _decode_state(path)
    version = raw.get("version")
    if isinstance(version, bool) or version != _STATE_VERSION:
        raise BrightDataStateError("Bright Data usage state is unavailable")
    period = raw.get("period_id")
    parsed_period = _parse_period(period)
    current_period = _parse_period(_period_id(moment))
    if parsed_period is None or current_period is None:
        raise BrightDataStateError("Bright Data usage state is unavailable")
    if parsed_period > current_period:
        raise BrightDataStateError("Bright Data usage state is unavailable")

    reservations_raw = raw.get("reservations")
    if not isinstance(reservations_raw, dict):
        raise BrightDataStateError("Bright Data usage state is unavailable")
    reservations_untyped = cast(dict[object, object], reservations_raw)
    reservations: dict[str, str] = {}
    for reservation_id, reserved_at in reservations_untyped.items():
        if (
            not isinstance(reservation_id, str)
            or not isinstance(reserved_at, str)
            or _parse_datetime(reserved_at) is None
        ):
            raise BrightDataStateError("Bright Data usage state is unavailable")
        reservations[reservation_id] = reserved_at

    open_until_raw = raw.get("circuit_open_until")
    if open_until_raw is not None and (
        not isinstance(open_until_raw, str) or _parse_datetime(open_until_raw) is None
    ):
        raise BrightDataStateError("Bright Data usage state is unavailable")
    open_until = open_until_raw if isinstance(open_until_raw, str) else None
    loaded = _UsageState(
        period_id=cast(str, period),
        updated_at=_iso(moment),
        monthly_limit=monthly_limit,
        billed_requests=_nonnegative_int(raw, "billed_requests"),
        attempts=_nonnegative_int(raw, "attempts"),
        successful_requests=_nonnegative_int(raw, "successful_requests"),
        consecutive_failures=_nonnegative_int(raw, "consecutive_failures"),
        circuit_open_until=open_until,
        reservations=reservations,
    )
    if rollover and parsed_period < current_period:
        rolled = _empty_state(moment, monthly_limit)
        # A request reserved before UTC midnight may complete after another
        # worker has opened the new ledger period. Keep those reservations so
        # they still consume capacity and can be completed exactly once. Any
        # unknown billing outcome is then charged conservatively in the new
        # period rather than silently disappearing.
        rolled.reservations = dict(loaded.reservations)
        rolled.attempts = len(rolled.reservations)
        rolled.consecutive_failures = loaded.consecutive_failures
        rolled.circuit_open_until = loaded.circuit_open_until
        return rolled
    return loaded


def _expire_stale_reservations(
    state: _UsageState,
    moment: datetime,
    *,
    circuit_failure_threshold: int,
    circuit_cooldown_seconds: int,
) -> None:
    cutoff = moment - timedelta(seconds=_STALE_RESERVATION_SECONDS)
    expired = [
        reservation_id
        for reservation_id, reserved_at in state.reservations.items()
        if (_parse_datetime(reserved_at) or moment) <= cutoff
    ]
    for reservation_id in expired:
        _ = state.reservations.pop(reservation_id, None)
        # A crashed worker cannot prove that its request was unbilled. Counting
        # it is conservative and preserves the hard monthly cap.
        state.billed_requests += 1
        state.consecutive_failures += 1
    if expired and state.consecutive_failures >= max(
        1, circuit_failure_threshold
    ):
        state.circuit_open_until = _iso(
            moment + timedelta(seconds=max(60, circuit_cooldown_seconds))
        )


def _write_state(path: Path, state: _UsageState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                state.to_wire(),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BrightDataStateError("Bright Data usage state is unavailable") from None


def _snapshot(state: _UsageState) -> BrightDataUsageSnapshot:
    reserved = len(state.reservations)
    return BrightDataUsageSnapshot(
        billed_requests=state.billed_requests,
        reserved_requests=reserved,
        remaining_requests=max(
            0,
            state.monthly_limit - state.billed_requests - reserved,
        ),
        consecutive_failures=state.consecutive_failures,
        circuit_open_until=state.circuit_open_until,
    )


def _mutate(
    monthly_limit: int,
    moment: datetime,
    callback: Callable[[_UsageState], _T],
    *,
    rollover: bool,
) -> _T:
    path = usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            try:
                lock_path.chmod(0o600)
            except OSError:
                pass
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            state = _load_state(
                path,
                moment,
                monthly_limit,
                rollover=rollover,
            )
            result = callback(state)
            state.updated_at = _iso(moment)
            state.monthly_limit = monthly_limit
            _write_state(path, state)
            return result
    except BrightDataStateError:
        raise
    except OSError:
        raise BrightDataStateError("Bright Data usage state is unavailable") from None


def initialize_usage_state(
    *,
    monthly_limit: int,
    billed_requests: int = 0,
    now: datetime | None = None,
) -> BrightDataUsageSnapshot:
    """Create the local paid-request ledger once, from an operator baseline."""
    if (
        monthly_limit <= 0
        or billed_requests < 0
        or billed_requests > monthly_limit
    ):
        raise BrightDataStateError("Bright Data usage baseline is invalid")
    moment = _normalize_now(now)
    path = usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            try:
                lock_path.chmod(0o600)
            except OSError:
                pass
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            if path.exists():
                raise BrightDataStateError(
                    "Bright Data usage state is already initialized"
                )
            state = _empty_state(moment, monthly_limit)
            state.billed_requests = billed_requests
            _write_state(path, state)
            return _snapshot(state)
    except BrightDataStateError:
        raise
    except OSError:
        raise BrightDataStateError("Bright Data usage state is unavailable") from None


def usage_state_initialized() -> bool:
    try:
        return usage_path().is_file()
    except OSError:
        return False


def reserve_request(
    *,
    monthly_limit: int,
    circuit_failure_threshold: int,
    circuit_cooldown_seconds: int,
    now: datetime | None = None,
) -> BrightDataReservation:
    if monthly_limit <= 0:
        raise BrightDataBudgetExceeded("Bright Data monthly budget exhausted")
    moment = _normalize_now(now)

    threshold = max(1, circuit_failure_threshold)
    cooldown = max(60, circuit_cooldown_seconds)

    def reserve(
        state: _UsageState,
    ) -> tuple[BrightDataReservation | None, BrightDataStateError | None]:
        _expire_stale_reservations(
            state,
            moment,
            circuit_failure_threshold=threshold,
            circuit_cooldown_seconds=cooldown,
        )
        open_until = _parse_datetime(state.circuit_open_until)
        if open_until is not None and moment < open_until:
            return None, BrightDataCircuitOpen("Bright Data circuit is open")
        if open_until is not None and state.consecutive_failures < threshold:
            state.circuit_open_until = None
        if state.billed_requests + len(state.reservations) >= monthly_limit:
            return None, BrightDataBudgetExceeded(
                "Bright Data monthly budget exhausted"
            )
        if open_until is None and state.consecutive_failures >= threshold:
            state.circuit_open_until = _iso(moment + timedelta(seconds=cooldown))
            return None, BrightDataCircuitOpen("Bright Data circuit is open")
        capacity = (
            1
            if state.consecutive_failures >= threshold
            else threshold - state.consecutive_failures
        )
        if len(state.reservations) >= capacity:
            return None, BrightDataCircuitOpen("Bright Data circuit is open")
        reservation_id = uuid.uuid4().hex
        state.reservations[reservation_id] = _iso(moment)
        state.attempts += 1
        return (
            BrightDataReservation(
                reservation_id=reservation_id,
                snapshot=_snapshot(state),
            ),
            None,
        )

    reservation, error = _mutate(
        monthly_limit,
        moment,
        reserve,
        rollover=True,
    )
    if error is not None:
        raise error
    if reservation is None:
        raise BrightDataStateError("Bright Data usage state is unavailable")
    return reservation


def finish_request(
    reservation_id: str,
    *,
    monthly_limit: int,
    billed: bool | None,
    succeeded: bool,
    circuit_failure_threshold: int,
    circuit_cooldown_seconds: int,
    now: datetime | None = None,
) -> BrightDataUsageSnapshot:
    if monthly_limit <= 0:
        raise BrightDataStateError("Bright Data usage state is unavailable")
    moment = _normalize_now(now)

    def finish(state: _UsageState) -> BrightDataUsageSnapshot:
        reservation = state.reservations.pop(reservation_id, None)
        if reservation is None:
            raise BrightDataStateError("Bright Data usage state is unavailable")
        if billed is not False:
            # Unknown transport outcomes are potentially billable and therefore
            # consume one slot. This intentionally biases toward cost safety.
            state.billed_requests += 1
        if succeeded:
            state.successful_requests += 1
            state.consecutive_failures = 0
            state.circuit_open_until = None
        else:
            state.consecutive_failures += 1
            if state.consecutive_failures >= max(1, circuit_failure_threshold):
                state.circuit_open_until = _iso(
                    moment + timedelta(seconds=max(60, circuit_cooldown_seconds))
                )
        return _snapshot(state)

    return _mutate(monthly_limit, moment, finish, rollover=False)
