"""Fail-closed systemd condition for targeted source recovery.

Exit code 0 means that a recovery refresh should run. Any other ordinary exit
code makes ``ExecCondition=`` skip the service without marking the unit failed.
The condition never fetches data and never mutates parser state.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from .config import data_dir
from .source_state import FAILURE_STATES, WARN_STATES
from .sources import SOURCE_BY_ID

RECOVERY_STATES = frozenset(str(state) for state in FAILURE_STATES | WARN_STATES)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def recovery_decision(
    status: dict[str, Any] | None,
    *,
    now: datetime,
    min_age_seconds: float,
) -> dict[str, Any]:
    """Decide whether the latest attempt is an old-enough live failure."""

    if not isinstance(status, dict):
        return {"ok": False, "run": False, "reason": "missing_status"}
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative")

    serving_lkg = status.get("serving_cached_dataset") is True
    if serving_lkg:
        attempt_state = str(status.get("last_refresh_state") or "")
        raw_attempt_at = status.get("last_refresh_at")
    else:
        attempt_state = str(status.get("state") or "")
        raw_attempt_at = status.get("fetched_at")

    base = {
        "ok": True,
        "run": False,
        "attempt_state": attempt_state or None,
        "serving_cached_dataset": serving_lkg,
    }
    if attempt_state == "ok" and not serving_lkg:
        return {**base, "reason": "latest_attempt_fresh"}
    failed_lkg = serving_lkg and bool(attempt_state) and attempt_state != "ok"
    if attempt_state not in RECOVERY_STATES and not failed_lkg:
        return {**base, "reason": "latest_attempt_not_failed"}

    attempt_at = _parse_timestamp(raw_attempt_at)
    if attempt_at is None:
        return {**base, "reason": "invalid_attempt_time"}
    age_seconds = (now.astimezone(UTC) - attempt_at).total_seconds()
    timed = {
        **base,
        "attempt_at": attempt_at.isoformat(),
        "age_seconds": round(age_seconds, 3),
    }
    if age_seconds < 0:
        return {**timed, "reason": "attempt_time_in_future"}
    if age_seconds < min_age_seconds:
        return {**timed, "reason": "minimum_age_not_reached"}
    return {**timed, "run": True, "reason": "latest_attempt_failed"}


def _read_status(source_id: str) -> dict[str, Any] | None:
    path = data_dir() / "statuses" / f"{source_id}.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recovery only after an old-enough failed source refresh."
    )
    parser.add_argument("--source", required=True, help="Configured source id.")
    parser.add_argument(
        "--min-age-seconds",
        type=_nonnegative_float,
        default=300.0,
        help="Minimum age of the latest failed attempt (default: 300).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.source not in SOURCE_BY_ID:
        print(
            json.dumps(
                {
                    "ok": False,
                    "run": False,
                    "source_id": args.source,
                    "reason": "unknown_source",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        status = _read_status(args.source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        decision = {
            "ok": False,
            "run": False,
            "reason": "status_read_error",
            "error_type": type(exc).__name__,
        }
    else:
        decision = recovery_decision(
            status,
            now=_utc_now(),
            min_age_seconds=args.min_age_seconds,
        )
    payload = {"source_id": args.source, **decision}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("run") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
