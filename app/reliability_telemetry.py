"""Durable, privacy-safe reliability telemetry for logical source refreshes.

This module deliberately records one terminal outcome per source and refresh
run. Provider retries, URLs, response bodies, exception messages, and secrets
never enter this database or its public aggregate report.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .source_state import SourceState
from .storage import root_dir

OUTCOME_TIMED_OUT = str(SourceState.TIMED_OUT)
OUTCOMES = (
    "fresh_published",
    "provisional",
    "lkg_served",
    "failed",
    OUTCOME_TIMED_OUT,
    "skipped",
)
ELIGIBLE_OUTCOMES = OUTCOMES[:-1]
WINDOWS = (
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
)
METHODOLOGY_VERSION = "logical-source-observed-v2"
SLO_TARGET_RATE_PCT = 99.0
FAILURE_REASONS = (
    "proxy_payment",
    "authentication",
    "rate_limited",
    "access_blocked",
    "upstream_4xx",
    "upstream_5xx",
    "timeout",
    "transport",
    "unavailable",
    "contract",
    "parse_error",
    "regression",
    "backend_policy",
    "ai_quarantine",
    "publication_sync",
    "preflight",
    "dependency",
    "unknown",
)

_schema_lock = threading.Lock()


def telemetry_db_path() -> Path:
    return root_dir() / "parser-telemetry.sqlite3"


def reliability_cache_revision(*, path: Path | None = None) -> str:
    """Return a cheap revision that changes for SQLite or WAL writes."""

    resolved_path = path or telemetry_db_path()
    fingerprints: list[str] = []
    for candidate in (
        resolved_path,
        Path(f"{resolved_path}-wal"),
    ):
        try:
            stat = candidate.stat()
        except OSError:
            continue
        fingerprints.append(f"{candidate.name}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(fingerprints) or "not-collected"


def classify_terminal_status(status: Mapping[str, object]) -> str:
    """Map a terminal source status to one stable, bounded outcome."""

    state = str(status.get("state") or "").strip().lower()
    if status.get("skipped") is True or state in {"locked", "skipped"}:
        return "skipped"
    if state == SourceState.TIMED_OUT:
        return OUTCOME_TIMED_OUT
    if status.get("serving_cached_dataset") is True:
        return "lkg_served"
    if state == "ok" and status.get("provisional") is True:
        return "provisional"
    if state == "ok":
        return "fresh_published"
    return "failed"


def classify_failure_reason(status: Mapping[str, object]) -> str:
    """Reduce an unsuccessful refresh to a safe, bounded operational reason."""

    outcome = classify_terminal_status(status)
    if outcome in {"fresh_published", "provisional"}:
        return "none"

    explicit = str(status.get("failure_reason_code") or "").strip().lower()
    if explicit in FAILURE_REASONS:
        return explicit

    ai_review = status.get("ai_review") or status.get("latest_ai_review")
    if isinstance(ai_review, Mapping) and ai_review.get("quarantine") is True:
        return "ai_quarantine"

    signal = _failure_signal(status)
    proxy_status = _bounded_http_status(
        status.get("last_refresh_proxy_status") or status.get("proxy_status")
    )
    http_status = _bounded_http_status(status.get("http_status"))

    if "regression" in signal or "metric count dropped" in signal:
        return "regression"
    if "backend policy" in signal or "policy changed" in signal:
        return "backend_policy"
    if "cache_sync_error" in signal or "publication sync" in signal:
        return "publication_sync"
    if proxy_status in {402, 407} or any(
        marker in signal
        for marker in ("proxy_402", "proxy_407", "payment required", "connect tunnel")
    ):
        return "proxy_payment"
    if http_status == 401 or any(
        marker in signal
        for marker in (
            "not authenticated",
            "unauthorized",
            "login required",
            "session expired",
            "cookie expired",
        )
    ):
        return "authentication"
    if http_status == 429 or "rate limit" in signal or "too many requests" in signal:
        return "rate_limited"
    if http_status == 403 or any(
        marker in signal
        for marker in (
            "access denied",
            "blocked_403",
            "captcha",
            "challenge page",
            "cloudflare",
            "forbidden",
        )
    ):
        return "access_blocked"
    if outcome == OUTCOME_TIMED_OUT or "timed out" in signal or "timeout" in signal:
        return "timeout"
    if (http_status is not None and 500 <= http_status <= 599) or any(
        marker in signal
        for marker in ("http 500", "http 502", "http 503", "http 504", "bad gateway")
    ):
        return "upstream_5xx"
    if http_status is not None and 400 <= http_status <= 499:
        return "upstream_4xx"
    if any(
        marker in signal
        for marker in (
            "source contract failed",
            "quality check failed",
            "too few rows",
            "fill rate",
            "schema mismatch",
        )
    ):
        return "contract"
    if "parse_error" in signal or "parse error" in signal or "parser" in signal:
        return "parse_error"
    if "preflight" in signal:
        return "preflight"
    if "dependency" in signal or "prefetch" in signal:
        return "dependency"
    if "unavailable" in signal:
        return "unavailable"
    if any(
        marker in signal
        for marker in (
            "connectionerror",
            "connecterror",
            "networkerror",
            "proxyerror",
            "transporterror",
        )
    ):
        return "transport"
    return "unknown"


def record_terminal_results(
    run_id: str,
    results: Iterable[Mapping[str, object]],
    *,
    finished_at: datetime | None = None,
    path: Path | None = None,
) -> int:
    """Persist terminal source results atomically and idempotently.

    A deterministic key for ``run_id + source_id`` prevents a telemetry retry
    from inflating the denominator. Only bounded operational fields are stored.
    """

    completed_at = _as_utc(finished_at or datetime.now(UTC))
    completed_epoch = completed_at.timestamp()
    recorded_epoch = datetime.now(UTC).timestamp()
    normalized_run_id = str(run_id).strip()[:160]
    if not normalized_run_id:
        raise ValueError("run_id is required")

    rows: list[tuple[str, str, str, float, str, str, str, float]] = []
    seen_sources: set[str] = set()
    for result in results:
        source_id = str(result.get("source_id") or "").strip()[:160]
        if not source_id or source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        outcome = classify_terminal_status(result)
        reason_code = classify_failure_reason(result)
        terminal_state = _bounded_terminal_state(result.get("state"))
        attempt_id = hashlib.sha256(
            f"{normalized_run_id}\0{source_id}".encode()
        ).hexdigest()
        rows.append(
            (
                attempt_id,
                normalized_run_id,
                source_id,
                completed_epoch,
                outcome,
                terminal_state,
                reason_code,
                recorded_epoch,
            )
        )

    if not rows:
        return 0

    resolved_path = path or telemetry_db_path()
    connection = _connect(resolved_path)
    inserted = 0
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO source_attempts (
                    attempt_id,
                    run_id,
                    source_id,
                    finished_at,
                    outcome,
                    terminal_state,
                    reason_code,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            inserted += max(0, cursor.rowcount)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted


def build_reliability_report(
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return aggregate reliability windows without per-source information."""

    report_at = _as_utc(now or datetime.now(UTC))
    resolved_path = path or telemetry_db_path()
    if not resolved_path.exists():
        return _empty_report(report_at)

    connection = _connect(resolved_path)
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN")
        coverage_row = connection.execute(
            "SELECT MIN(finished_at) FROM source_attempts"
        ).fetchone()
        coverage_epoch = (
            float(coverage_row[0])
            if coverage_row is not None and coverage_row[0] is not None
            else None
        )
        windows = [
            _query_window(
                connection,
                label=label,
                duration=duration,
                report_at=report_at,
                coverage_epoch=coverage_epoch,
            )
            for label, duration in WINDOWS
        ]
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "methodology": _methodology(),
        "generated_at": report_at.isoformat(),
        "coverage_started_at": _iso_from_epoch(coverage_epoch),
        "windows": windows,
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    # The in-process lock avoids competing PRAGMA journal-mode transitions.
    # SQLite's own lock plus busy_timeout protects the same initialization when
    # multiple parser processes start concurrently.
    with _schema_lock:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                finished_at REAL NOT NULL,
                outcome TEXT NOT NULL CHECK (
                    outcome IN (
                        'fresh_published',
                        'provisional',
                        'lkg_served',
                        'failed',
                        'timed_out',
                        'skipped'
                    )
                ),
                terminal_state TEXT NOT NULL,
                reason_code TEXT NOT NULL DEFAULT 'unknown',
                recorded_at REAL NOT NULL
            )
            """
        )
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(source_attempts)")
        }
        if "reason_code" not in columns:
            connection.execute(
                "ALTER TABLE source_attempts "
                "ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'unknown'"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS source_attempts_finished_at_idx
            ON source_attempts (finished_at)
            """
        )


def _query_window(
    connection: sqlite3.Connection,
    *,
    label: str,
    duration: timedelta,
    report_at: datetime,
    coverage_epoch: float | None,
) -> dict[str, Any]:
    from_at = report_at - duration
    rows = connection.execute(
        """
        SELECT outcome, COUNT(*)
        FROM source_attempts
        WHERE finished_at >= ? AND finished_at <= ?
        GROUP BY outcome
        """,
        (from_at.timestamp(), report_at.timestamp()),
    ).fetchall()
    counts = {outcome: 0 for outcome in OUTCOMES}
    for outcome, count in rows:
        if outcome in counts:
            counts[str(outcome)] = int(count)

    reason_rows = connection.execute(
        """
        SELECT COALESCE(reason_code, 'unknown'), COUNT(*)
        FROM source_attempts
        WHERE finished_at >= ? AND finished_at <= ?
          AND outcome IN ('lkg_served', 'failed', 'timed_out')
        GROUP BY COALESCE(reason_code, 'unknown')
        """,
        (from_at.timestamp(), report_at.timestamp()),
    ).fetchall()
    failure_reasons = {reason: 0 for reason in FAILURE_REASONS}
    for reason, count in reason_rows:
        bounded_reason = str(reason) if reason in failure_reasons else "unknown"
        failure_reasons[bounded_reason] += int(count)

    total_attempts = sum(counts.values())
    eligible_attempts = sum(counts[outcome] for outcome in ELIGIBLE_OUTCOMES)
    full_fresh = counts["fresh_published"]
    accepted_fresh = full_fresh + counts["provisional"]
    data_available = accepted_fresh + counts["lkg_served"]
    coverage_ratio = _coverage_ratio(
        coverage_epoch=coverage_epoch,
        from_at=from_at,
        report_at=report_at,
    )
    return {
        "window": label,
        "from_at": from_at.isoformat(),
        "to_at": report_at.isoformat(),
        "measurement_status": "observed" if coverage_ratio >= 1.0 else "collecting",
        "coverage_ratio": coverage_ratio,
        "total_attempts": total_attempts,
        "eligible_attempts": eligible_attempts,
        "counts": counts,
        "failure_reasons": failure_reasons,
        "full_fresh_rate_pct": _percentage(full_fresh, eligible_attempts),
        "accepted_fresh_rate_pct": _percentage(accepted_fresh, eligible_attempts),
        "data_available_rate_pct": _percentage(data_available, eligible_attempts),
        "freshness_slo": _slo_budget(
            good_attempts=full_fresh,
            eligible_attempts=eligible_attempts,
            measurement_complete=coverage_ratio >= 1.0,
        ),
        "availability_slo": _slo_budget(
            good_attempts=data_available,
            eligible_attempts=eligible_attempts,
            measurement_complete=coverage_ratio >= 1.0,
        ),
    }


def _empty_report(report_at: datetime) -> dict[str, Any]:
    return {
        "methodology": _methodology(),
        "generated_at": report_at.isoformat(),
        "coverage_started_at": None,
        "windows": [
            {
                "window": label,
                "from_at": (report_at - duration).isoformat(),
                "to_at": report_at.isoformat(),
                "measurement_status": "collecting",
                "coverage_ratio": 0.0,
                "total_attempts": 0,
                "eligible_attempts": 0,
                "counts": {outcome: 0 for outcome in OUTCOMES},
                "failure_reasons": {reason: 0 for reason in FAILURE_REASONS},
                "full_fresh_rate_pct": None,
                "accepted_fresh_rate_pct": None,
                "data_available_rate_pct": None,
                "freshness_slo": _slo_budget(
                    good_attempts=0,
                    eligible_attempts=0,
                    measurement_complete=False,
                ),
                "availability_slo": _slo_budget(
                    good_attempts=0,
                    eligible_attempts=0,
                    measurement_complete=False,
                ),
            }
            for label, duration in WINDOWS
        ],
    }


def _methodology() -> dict[str, Any]:
    return {
        "version": METHODOLOGY_VERSION,
        "unit": "one terminal outcome per source in a refresh run",
        "scope": "generic_refresh_sources",
        "completeness": "observed_attempts_only",
        "limitations": [
            "dedicated_pipeline_sources_excluded",
            "best_effort_write_gaps_not_detectable",
        ],
        "eligible_outcomes": list(ELIGIBLE_OUTCOMES),
        "excluded_outcomes": ["skipped"],
        "slo_target_rate_pct": SLO_TARGET_RATE_PCT,
        "failure_reason_values": list(FAILURE_REASONS),
    }


def _bounded_terminal_state(value: object) -> str:
    state = str(value or "").strip().lower()
    allowed = {str(source_state) for source_state in SourceState} | {
        "locked",
        "skipped",
    }
    return state if state in allowed else "unknown_error"


def _failure_signal(status: Mapping[str, object]) -> str:
    values = (
        status.get("last_refresh_state"),
        status.get("last_refresh_failure_class"),
        status.get("last_refresh_error"),
        status.get("failure_class"),
        status.get("state"),
        status.get("error"),
        status.get("detail"),
    )
    return " ".join(str(value).casefold()[:1000] for value in values if value)[:4000]


def _bounded_http_status(value: object) -> int | None:
    try:
        status = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


def _slo_budget(
    *,
    good_attempts: int,
    eligible_attempts: int,
    measurement_complete: bool,
) -> dict[str, Any]:
    bad_attempts = max(0, eligible_attempts - good_attempts)
    allowed_bad = eligible_attempts * ((100.0 - SLO_TARGET_RATE_PCT) / 100.0)
    remaining = allowed_bad - bad_attempts
    if not measurement_complete or eligible_attempts <= 0:
        objective_status = "collecting"
    elif good_attempts / eligible_attempts >= SLO_TARGET_RATE_PCT / 100.0:
        objective_status = "meeting"
    else:
        objective_status = "breached"
    return {
        "target_rate_pct": SLO_TARGET_RATE_PCT,
        "objective_status": objective_status,
        "good_attempts": good_attempts,
        "bad_attempts": bad_attempts,
        "allowed_bad_attempts": round(allowed_bad, 2),
        "bad_attempts_over_budget": max(0, math.ceil(bad_attempts - allowed_bad)),
        "error_budget_remaining_attempts": round(remaining, 2),
        "error_budget_consumed_pct": (
            round((bad_attempts / allowed_bad) * 100.0, 2)
            if allowed_bad > 0
            else None
        ),
    }


def _coverage_ratio(
    *,
    coverage_epoch: float | None,
    from_at: datetime,
    report_at: datetime,
) -> float:
    if coverage_epoch is None:
        return 0.0
    coverage_at = datetime.fromtimestamp(coverage_epoch, tz=UTC)
    observed_seconds = max(0.0, (report_at - coverage_at).total_seconds())
    window_seconds = max(1.0, (report_at - from_at).total_seconds())
    return round(min(1.0, observed_seconds / window_seconds), 4)


def _percentage(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 2)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()
