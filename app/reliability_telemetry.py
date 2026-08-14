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
METHODOLOGY_VERSION = "logical-source-observed-v9"
SLO_TARGET_RATE_PCT = 99.0
PIPELINE_SCHEDULE_LEDGER_READY = False
COVERAGE_BUCKET_SECONDS = 24 * 60 * 60
COVERAGE_MAX_GAP_SECONDS = 25 * 60 * 60
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
AI_REVIEW_STATES = ("not_run", "ok", "disabled", "skipped", "error")
AI_REVIEW_VERDICTS = ("not_run", "pass", "fail", "uncertain")
AI_DIAGNOSIS_CLASSIFICATIONS = (
    "not_run",
    "healthy",
    "anomalous",
    "inconclusive",
)
AI_DIAGNOSIS_DOMAINS = (
    "none",
    "identity",
    "protection",
    "auth",
    "scope",
    "schema",
    "completeness",
    "semantics",
    "freshness",
    "regression",
    "backend_policy",
    "unknown",
)

_schema_lock = threading.Lock()


def telemetry_db_path() -> Path:
    return root_dir() / "parser-telemetry.sqlite3"


def canonical_scrape_cohort_hash() -> str | None:
    """Hash the current complete generic scrape registry without exposing IDs."""

    from .sources import SOURCES

    source_ids = sorted(source.id for source in SOURCES if source.kind == "scrape")
    if not source_ids:
        return None
    return hashlib.sha256("\0".join(source_ids).encode()).hexdigest()


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
    if (
        status.get("diagnostic") is True
        or status.get("skipped") is True
        or state in {"diagnostic", "diagnostic_failed", "locked", "skipped"}
    ):
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


def _ai_terminal_fields(
    status: Mapping[str, object],
) -> tuple[str, str, str, str, str, int]:
    review = status.get("ai_review") or status.get("latest_ai_review")
    diagnosis = status.get("ai_diagnosis") or status.get("latest_ai_diagnosis")
    review_mapping = review if isinstance(review, Mapping) else {}
    diagnosis_mapping = diagnosis if isinstance(diagnosis, Mapping) else {}
    review_state = _bounded_enum(
        review_mapping.get("state"),
        AI_REVIEW_STATES,
        default="not_run",
    )
    review_verdict = _bounded_enum(
        review_mapping.get("verdict"),
        AI_REVIEW_VERDICTS,
        default="not_run",
    )
    diagnosis_state = _bounded_enum(
        diagnosis_mapping.get("state"),
        AI_REVIEW_STATES,
        default="not_run",
    )
    diagnosis_classification = _bounded_enum(
        diagnosis_mapping.get("classification"),
        AI_DIAGNOSIS_CLASSIFICATIONS,
        default="not_run",
    )
    diagnosis_domain = _bounded_enum(
        diagnosis_mapping.get("failure_domain"),
        AI_DIAGNOSIS_DOMAINS,
        default="none",
    )
    quarantined = int(review_mapping.get("quarantine") is True)
    return (
        review_state,
        review_verdict,
        diagnosis_state,
        diagnosis_classification,
        diagnosis_domain,
        quarantined,
    )


def record_terminal_results(
    run_id: str,
    results: Iterable[Mapping[str, object]],
    *,
    finished_at: datetime | None = None,
    path: Path | None = None,
    coverage_scope: str = "partial",
    expected_source_count: int | None = None,
    refresh_window_id: str | None = None,
) -> int:
    """Persist terminal source results atomically and idempotently.

    A deterministic key for ``run_id + source_id`` prevents a telemetry retry
    from creating a duplicate physical attempt. When retries share an explicit
    ``refresh_window_id``, reports use their latest non-skipped terminal result
    as one logical SLO outcome while retaining every row for cost accounting.
    Only bounded operational fields are stored.
    """

    completed_at = _as_utc(finished_at or datetime.now(UTC))
    completed_epoch = completed_at.timestamp()
    recorded_epoch = datetime.now(UTC).timestamp()
    normalized_run_id = str(run_id).strip()[:160]
    if not normalized_run_id:
        raise ValueError("run_id is required")

    rows: list[tuple[object, ...]] = []
    seen_sources: set[str] = set()
    for result in results:
        source_id = str(result.get("source_id") or "").strip()[:160]
        if not source_id or source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        outcome = classify_terminal_status(result)
        reason_code = classify_failure_reason(result)
        ai_fields = _ai_terminal_fields(result)
        terminal_state = _bounded_terminal_state(result.get("state"))
        result_refresh_window_id = _bounded_refresh_window_id(
            refresh_window_id
            if refresh_window_id is not None
            else result.get("refresh_window_id")
        )
        attempt_id = hashlib.sha256(
            f"{normalized_run_id}\0{source_id}".encode()
        ).hexdigest()
        rows.append(
            (
                attempt_id,
                normalized_run_id,
                result_refresh_window_id,
                source_id,
                completed_epoch,
                outcome,
                terminal_state,
                reason_code,
                *ai_fields,
                recorded_epoch,
            )
        )

    if not rows:
        return 0

    normalized_scope = "full" if coverage_scope == "full" else "partial"
    cohort_hash = (
        canonical_scrape_cohort_hash() or "" if normalized_scope == "full" else ""
    )
    observed_sources = sum(1 for row in rows if row[5] != "skipped")
    try:
        expected_sources = (
            max(0, int(expected_source_count))
            if expected_source_count is not None
            else len(rows)
        )
    except (TypeError, ValueError, OverflowError):
        expected_sources = len(rows)
    row_refresh_windows = {str(row[2]) for row in rows if row[2]}
    run_refresh_window_id = (
        next(iter(row_refresh_windows)) if len(row_refresh_windows) == 1 else ""
    )

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
                    refresh_window_id,
                    source_id,
                    finished_at,
                    outcome,
                    terminal_state,
                    reason_code,
                    ai_review_state,
                    ai_review_verdict,
                    ai_diagnosis_state,
                    ai_diagnosis_classification,
                    ai_diagnosis_domain,
                    ai_quarantined,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            inserted += max(0, cursor.rowcount)
        connection.execute(
            """
            INSERT OR IGNORE INTO refresh_runs (
                run_id,
                refresh_window_id,
                finished_at,
                scope,
                cohort_hash,
                expected_sources,
                observed_sources,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                observed_sources = MAX(
                    refresh_runs.observed_sources,
                    excluded.observed_sources
                )
            WHERE refresh_runs.finished_at = excluded.finished_at
              AND refresh_runs.refresh_window_id = excluded.refresh_window_id
              AND refresh_runs.scope = excluded.scope
              AND refresh_runs.cohort_hash = excluded.cohort_hash
              AND refresh_runs.expected_sources = excluded.expected_sources
            """,
            (
                normalized_run_id,
                run_refresh_window_id,
                completed_epoch,
                normalized_scope,
                cohort_hash,
                expected_sources,
                observed_sources,
                recorded_epoch,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted


def update_terminal_ai_results(
    run_id: str,
    results: Iterable[Mapping[str, object]],
    *,
    path: Path | None = None,
) -> int:
    """Attach bounded advisory AI fields after terminal outcomes are durable."""

    normalized_run_id = str(run_id).strip()[:160]
    if not normalized_run_id:
        raise ValueError("run_id is required")
    rows: list[tuple[object, ...]] = []
    seen_sources: set[str] = set()
    for result in results:
        source_id = str(result.get("source_id") or "").strip()[:160]
        if not source_id or source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        ai_fields = _ai_terminal_fields(result)
        if ai_fields[:5] == ("not_run", "not_run", "not_run", "not_run", "none"):
            continue
        attempt_id = hashlib.sha256(
            f"{normalized_run_id}\0{source_id}".encode()
        ).hexdigest()
        rows.append((*ai_fields, attempt_id, normalized_run_id))
    if not rows:
        return 0

    connection = _connect(path or telemetry_db_path())
    updated = 0
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            cursor = connection.execute(
                """
                UPDATE source_attempts
                SET ai_review_state = ?,
                    ai_review_verdict = ?,
                    ai_diagnosis_state = ?,
                    ai_diagnosis_classification = ?,
                    ai_diagnosis_domain = ?,
                    ai_quarantined = ?
                WHERE attempt_id = ? AND run_id = ?
                """,
                row,
            )
            updated += max(0, cursor.rowcount)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return updated


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
        if not _schema_is_current(connection):
            _ensure_schema(connection)
        connection.execute("BEGIN")
        coverage_cohort_hash = canonical_scrape_cohort_hash()
        coverage_row = (
            connection.execute(
                """
                SELECT MIN(finished_at)
                FROM refresh_runs
                WHERE scope = 'full'
                  AND cohort_hash = ?
                  AND expected_sources > 0
                  AND observed_sources >= expected_sources
                """,
                (coverage_cohort_hash,),
            ).fetchone()
            if coverage_cohort_hash is not None
            else None
        )
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
                coverage_cohort_hash=coverage_cohort_hash,
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
        "coverage_cohort_hash": coverage_cohort_hash,
        "coverage_started_at": _iso_from_epoch(coverage_epoch),
        "windows": windows,
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _schema_is_current(connection: sqlite3.Connection) -> bool:
    """Check the read contract without taking a SQLite write lock."""

    try:
        source_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(source_attempts)")
        }
        run_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(refresh_runs)")
        }
    except sqlite3.DatabaseError:
        return False
    return {
        "attempt_id",
        "run_id",
        "refresh_window_id",
        "source_id",
        "finished_at",
        "outcome",
        "terminal_state",
        "reason_code",
        "ai_review_state",
        "ai_review_verdict",
        "ai_diagnosis_state",
        "ai_diagnosis_classification",
        "ai_diagnosis_domain",
        "ai_quarantined",
        "recorded_at",
    }.issubset(source_columns) and {
        "run_id",
        "refresh_window_id",
        "finished_at",
        "scope",
        "cohort_hash",
        "expected_sources",
        "observed_sources",
        "recorded_at",
    }.issubset(run_columns)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    # The in-process lock avoids competing PRAGMA journal-mode transitions.
    # BEGIN IMMEDIATE also serializes migrations across parser processes. Every
    # waiter re-reads table_info only after the previous migration commits, so
    # two cold-start workers can never attempt the same ALTER TABLE.
    with _schema_lock:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as exc:
            # A concurrent process may already be switching the database to WAL.
            # That PRAGMA can fail immediately despite busy_timeout; the schema
            # transaction below is still safe and will wait for the winner.
            if "locked" not in str(exc).casefold():
                raise
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    refresh_window_id TEXT NOT NULL DEFAULT '',
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
                    ai_review_state TEXT NOT NULL DEFAULT 'not_run',
                    ai_review_verdict TEXT NOT NULL DEFAULT 'not_run',
                    ai_diagnosis_state TEXT NOT NULL DEFAULT 'not_run',
                    ai_diagnosis_classification TEXT NOT NULL DEFAULT 'not_run',
                    ai_diagnosis_domain TEXT NOT NULL DEFAULT 'none',
                    ai_quarantined INTEGER NOT NULL DEFAULT 0,
                    recorded_at REAL NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(source_attempts)")
            }
            migrations = {
                "refresh_window_id": "TEXT NOT NULL DEFAULT ''",
                "reason_code": "TEXT NOT NULL DEFAULT 'unknown'",
                "ai_review_state": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_review_verdict": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_diagnosis_state": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_diagnosis_classification": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_diagnosis_domain": "TEXT NOT NULL DEFAULT 'none'",
                "ai_quarantined": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE source_attempts ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS source_attempts_finished_at_idx
                ON source_attempts (finished_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS source_attempts_refresh_window_idx
                ON source_attempts (refresh_window_id, source_id, finished_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS refresh_runs (
                    run_id TEXT PRIMARY KEY,
                    refresh_window_id TEXT NOT NULL DEFAULT '',
                    finished_at REAL NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('full', 'partial')),
                    cohort_hash TEXT NOT NULL DEFAULT '',
                    expected_sources INTEGER NOT NULL,
                    observed_sources INTEGER NOT NULL,
                    recorded_at REAL NOT NULL
                )
                """
            )
            run_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(refresh_runs)")
            }
            if "cohort_hash" not in run_columns:
                connection.execute(
                    "ALTER TABLE refresh_runs "
                    "ADD COLUMN cohort_hash TEXT NOT NULL DEFAULT ''"
                )
            if "refresh_window_id" not in run_columns:
                connection.execute(
                    "ALTER TABLE refresh_runs "
                    "ADD COLUMN refresh_window_id TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """
                    UPDATE refresh_runs
                    SET refresh_window_id = COALESCE(
                        (
                            SELECT CASE
                                WHEN COUNT(DISTINCT NULLIF(
                                    source_attempts.refresh_window_id,
                                    ''
                                )) = 1
                                THEN MAX(NULLIF(
                                    source_attempts.refresh_window_id,
                                    ''
                                ))
                                ELSE ''
                            END
                            FROM source_attempts
                            WHERE source_attempts.run_id = refresh_runs.run_id
                        ),
                        ''
                    )
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS refresh_runs_coverage_idx
                ON refresh_runs (scope, finished_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS refresh_runs_cohort_coverage_idx
                ON refresh_runs (cohort_hash, scope, finished_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS refresh_runs_window_idx
                ON refresh_runs (refresh_window_id, finished_at)
                """
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _query_window(
    connection: sqlite3.Connection,
    *,
    label: str,
    duration: timedelta,
    report_at: datetime,
    coverage_cohort_hash: str | None,
) -> dict[str, Any]:
    from_at = report_at - duration
    rows = connection.execute(
        """
        WITH ranked_attempts AS (
            SELECT
                attempt_id,
                outcome,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        source_id,
                        CASE
                            WHEN refresh_window_id != ''
                                THEN 'window:' || refresh_window_id
                            ELSE 'attempt:' || attempt_id
                        END
                    ORDER BY
                        CASE WHEN outcome = 'skipped' THEN 1 ELSE 0 END,
                        finished_at DESC,
                        recorded_at DESC,
                        attempt_id DESC
                ) AS logical_rank
            FROM source_attempts
            WHERE finished_at >= ? AND finished_at <= ?
        )
        SELECT outcome, COUNT(*)
        FROM ranked_attempts
        WHERE logical_rank = 1
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
        WITH ranked_attempts AS (
            SELECT
                attempt_id,
                outcome,
                reason_code,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        source_id,
                        CASE
                            WHEN refresh_window_id != ''
                                THEN 'window:' || refresh_window_id
                            ELSE 'attempt:' || attempt_id
                        END
                    ORDER BY
                        CASE WHEN outcome = 'skipped' THEN 1 ELSE 0 END,
                        finished_at DESC,
                        recorded_at DESC,
                        attempt_id DESC
                ) AS logical_rank
            FROM source_attempts
            WHERE finished_at >= ? AND finished_at <= ?
        )
        SELECT COALESCE(reason_code, 'unknown'), COUNT(*)
        FROM ranked_attempts
        WHERE logical_rank = 1
          AND outcome IN ('lkg_served', 'failed', 'timed_out')
        GROUP BY COALESCE(reason_code, 'unknown')
        """,
        (from_at.timestamp(), report_at.timestamp()),
    ).fetchall()
    failure_reasons = {reason: 0 for reason in FAILURE_REASONS}
    for reason, count in reason_rows:
        bounded_reason = str(reason) if reason in failure_reasons else "unknown"
        failure_reasons[bounded_reason] += int(count)

    physical_attempts = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM source_attempts
            WHERE finished_at >= ? AND finished_at <= ?
            """,
            (from_at.timestamp(), report_at.timestamp()),
        ).fetchone()[0]
    )
    total_attempts = sum(counts.values())
    observed_eligible_attempts = sum(counts[outcome] for outcome in ELIGIBLE_OUTCOMES)
    missing_terminal_windows = _missing_terminal_windows(
        connection,
        from_epoch=from_at.timestamp(),
        to_epoch=report_at.timestamp(),
    )
    eligible_attempts = observed_eligible_attempts + missing_terminal_windows
    full_fresh = counts["fresh_published"]
    accepted_fresh = full_fresh + counts["provisional"]
    data_available = accepted_fresh + counts["lkg_served"]
    coverage_ratio = _coverage_ratio(
        connection,
        from_at=from_at,
        report_at=report_at,
        duration=duration,
        cohort_hash=coverage_cohort_hash,
    )
    measurement_complete = coverage_ratio >= 1.0 and PIPELINE_SCHEDULE_LEDGER_READY
    ai_quality = _query_ai_quality(
        connection,
        from_epoch=from_at.timestamp(),
        to_epoch=report_at.timestamp(),
    )
    return {
        "window": label,
        "from_at": from_at.isoformat(),
        "to_at": report_at.isoformat(),
        "measurement_status": "observed" if measurement_complete else "collecting",
        "coverage_ratio": coverage_ratio,
        "physical_attempts": physical_attempts,
        "total_attempts": total_attempts,
        "observed_eligible_attempts": observed_eligible_attempts,
        "missing_terminal_windows": missing_terminal_windows,
        "eligible_attempts": eligible_attempts,
        "counts": counts,
        "failure_reasons": failure_reasons,
        "full_fresh_rate_pct": _percentage(full_fresh, eligible_attempts),
        "accepted_fresh_rate_pct": _percentage(accepted_fresh, eligible_attempts),
        "data_available_rate_pct": _percentage(data_available, eligible_attempts),
        "freshness_slo": _slo_budget(
            good_attempts=full_fresh,
            eligible_attempts=eligible_attempts,
            measurement_complete=measurement_complete,
        ),
        "availability_slo": _slo_budget(
            good_attempts=data_available,
            eligible_attempts=eligible_attempts,
            measurement_complete=measurement_complete,
        ),
        "ai_quality": ai_quality,
    }


def _empty_report(report_at: datetime) -> dict[str, Any]:
    return {
        "methodology": _methodology(),
        "generated_at": report_at.isoformat(),
        "coverage_cohort_hash": canonical_scrape_cohort_hash(),
        "coverage_started_at": None,
        "windows": [
            {
                "window": label,
                "from_at": (report_at - duration).isoformat(),
                "to_at": report_at.isoformat(),
                "measurement_status": "collecting",
                "coverage_ratio": 0.0,
                "physical_attempts": 0,
                "total_attempts": 0,
                "observed_eligible_attempts": 0,
                "missing_terminal_windows": 0,
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
                "ai_quality": _empty_ai_quality(),
            }
            for label, duration in WINDOWS
        ],
    }


def _methodology() -> dict[str, Any]:
    return {
        "version": METHODOLOGY_VERSION,
        "unit": (
            "one final terminal outcome per source in an explicit refresh window; "
            "otherwise one outcome per refresh run; absent expected terminal rows "
            "in recorded runs count as missing terminal windows"
        ),
        "scope": "observed_scrape_and_pipeline_sources",
        "completeness": "observed_attempts_plus_recorded_run_deficits",
        "limitations": [
            "entirely_missing_scheduled_runs_not_detectable_until_ledger",
            "best_effort_write_gaps_not_detectable",
        ],
        "coverage_method": "complete_generic_refresh_per_24h_bucket",
        "coverage_scope": "generic_scrape_sources_only",
        "coverage_max_gap_hours": COVERAGE_MAX_GAP_SECONDS / 3600,
        "coverage_cohort_method": "current_canonical_scrape_registry_hash",
        "combined_slo_readiness": (
            "ready"
            if PIPELINE_SCHEDULE_LEDGER_READY
            else "collecting_pipeline_schedule_ledger"
        ),
        "eligible_outcomes": list(ELIGIBLE_OUTCOMES),
        "excluded_outcomes": ["skipped"],
        "slo_target_rate_pct": SLO_TARGET_RATE_PCT,
        "failure_reason_values": list(FAILURE_REASONS),
        "physical_attempts_method": "all persisted source attempts before window folding",
        "missing_terminal_method": (
            "sum_positive_expected_minus_distinct_terminal_rows_per_recorded_logical_refresh"
        ),
        "ai_accuracy_method": "human_labels_required",
    }


def _bounded_terminal_state(value: object) -> str:
    state = str(value or "").strip().lower()
    allowed = {str(source_state) for source_state in SourceState} | {
        "diagnostic",
        "diagnostic_failed",
        "locked",
        "skipped",
    }
    return state if state in allowed else "unknown_error"


def _bounded_enum(value: object, allowed: tuple[str, ...], *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def _query_ai_quality(
    connection: sqlite3.Connection,
    *,
    from_epoch: float,
    to_epoch: float,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
            outcome,
            ai_review_state,
            ai_review_verdict,
            ai_diagnosis_state,
            ai_diagnosis_classification,
            ai_diagnosis_domain,
            ai_quarantined
        FROM source_attempts
        WHERE finished_at >= ? AND finished_at <= ?
          AND outcome != 'skipped'
        """,
        (from_epoch, to_epoch),
    ).fetchall()
    eligible = len(rows)
    review_attempted = 0
    review_completed = 0
    review_errors = 0
    review_skipped = 0
    review_counts = {
        verdict: 0 for verdict in AI_REVIEW_VERDICTS if verdict != "not_run"
    }
    quarantined = 0
    problem_attempts = 0
    diagnosis_attempted = 0
    diagnosis_completed = 0
    diagnosis_errors = 0
    diagnosis_counts = {
        value: 0 for value in AI_DIAGNOSIS_CLASSIFICATIONS if value != "not_run"
    }
    diagnosis_domains = {value: 0 for value in AI_DIAGNOSIS_DOMAINS if value != "none"}
    for (
        outcome,
        review_state,
        review_verdict,
        diagnosis_state,
        diagnosis_classification,
        diagnosis_domain,
        ai_quarantined,
    ) in rows:
        if review_state in {"ok", "error"}:
            review_attempted += 1
        elif review_state in {"disabled", "skipped"}:
            review_skipped += 1
        if review_state == "error":
            review_errors += 1
        if review_state == "ok" and review_verdict in review_counts:
            review_completed += 1
            review_counts[str(review_verdict)] += 1
        quarantined += int(int(ai_quarantined or 0) > 0)
        if outcome not in {"lkg_served", "failed", OUTCOME_TIMED_OUT}:
            continue
        problem_attempts += 1
        if diagnosis_state != "not_run":
            diagnosis_attempted += 1
        if diagnosis_state == "error":
            diagnosis_errors += 1
        if diagnosis_state == "ok" and diagnosis_classification in diagnosis_counts:
            diagnosis_completed += 1
            diagnosis_counts[str(diagnosis_classification)] += 1
            if diagnosis_domain in diagnosis_domains:
                diagnosis_domains[str(diagnosis_domain)] += 1
    return {
        "candidate_review": {
            "all_parser_attempts": eligible,
            "attempted": review_attempted,
            "completed": review_completed,
            "errors": review_errors,
            "skipped": review_skipped,
            "coverage_of_all_parser_attempts_pct": _percentage(
                review_completed,
                eligible,
            ),
            "valid_response_rate_pct": _percentage(
                review_completed,
                review_completed + review_errors,
            ),
            "verdicts": review_counts,
            "quarantined": quarantined,
        },
        "failure_diagnosis": {
            "all_problem_attempts": problem_attempts,
            "attempted": diagnosis_attempted,
            "completed": diagnosis_completed,
            "errors": diagnosis_errors,
            "coverage_of_all_problem_attempts_pct": _percentage(
                diagnosis_completed,
                problem_attempts,
            ),
            "valid_response_rate_pct": _percentage(
                diagnosis_completed,
                diagnosis_completed + diagnosis_errors,
            ),
            "classifications": diagnosis_counts,
            "failure_domains": diagnosis_domains,
        },
        "calibration": {
            "status": "not_calibrated",
            "human_labeled_examples": 0,
            "precision_pct": None,
            "recall_pct": None,
            "false_positive_rate_pct": None,
            "limitation": "human_labels_not_collected",
        },
    }


def _empty_ai_quality() -> dict[str, Any]:
    return {
        "candidate_review": {
            "all_parser_attempts": 0,
            "attempted": 0,
            "completed": 0,
            "errors": 0,
            "skipped": 0,
            "coverage_of_all_parser_attempts_pct": None,
            "valid_response_rate_pct": None,
            "verdicts": {"pass": 0, "fail": 0, "uncertain": 0},
            "quarantined": 0,
        },
        "failure_diagnosis": {
            "all_problem_attempts": 0,
            "attempted": 0,
            "completed": 0,
            "errors": 0,
            "coverage_of_all_problem_attempts_pct": None,
            "valid_response_rate_pct": None,
            "classifications": {
                "healthy": 0,
                "anomalous": 0,
                "inconclusive": 0,
            },
            "failure_domains": {
                value: 0 for value in AI_DIAGNOSIS_DOMAINS if value != "none"
            },
        },
        "calibration": {
            "status": "not_calibrated",
            "human_labeled_examples": 0,
            "precision_pct": None,
            "recall_pct": None,
            "false_positive_rate_pct": None,
            "limitation": "human_labels_not_collected",
        },
    }


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


def _bounded_refresh_window_id(value: object) -> str:
    """Keep a useful correlation ID without persisting arbitrary secret text."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 160 and all(
        character.isalnum() or character in {":", ".", "_", "-"} for character in raw
    ):
        return raw
    return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


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
            round((bad_attempts / allowed_bad) * 100.0, 2) if allowed_bad > 0 else None
        ),
    }


def _missing_terminal_windows(
    connection: sqlite3.Connection,
    *,
    from_epoch: float,
    to_epoch: float,
) -> int:
    """Count expected source terminals absent from logical refreshes.

    ``observed_sources`` deliberately excludes skipped outcomes, so it cannot
    distinguish a recorded helper/lock skip from a telemetry write gap. Count
    distinct persisted source terminals instead; skipped rows remain visible
    in ``counts.skipped`` but never become false SLO failures. Runs sharing an
    explicit refresh window are one logical refresh, so a targeted recovery can
    fill an earlier missing source without inflating the denominator.
    """

    row = connection.execute(
        """
        WITH window_runs AS (
            SELECT
                run_id,
                CASE
                    WHEN refresh_window_id != ''
                    THEN 'window:' || refresh_window_id
                    ELSE 'run:' || run_id
                END AS logical_refresh_id,
                expected_sources
            FROM refresh_runs
            WHERE finished_at >= ?
              AND finished_at <= ?
        ),
        logical_expectations AS (
            SELECT
                logical_refresh_id,
                MAX(expected_sources) AS expected_sources
            FROM window_runs
            GROUP BY logical_refresh_id
        ),
        logical_terminal_counts AS (
            SELECT
                window_runs.logical_refresh_id,
                COUNT(DISTINCT source_attempts.source_id) AS terminal_sources
            FROM window_runs
            LEFT JOIN source_attempts USING (run_id)
            GROUP BY window_runs.logical_refresh_id
        )
        SELECT COALESCE(
            SUM(
                CASE
                    WHEN logical_expectations.expected_sources
                         > COALESCE(logical_terminal_counts.terminal_sources, 0)
                    THEN logical_expectations.expected_sources
                         - COALESCE(logical_terminal_counts.terminal_sources, 0)
                    ELSE 0
                END
            ),
            0
        )
        FROM logical_expectations
        LEFT JOIN logical_terminal_counts USING (logical_refresh_id)
        """,
        (from_epoch, to_epoch),
    ).fetchone()
    return max(0, int(row[0] if row is not None and row[0] is not None else 0))


def _coverage_ratio(
    connection: sqlite3.Connection,
    *,
    from_at: datetime,
    report_at: datetime,
    duration: timedelta,
    cohort_hash: str | None,
) -> float:
    if cohort_hash is None:
        return 0.0
    expected_buckets = max(
        1,
        math.ceil(duration.total_seconds() / COVERAGE_BUCKET_SECONDS),
    )
    rows = connection.execute(
        """
        SELECT finished_at
        FROM refresh_runs
        WHERE scope = 'full'
          AND cohort_hash = ?
          AND expected_sources > 0
          AND observed_sources >= expected_sources
          AND finished_at >= ?
          AND finished_at <= ?
        """,
        (cohort_hash, from_at.timestamp(), report_at.timestamp()),
    ).fetchall()
    timestamps = sorted({float(row[0]) for row in rows if row and row[0] is not None})
    if not timestamps:
        return 0.0
    window_start = from_at.timestamp()
    window_end = report_at.timestamp()
    covered_buckets = {
        min(
            expected_buckets - 1,
            max(
                0,
                int((timestamp - window_start) // COVERAGE_BUCKET_SECONDS),
            ),
        )
        for timestamp in timestamps
    }
    cadence_misses = 0
    anchors = [window_start, *timestamps, window_end]
    for index in range(1, len(anchors)):
        previous = anchors[index - 1]
        current = anchors[index]
        gap_seconds = max(0.0, current - previous)
        cadence_misses += max(
            0,
            math.ceil(gap_seconds / COVERAGE_MAX_GAP_SECONDS) - 1,
        )
    bucket_ratio = len(covered_buckets) / expected_buckets
    cadence_ratio = max(
        0.0,
        (expected_buckets - min(expected_buckets, cadence_misses)) / expected_buckets,
    )
    return round(min(bucket_ratio, cadence_ratio), 4)


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
