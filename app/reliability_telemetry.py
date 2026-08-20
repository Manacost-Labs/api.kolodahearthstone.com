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
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from .completeness import COMPLETENESS_SCHEMA_VERSION
from .config import source_operationally_enabled
from .source_contracts import HSREPLAY_FRESHNESS_GATED_SOURCE_IDS
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
METHODOLOGY_VERSION = "logical-source-observed-v14"
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
COMPLETENESS_STATES = ("complete", "incomplete", "unknown")
PARSESUNIX_MODES = ("none", "shadow", "active")
PARSESUNIX_VERDICTS = (
    "not_observed",
    "OK",
    "DEAD_URL",
    "ORIGIN_DOWN",
    "RATE_LIMITED",
    "AUTH_REQUIRED",
    "ACCESS_DENIED",
    "BLOCKED",
    "SOFT_BLOCK",
    "THIN_CONTENT",
    "CSR_REQUIRED",
    "NOT_MODIFIED",
    "PROVIDER_ERROR",
    "PARSE_FAIL",
)
# ``None`` means every current operational source participates in completeness
# telemetry. Tests and bounded rollouts may replace it with an explicit subset.
# Sources without source-specific retrieval evidence remain ``unknown``; this
# exposes the gap without manufacturing success.
COMPLETENESS_TRACKED_SOURCE_IDS: frozenset[str] | None = None
# Keep telemetry anchored to the same canonical freshness policy as publish
# validation. A duplicated list can silently overstate strict completeness
# when a newly instrumented HSReplay source is added in only one place.
HSREPLAY_UPSTREAM_FRESHNESS_SOURCE_IDS = HSREPLAY_FRESHNESS_GATED_SOURCE_IDS

_schema_lock = threading.Lock()


def telemetry_db_path() -> Path:
    return root_dir() / "parser-telemetry.sqlite3"


def canonical_scrape_cohort_hash() -> str | None:
    """Hash the current complete generic scrape registry without exposing IDs."""

    from .sources import SOURCES

    source_ids = sorted(
        source.id
        for source in SOURCES
        if source.kind == "scrape" and source_operationally_enabled(source.id)
    )
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


def _confirmed_upstream_publication_pending(status: Mapping[str, object]) -> bool:
    """Return true only for independently discovered, current Vicious absence."""

    if status.get("source_id") != "vicious_syndicate_radars":
        return False
    if status.get("failure_reason_code") != "unavailable":
        return False
    if (
        status.get("upstream_state") != "upstream_publication_pending"
        or status.get("last_refresh_upstream_state")
        != "upstream_publication_pending"
    ):
        return False
    readiness = status.get("last_refresh_upstream_readiness")
    if not isinstance(readiness, Mapping):
        return False
    latest = str(readiness.get("latest_report_issue") or "")
    candidate = str(readiness.get("candidate_issue") or "")
    if not latest.isdigit() or not candidate.isdigit() or int(candidate) >= int(latest):
        return False
    try:
        discovered_at = datetime.fromisoformat(
            str(readiness.get("full_discovery_at") or "").replace("Z", "+00:00")
        )
        refreshed_at = datetime.fromisoformat(
            str(status.get("last_refresh_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if discovered_at.tzinfo is None or refreshed_at.tzinfo is None:
        return False
    discovery_age = refreshed_at - discovered_at
    return timedelta(minutes=-5) <= discovery_age <= timedelta(hours=12)


def classify_terminal_status(status: Mapping[str, object]) -> str:
    """Map a terminal source status to one stable, bounded outcome."""

    state = str(status.get("state") or "").strip().lower()
    if (
        status.get("diagnostic") is True
        or status.get("skipped") is True
        or state in {"diagnostic", "diagnostic_failed", "locked", "skipped"}
    ):
        return "skipped"
    if _confirmed_upstream_publication_pending(status):
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


def _independently_ineligible_reason(status: Mapping[str, object]) -> str:
    if _confirmed_upstream_publication_pending(status):
        return "upstream_not_published"
    return ""


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


def _completeness_terminal_fields(
    source_id: str,
    status: Mapping[str, object],
) -> tuple[int, str, float | None]:
    """Return only rollout-scoped, bounded completeness telemetry."""

    instrumented_source_ids, _ = _completeness_source_catalog()
    if source_id not in instrumented_source_ids:
        return 0, "unknown", None

    quality = status.get("quality")
    if not isinstance(quality, Mapping):
        return 1, "unknown", None
    quality_mapping = quality
    evidence_fields = (
        "completeness_schema_version",
        "retrieval_complete",
        "retrieval_completeness_score",
    )
    if not all(field in quality_mapping for field in evidence_fields):
        return 1, "unknown", None
    if any(
        field in status
        and not _completeness_values_agree(
            field,
            status.get(field),
            quality_mapping.get(field),
        )
        for field in evidence_fields
    ):
        return 1, "unknown", None

    schema_version = _bounded_completeness_schema_version(
        quality_mapping.get("completeness_schema_version")
    )
    if schema_version is None:
        return 1, "unknown", None

    raw_complete = quality_mapping.get("retrieval_complete")
    score = _bounded_completeness_score(
        quality_mapping.get("retrieval_completeness_score")
    )
    if score is None:
        return 1, "unknown", None
    # A deterministic retrieval failure is stronger evidence than missing
    # freshness metadata.  Preserve that known failure instead of diluting it
    # into ``unknown`` when a transport did not expose target headers.
    if raw_complete is False:
        return 1, "incomplete", score
    if source_id in HSREPLAY_UPSTREAM_FRESHNESS_SOURCE_IDS:
        freshness = quality_mapping.get("upstream_freshness")
        if (
            not isinstance(freshness, Mapping)
            or quality_mapping.get("population_completeness") != "unverifiable"
        ):
            return 1, "unknown", score
        freshness_status = freshness.get("status")
        if freshness_status == "stale":
            return 1, "incomplete", score
        if freshness_status != "fresh":
            return 1, "unknown", score
    if raw_complete is True and score == 1.0:
        return 1, "complete", score
    return 1, "unknown", score


def _parsesunix_terminal_fields(
    status: Mapping[str, object],
) -> tuple[
    str,
    str,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
]:
    """Extract bounded rollout evidence without persisting bodies, URLs, or reasons."""

    mode = "none"
    evidence: Mapping[str, object] | None = None
    for candidate_mode, key in (
        ("active", "parsesunix_transport"),
        ("shadow", "parsesunix_shadow"),
        ("active", "last_refresh_parsesunix_transport"),
        ("shadow", "last_refresh_parsesunix_shadow"),
    ):
        candidate = status.get(key)
        if isinstance(candidate, Mapping):
            mode = candidate_mode
            evidence = candidate
            break
    if evidence is None:
        return ("none", "not_observed", None, None, None, None, None, None, None)

    raw_verdict = str(evidence.get("verdict") or "").strip()
    canonical_verdict = raw_verdict.upper()
    verdict = (
        canonical_verdict
        if canonical_verdict in PARSESUNIX_VERDICTS
        else "not_observed"
    )
    paid_requests = _bounded_nonnegative_int(
        evidence.get("paid_requests"), maximum=1_000_000
    )
    paid_cost_microusd = _bounded_exact_microusd(
        evidence.get("paid_cost_usd"),
        certainty=evidence.get("cost_certainty"),
    )
    return (
        mode,
        verdict,
        _bounded_optional_bool(evidence.get("transport_validated")),
        _bounded_optional_bool(evidence.get("candidate_validated")),
        _bounded_optional_bool(evidence.get("publication_validated")),
        _bounded_optional_bool(evidence.get("http_status_match")),
        _bounded_optional_bool(evidence.get("content_hash_match")),
        paid_requests,
        paid_cost_microusd,
    )


def _bounded_optional_bool(value: object) -> int | None:
    return int(value) if isinstance(value, bool) else None


def _bounded_nonnegative_int(value: object, *, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _bounded_exact_microusd(value: object, *, certainty: object) -> int | None:
    if certainty != "exact" or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal(1000000):
        return None
    micros = amount * Decimal(1_000_000)
    if micros != micros.to_integral_value():
        return None
    return int(micros)


def _bounded_completeness_schema_version(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value == COMPLETENESS_SCHEMA_VERSION else None


def _bounded_completeness_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    candidate = float(value)
    return candidate if math.isfinite(candidate) and 0.0 <= candidate <= 1.0 else None


def _completeness_values_agree(
    field: str,
    left: object,
    right: object,
) -> bool:
    if field == "completeness_schema_version":
        left_version = _bounded_completeness_schema_version(left)
        right_version = _bounded_completeness_schema_version(right)
        return left_version is not None and left_version == right_version
    if field == "retrieval_complete":
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if left is None or right is None:
        return left is None and right is None
    left_score = _bounded_completeness_score(left)
    right_score = _bounded_completeness_score(right)
    return left_score is not None and left_score == right_score


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
        independently_ineligible_reason = _independently_ineligible_reason(result)
        ai_fields = _ai_terminal_fields(result)
        completeness_fields = _completeness_terminal_fields(source_id, result)
        parsesunix_fields = _parsesunix_terminal_fields(result)
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
                independently_ineligible_reason,
                *ai_fields,
                *completeness_fields,
                *parsesunix_fields,
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
                    independently_ineligible_reason,
                    ai_review_state,
                    ai_review_verdict,
                    ai_diagnosis_state,
                    ai_diagnosis_classification,
                    ai_diagnosis_domain,
                    ai_quarantined,
                    completeness_tracked,
                    completeness_state,
                    retrieval_completeness_score,
                    parsesunix_mode,
                    parsesunix_verdict,
                    parsesunix_transport_validated,
                    parsesunix_candidate_validated,
                    parsesunix_publication_validated,
                    parsesunix_http_status_match,
                    parsesunix_content_hash_match,
                    parsesunix_paid_requests,
                    parsesunix_paid_cost_microusd,
                    recorded_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
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
        "independently_ineligible_reason",
        "ai_review_state",
        "ai_review_verdict",
        "ai_diagnosis_state",
        "ai_diagnosis_classification",
        "ai_diagnosis_domain",
        "ai_quarantined",
        "completeness_tracked",
        "completeness_state",
        "retrieval_completeness_score",
        "parsesunix_mode",
        "parsesunix_verdict",
        "parsesunix_transport_validated",
        "parsesunix_candidate_validated",
        "parsesunix_publication_validated",
        "parsesunix_http_status_match",
        "parsesunix_content_hash_match",
        "parsesunix_paid_requests",
        "parsesunix_paid_cost_microusd",
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
                    independently_ineligible_reason TEXT NOT NULL DEFAULT '',
                    ai_review_state TEXT NOT NULL DEFAULT 'not_run',
                    ai_review_verdict TEXT NOT NULL DEFAULT 'not_run',
                    ai_diagnosis_state TEXT NOT NULL DEFAULT 'not_run',
                    ai_diagnosis_classification TEXT NOT NULL DEFAULT 'not_run',
                    ai_diagnosis_domain TEXT NOT NULL DEFAULT 'none',
                    ai_quarantined INTEGER NOT NULL DEFAULT 0,
                    completeness_tracked INTEGER NOT NULL DEFAULT 0 CHECK (
                        completeness_tracked IN (0, 1)
                    ),
                    completeness_state TEXT NOT NULL DEFAULT 'unknown' CHECK (
                        completeness_state IN ('complete', 'incomplete', 'unknown')
                    ),
                    retrieval_completeness_score REAL CHECK (
                        retrieval_completeness_score IS NULL OR (
                            retrieval_completeness_score >= 0.0 AND
                            retrieval_completeness_score <= 1.0
                        )
                    ),
                    parsesunix_mode TEXT NOT NULL DEFAULT 'none' CHECK (
                        parsesunix_mode IN ('none', 'shadow', 'active')
                    ),
                    parsesunix_verdict TEXT NOT NULL DEFAULT 'not_observed',
                    parsesunix_transport_validated INTEGER CHECK (
                        parsesunix_transport_validated IS NULL OR
                        parsesunix_transport_validated IN (0, 1)
                    ),
                    parsesunix_candidate_validated INTEGER CHECK (
                        parsesunix_candidate_validated IS NULL OR
                        parsesunix_candidate_validated IN (0, 1)
                    ),
                    parsesunix_publication_validated INTEGER CHECK (
                        parsesunix_publication_validated IS NULL OR
                        parsesunix_publication_validated IN (0, 1)
                    ),
                    parsesunix_http_status_match INTEGER CHECK (
                        parsesunix_http_status_match IS NULL OR
                        parsesunix_http_status_match IN (0, 1)
                    ),
                    parsesunix_content_hash_match INTEGER CHECK (
                        parsesunix_content_hash_match IS NULL OR
                        parsesunix_content_hash_match IN (0, 1)
                    ),
                    parsesunix_paid_requests INTEGER CHECK (
                        parsesunix_paid_requests IS NULL OR
                        parsesunix_paid_requests >= 0
                    ),
                    parsesunix_paid_cost_microusd INTEGER CHECK (
                        parsesunix_paid_cost_microusd IS NULL OR
                        parsesunix_paid_cost_microusd >= 0
                    ),
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
                "independently_ineligible_reason": "TEXT NOT NULL DEFAULT ''",
                "ai_review_state": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_review_verdict": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_diagnosis_state": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_diagnosis_classification": "TEXT NOT NULL DEFAULT 'not_run'",
                "ai_diagnosis_domain": "TEXT NOT NULL DEFAULT 'none'",
                "ai_quarantined": "INTEGER NOT NULL DEFAULT 0",
                "completeness_tracked": (
                    "INTEGER NOT NULL DEFAULT 0 CHECK (completeness_tracked IN (0, 1))"
                ),
                "completeness_state": (
                    "TEXT NOT NULL DEFAULT 'unknown' "
                    "CHECK (completeness_state IN ('complete', 'incomplete', 'unknown'))"
                ),
                "retrieval_completeness_score": (
                    "REAL CHECK (retrieval_completeness_score IS NULL OR "
                    "(retrieval_completeness_score >= 0.0 AND "
                    "retrieval_completeness_score <= 1.0))"
                ),
                "parsesunix_mode": (
                    "TEXT NOT NULL DEFAULT 'none' "
                    "CHECK (parsesunix_mode IN ('none', 'shadow', 'active'))"
                ),
                "parsesunix_verdict": "TEXT NOT NULL DEFAULT 'not_observed'",
                "parsesunix_transport_validated": (
                    "INTEGER CHECK (parsesunix_transport_validated IS NULL OR "
                    "parsesunix_transport_validated IN (0, 1))"
                ),
                "parsesunix_candidate_validated": (
                    "INTEGER CHECK (parsesunix_candidate_validated IS NULL OR "
                    "parsesunix_candidate_validated IN (0, 1))"
                ),
                "parsesunix_publication_validated": (
                    "INTEGER CHECK (parsesunix_publication_validated IS NULL OR "
                    "parsesunix_publication_validated IN (0, 1))"
                ),
                "parsesunix_http_status_match": (
                    "INTEGER CHECK (parsesunix_http_status_match IS NULL OR "
                    "parsesunix_http_status_match IN (0, 1))"
                ),
                "parsesunix_content_hash_match": (
                    "INTEGER CHECK (parsesunix_content_hash_match IS NULL OR "
                    "parsesunix_content_hash_match IN (0, 1))"
                ),
                "parsesunix_paid_requests": (
                    "INTEGER CHECK (parsesunix_paid_requests IS NULL OR "
                    "parsesunix_paid_requests >= 0)"
                ),
                "parsesunix_paid_cost_microusd": (
                    "INTEGER CHECK (parsesunix_paid_cost_microusd IS NULL OR "
                    "parsesunix_paid_cost_microusd >= 0)"
                ),
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


def _query_verified_completeness(
    connection: sqlite3.Connection,
    *,
    from_epoch: float,
    to_epoch: float,
    eligible_parser_attempts: int,
    measurement_complete: bool,
) -> dict[str, Any]:
    instrumented_source_ids, catalog_sources = _completeness_source_catalog()
    source_rows = connection.execute(
        """
        WITH ranked_attempts AS (
            SELECT
                attempt_id,
                source_id,
                outcome,
                completeness_tracked,
                completeness_state,
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
        SELECT
            source_id,
            COUNT(*),
            COALESCE(SUM(
                CASE
                    WHEN completeness_state = 'complete'
                     AND outcome = 'fresh_published'
                    THEN 1 ELSE 0
                END
            ), 0),
            COALESCE(SUM(
                CASE
                    WHEN completeness_state = 'complete'
                    THEN 1 ELSE 0
                END
            ), 0),
            COALESCE(SUM(
                CASE
                    WHEN completeness_state = 'incomplete'
                    THEN 1 ELSE 0
                END
            ), 0),
            COALESCE(SUM(
                CASE
                    WHEN completeness_state NOT IN ('complete', 'incomplete')
                    THEN 1 ELSE 0
                END
            ), 0)
        FROM ranked_attempts
        WHERE logical_rank = 1
          AND outcome != 'skipped'
          AND completeness_tracked = 1
        GROUP BY source_id
        """,
        (from_epoch, to_epoch),
    ).fetchall()
    per_source_counts = {
        str(source_row[0]): tuple(int(value or 0) for value in source_row[1:])
        for source_row in source_rows
        if str(source_row[0]) in instrumented_source_ids
    }
    instrumented_sources = len(instrumented_source_ids)
    observed_instrumented_sources = len(per_source_counts)
    tracked_attempts = sum(counts[0] for counts in per_source_counts.values())
    complete_fresh = sum(counts[1] for counts in per_source_counts.values())
    states = {
        "complete": sum(counts[2] for counts in per_source_counts.values()),
        "incomplete": sum(counts[3] for counts in per_source_counts.values()),
        "unknown": sum(counts[4] for counts in per_source_counts.values()),
    }
    observed_source_rates = [
        Fraction(counts[1], counts[0])
        for counts in per_source_counts.values()
        if counts[0] > 0
    ]
    sources_meeting_target = sum(
        int(_ratio_meets_target(counts[1], counts[0]))
        for counts in per_source_counts.values()
        if counts[0] > 0
    )
    sources_below_target = observed_instrumented_sources - sources_meeting_target
    sources_without_observations = max(
        0,
        instrumented_sources - observed_instrumented_sources,
    )
    source_target_attainment_pct = _percentage(
        sources_meeting_target,
        instrumented_sources,
    )
    macro_complete_fresh_rate_pct = (
        round(
            float(sum(observed_source_rates, start=Fraction()) / instrumented_sources)
            * 100.0,
            2,
        )
        if instrumented_sources > 0
        else None
    )
    worst_observed_source_rate_pct = (
        round(float(min(observed_source_rates)) * 100.0, 2)
        if observed_source_rates
        else None
    )
    macro_target_met = _macro_source_ratio_meets_target(
        per_source_counts,
        instrumented_sources=instrumented_sources,
    )
    source_catalog_coverage_pct = _percentage(
        instrumented_sources,
        catalog_sources,
    )
    instrumented_source_observation_coverage_pct = _percentage(
        observed_instrumented_sources,
        instrumented_sources,
    )
    coverage_pct = _percentage(tracked_attempts, eligible_parser_attempts)
    complete_fresh_rate_pct = _percentage(complete_fresh, tracked_attempts)
    coverage_gates_met = (
        _ratio_meets_target(instrumented_sources, catalog_sources),
        _ratio_meets_target(
            observed_instrumented_sources,
            instrumented_sources,
        ),
        _ratio_meets_target(tracked_attempts, eligible_parser_attempts),
    )
    source_target_gate_met = _ratio_meets_target(
        sources_meeting_target,
        instrumented_sources,
    )
    if (
        not measurement_complete
        or complete_fresh_rate_pct is None
        or not all(coverage_gates_met)
    ):
        objective_status = "collecting"
    elif (
        _ratio_meets_target(
            complete_fresh,
            tracked_attempts,
        )
        and source_target_gate_met
        and macro_target_met
    ):
        objective_status = "met"
    else:
        objective_status = "miss"
    return {
        "instrumented_sources": instrumented_sources,
        "catalog_sources": catalog_sources,
        "source_catalog_coverage_pct": source_catalog_coverage_pct,
        "observed_instrumented_sources": observed_instrumented_sources,
        "instrumented_source_observation_coverage_pct": (
            instrumented_source_observation_coverage_pct
        ),
        "sources_meeting_target": sources_meeting_target,
        "sources_below_target": sources_below_target,
        "sources_without_observations": sources_without_observations,
        "source_target_attainment_pct": source_target_attainment_pct,
        "macro_complete_fresh_rate_pct": macro_complete_fresh_rate_pct,
        "macro_target_met": macro_target_met,
        "worst_observed_source_rate_pct": worst_observed_source_rate_pct,
        "tracked_attempts": tracked_attempts,
        "complete_fresh": complete_fresh,
        "states": states,
        "coverage_of_all_parser_attempts_pct": coverage_pct,
        "complete_fresh_rate_pct": complete_fresh_rate_pct,
        "target_rate_pct": SLO_TARGET_RATE_PCT,
        "objective_status": objective_status,
    }


def _empty_verified_completeness() -> dict[str, Any]:
    instrumented_source_ids, catalog_sources = _completeness_source_catalog()
    instrumented_sources = len(instrumented_source_ids)
    return {
        "instrumented_sources": instrumented_sources,
        "catalog_sources": catalog_sources,
        "source_catalog_coverage_pct": _percentage(
            instrumented_sources,
            catalog_sources,
        ),
        "observed_instrumented_sources": 0,
        "instrumented_source_observation_coverage_pct": _percentage(
            0,
            instrumented_sources,
        ),
        "sources_meeting_target": 0,
        "sources_below_target": 0,
        "sources_without_observations": instrumented_sources,
        "source_target_attainment_pct": _percentage(0, instrumented_sources),
        "macro_complete_fresh_rate_pct": _percentage(0, instrumented_sources),
        "macro_target_met": False,
        "worst_observed_source_rate_pct": None,
        "tracked_attempts": 0,
        "complete_fresh": 0,
        "states": {state: 0 for state in COMPLETENESS_STATES},
        "coverage_of_all_parser_attempts_pct": None,
        "complete_fresh_rate_pct": None,
        "target_rate_pct": SLO_TARGET_RATE_PCT,
        "objective_status": "collecting",
    }


def _completeness_source_catalog() -> tuple[frozenset[str], int]:
    """Return current catalog instrumentation without inflating removed sources."""

    from .sources import SOURCES

    catalog_source_ids = frozenset(
        source.id for source in SOURCES if source_operationally_enabled(source.id)
    )
    tracked_source_ids = COMPLETENESS_TRACKED_SOURCE_IDS
    instrumented_source_ids = (
        catalog_source_ids
        if tracked_source_ids is None
        else catalog_source_ids & tracked_source_ids
    )
    return instrumented_source_ids, len(catalog_source_ids)


def _query_parsesunix_rollout(
    connection: sqlite3.Connection,
    *,
    from_epoch: float,
    to_epoch: float,
) -> dict[str, Any]:
    row = connection.execute(
        """
        WITH ranked_attempts AS (
            SELECT
                attempt_id,
                source_id,
                outcome,
                parsesunix_mode,
                parsesunix_transport_validated,
                parsesunix_candidate_validated,
                parsesunix_publication_validated,
                parsesunix_http_status_match,
                parsesunix_content_hash_match,
                parsesunix_paid_requests,
                parsesunix_paid_cost_microusd,
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
        SELECT
            COUNT(*),
            COUNT(DISTINCT source_id),
            COALESCE(SUM(CASE WHEN parsesunix_mode = 'shadow' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN parsesunix_mode = 'active' THEN 1 ELSE 0 END), 0),
            COUNT(parsesunix_transport_validated),
            COALESCE(SUM(parsesunix_transport_validated), 0),
            COUNT(parsesunix_candidate_validated),
            COALESCE(SUM(parsesunix_candidate_validated), 0),
            COUNT(parsesunix_publication_validated),
            COALESCE(SUM(parsesunix_publication_validated), 0),
            COUNT(parsesunix_http_status_match),
            COALESCE(SUM(parsesunix_http_status_match), 0),
            COUNT(parsesunix_content_hash_match),
            COALESCE(SUM(parsesunix_content_hash_match), 0),
            COUNT(parsesunix_paid_requests),
            COALESCE(SUM(parsesunix_paid_requests), 0),
            COUNT(parsesunix_paid_cost_microusd),
            COALESCE(SUM(parsesunix_paid_cost_microusd), 0)
        FROM ranked_attempts
        WHERE logical_rank = 1
          AND parsesunix_mode != 'none'
        """,
        (from_epoch, to_epoch),
    ).fetchone()
    values = tuple(int(value or 0) for value in (row or (0,) * 18))
    (
        observed_attempts,
        observed_sources,
        shadow_attempts,
        active_attempts,
        transport_checked,
        transport_validated,
        candidate_checked,
        candidate_validated,
        publication_checked,
        publication_validated,
        http_status_compared,
        http_status_matches,
        content_hash_compared,
        content_hash_matches,
        paid_requests_known_attempts,
        paid_requests_total,
        paid_cost_known_attempts,
        paid_cost_microusd,
    ) = values
    return {
        "observed_attempts": observed_attempts,
        "observed_sources": observed_sources,
        "shadow_attempts": shadow_attempts,
        "active_attempts": active_attempts,
        "transport_checked": transport_checked,
        "transport_validated": transport_validated,
        "transport_validated_rate_pct": _percentage(
            transport_validated,
            transport_checked,
        ),
        "candidate_checked": candidate_checked,
        "candidate_validated": candidate_validated,
        "candidate_validated_rate_pct": _percentage(
            candidate_validated,
            candidate_checked,
        ),
        "publication_checked": publication_checked,
        "publication_validated": publication_validated,
        "publication_validated_rate_pct": _percentage(
            publication_validated,
            publication_checked,
        ),
        "http_status_compared": http_status_compared,
        "http_status_matches": http_status_matches,
        "http_status_match_rate_pct": _percentage(
            http_status_matches,
            http_status_compared,
        ),
        "content_hash_compared": content_hash_compared,
        "content_hash_matches": content_hash_matches,
        "content_hash_match_rate_pct": _percentage(
            content_hash_matches,
            content_hash_compared,
        ),
        "paid_requests_known_attempts": paid_requests_known_attempts,
        "paid_requests": (
            paid_requests_total
            if paid_requests_known_attempts == observed_attempts
            else None
        ),
        "paid_cost_known_attempts": paid_cost_known_attempts,
        "paid_cost_usd": (
            f"{Decimal(paid_cost_microusd) / Decimal(1_000_000):.6f}"
            if paid_cost_known_attempts == observed_attempts
            else None
        ),
    }


def _empty_parsesunix_rollout() -> dict[str, Any]:
    return {
        "observed_attempts": 0,
        "observed_sources": 0,
        "shadow_attempts": 0,
        "active_attempts": 0,
        "transport_checked": 0,
        "transport_validated": 0,
        "transport_validated_rate_pct": None,
        "candidate_checked": 0,
        "candidate_validated": 0,
        "candidate_validated_rate_pct": None,
        "publication_checked": 0,
        "publication_validated": 0,
        "publication_validated_rate_pct": None,
        "http_status_compared": 0,
        "http_status_matches": 0,
        "http_status_match_rate_pct": None,
        "content_hash_compared": 0,
        "content_hash_matches": 0,
        "content_hash_match_rate_pct": None,
        "paid_requests_known_attempts": 0,
        "paid_requests": 0,
        "paid_cost_known_attempts": 0,
        "paid_cost_usd": "0.000000",
    }


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
                independently_ineligible_reason,
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
        SELECT outcome, independently_ineligible_reason, COUNT(*)
        FROM ranked_attempts
        WHERE logical_rank = 1
        GROUP BY outcome, independently_ineligible_reason
        """,
        (from_at.timestamp(), report_at.timestamp()),
    ).fetchall()
    counts = {outcome: 0 for outcome in OUTCOMES}
    upstream_pending_attempts = 0
    for outcome, ineligible_reason, count in rows:
        if outcome in counts:
            counts[str(outcome)] += int(count)
        if ineligible_reason == "upstream_not_published":
            upstream_pending_attempts += int(count)

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
    end_to_end_attempts = eligible_attempts + upstream_pending_attempts
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
    verified_completeness = _query_verified_completeness(
        connection,
        from_epoch=from_at.timestamp(),
        to_epoch=report_at.timestamp(),
        eligible_parser_attempts=eligible_attempts,
        measurement_complete=measurement_complete,
    )
    parsesunix_rollout = _query_parsesunix_rollout(
        connection,
        from_epoch=from_at.timestamp(),
        to_epoch=report_at.timestamp(),
    )
    scheduled_reliability = _query_scheduled_reliability(
        connection,
        from_at=from_at,
        report_at=report_at,
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
        "upstream_pending_attempts": upstream_pending_attempts,
        "end_to_end_attempts": end_to_end_attempts,
        "counts": counts,
        "failure_reasons": failure_reasons,
        "full_fresh_rate_pct": _percentage(full_fresh, eligible_attempts),
        "end_to_end_fresh_rate_pct": _percentage(full_fresh, end_to_end_attempts),
        "accepted_fresh_rate_pct": _percentage(accepted_fresh, eligible_attempts),
        "data_available_rate_pct": _percentage(data_available, eligible_attempts),
        "freshness_slo": _slo_budget(
            good_attempts=full_fresh,
            eligible_attempts=eligible_attempts,
            measurement_complete=measurement_complete,
        ),
        "end_to_end_freshness_slo": _slo_budget(
            good_attempts=full_fresh,
            eligible_attempts=end_to_end_attempts,
            measurement_complete=measurement_complete,
        ),
        "availability_slo": _slo_budget(
            good_attempts=data_available,
            eligible_attempts=eligible_attempts,
            measurement_complete=measurement_complete,
        ),
        "verified_completeness": verified_completeness,
        "parsesunix_rollout": parsesunix_rollout,
        "ai_quality": ai_quality,
        "scheduled_reliability": scheduled_reliability,
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
                "upstream_pending_attempts": 0,
                "end_to_end_attempts": 0,
                "counts": {outcome: 0 for outcome in OUTCOMES},
                "failure_reasons": {reason: 0 for reason in FAILURE_REASONS},
                "full_fresh_rate_pct": None,
                "end_to_end_fresh_rate_pct": None,
                "accepted_fresh_rate_pct": None,
                "data_available_rate_pct": None,
                "freshness_slo": _slo_budget(
                    good_attempts=0,
                    eligible_attempts=0,
                    measurement_complete=False,
                ),
                "end_to_end_freshness_slo": _slo_budget(
                    good_attempts=0,
                    eligible_attempts=0,
                    measurement_complete=False,
                ),
                "availability_slo": _slo_budget(
                    good_attempts=0,
                    eligible_attempts=0,
                    measurement_complete=False,
                ),
                "verified_completeness": _empty_verified_completeness(),
                "parsesunix_rollout": _empty_parsesunix_rollout(),
                "ai_quality": _empty_ai_quality(),
                "scheduled_reliability": _empty_scheduled_reliability(),
            }
            for label, duration in WINDOWS
        ],
    }


def _empty_scheduled_reliability() -> dict[str, Any]:
    return {
        "ledger_status": "absent",
        "measurement_status": "collecting",
        "schedule_coverage_ratio": 0.0,
        "temporal_coverage_ratio": 0.0,
        "coverage_started_at": None,
        "materialized_through": None,
        "tracked_schedules": 0,
        "catalog_schedules": 0,
        "expected_slots": 0,
        "eligible_slots": 0,
        "excluded_slots": 0,
        "pending_slots": 0,
        "due_slots": 0,
        "on_time_fresh": 0,
        "on_time_upstream_pending": 0,
        "on_time_nonfresh": 0,
        "late": 0,
        "missing": 0,
        "on_time_fresh_rate_pct": None,
        "parser_eligible_due_slots": 0,
        "parser_on_time_fresh_rate_pct": None,
        "target_rate_pct": SLO_TARGET_RATE_PCT,
        "objective_status": "collecting",
        "parser_objective_status": "collecting",
    }


def _query_scheduled_reliability(
    connection: sqlite3.Connection,
    *,
    from_at: datetime,
    report_at: datetime,
) -> dict[str, Any]:
    required_windows = {
        "occurrence_id",
        "schedule_id",
        "scheduled_for",
        "deadline_at",
        "source_id",
        "cohort_hash",
        "eligible",
    }
    required_state = {
        "coverage_started_at",
        "materialized_through",
        "cohort_hash",
        "tracked_schedule_count",
        "catalog_schedule_count",
    }
    try:
        window_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(schedule_source_windows)")
        }
        state_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(schedule_ledger_state)")
        }
    except sqlite3.DatabaseError:
        return _empty_scheduled_reliability()
    if not required_windows.issubset(window_columns) or not required_state.issubset(
        state_columns
    ):
        return _empty_scheduled_reliability()

    state = connection.execute(
        """
        SELECT
            coverage_started_at,
            materialized_through,
            cohort_hash,
            tracked_schedule_count,
            catalog_schedule_count
        FROM schedule_ledger_state
        WHERE singleton = 1
        """
    ).fetchone()
    if state is None:
        return _empty_scheduled_reliability()

    try:
        coverage_started_at = float(state[0])
        materialized_through = float(state[1])
        cohort_hash = str(state[2])
        tracked_schedules = int(state[3])
        catalog_schedules = int(state[4])
    except (TypeError, ValueError, OverflowError):
        return _empty_scheduled_reliability()
    if (
        not math.isfinite(coverage_started_at)
        or not math.isfinite(materialized_through)
        or materialized_through < coverage_started_at
        or len(cohort_hash) != 64
        or any(character not in "0123456789abcdef" for character in cohort_hash)
        or tracked_schedules <= 0
        or catalog_schedules <= 0
        or tracked_schedules > catalog_schedules
    ):
        return _empty_scheduled_reliability()
    from_epoch = from_at.timestamp()
    report_epoch = report_at.timestamp()
    duration_seconds = max(1.0, report_epoch - from_epoch)
    temporal_seconds = max(
        0.0,
        min(report_epoch, materialized_through) - max(from_epoch, coverage_started_at),
    )
    temporal_coverage_ratio = round(
        min(1.0, temporal_seconds / duration_seconds),
        4,
    )
    schedule_coverage_ratio = (
        round(
            min(1.0, tracked_schedules / catalog_schedules),
            4,
        )
        if catalog_schedules > 0
        else 0.0
    )

    counts_row = connection.execute(
        """
        WITH relevant_slots AS (
            SELECT *
            FROM schedule_source_windows
            WHERE cohort_hash = ?
              AND deadline_at >= ?
              AND scheduled_for <= ?
        ),
        attempt_flags AS (
            SELECT
                slots.occurrence_id,
                slots.source_id,
                MAX(CASE
                    WHEN attempts.outcome = 'fresh_published'
                     AND attempts.finished_at <= slots.deadline_at
                    THEN 1 ELSE 0
                END) AS on_time_fresh,
                MAX(CASE
                    WHEN attempts.independently_ineligible_reason =
                         'upstream_not_published'
                     AND attempts.finished_at <= slots.deadline_at
                    THEN 1 ELSE 0
                END) AS on_time_upstream_pending,
                MAX(CASE
                    WHEN attempts.outcome != 'skipped'
                     AND attempts.finished_at <= slots.deadline_at
                    THEN 1 ELSE 0
                END) AS on_time_terminal,
                MAX(CASE
                    WHEN (
                        attempts.outcome != 'skipped'
                        OR attempts.independently_ineligible_reason =
                           'upstream_not_published'
                    )
                     AND attempts.finished_at > slots.deadline_at
                    THEN 1 ELSE 0
                END) AS late_terminal
            FROM relevant_slots AS slots
            LEFT JOIN source_attempts AS attempts
              ON attempts.refresh_window_id = slots.occurrence_id
             AND attempts.source_id = slots.source_id
             AND attempts.finished_at <= ?
            GROUP BY slots.occurrence_id, slots.source_id
        )
        SELECT
            COUNT(*),
            COALESCE(SUM(CASE WHEN slots.eligible = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN slots.eligible = 0 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at > ? THEN 1 ELSE 0
            END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at <= ? THEN 1 ELSE 0
            END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at <= ?
                 AND flags.on_time_fresh = 1 THEN 1 ELSE 0
            END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at <= ?
                 AND flags.on_time_fresh = 0
                 AND flags.on_time_upstream_pending = 1 THEN 1 ELSE 0
            END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at <= ?
                 AND flags.on_time_fresh = 0
                 AND flags.on_time_upstream_pending = 0
                 AND flags.on_time_terminal = 1
                THEN 1 ELSE 0
            END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at <= ?
                 AND flags.on_time_fresh = 0 AND flags.on_time_terminal = 0
                 AND flags.on_time_upstream_pending = 0
                 AND flags.late_terminal = 1 THEN 1 ELSE 0
            END), 0),
            COALESCE(SUM(CASE
                WHEN slots.eligible = 1 AND slots.deadline_at <= ?
                 AND flags.on_time_fresh = 0 AND flags.on_time_terminal = 0
                 AND flags.on_time_upstream_pending = 0
                 AND flags.late_terminal = 0 THEN 1 ELSE 0
            END), 0)
        FROM relevant_slots AS slots
        JOIN attempt_flags AS flags USING (occurrence_id, source_id)
        """,
        (
            cohort_hash,
            from_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
            report_epoch,
        ),
    ).fetchone()
    values = [int(value or 0) for value in (counts_row or (0,) * 10)]
    (
        expected_slots,
        eligible_slots,
        excluded_slots,
        pending_slots,
        due_slots,
        on_time_fresh,
        on_time_upstream_pending,
        on_time_nonfresh,
        late,
        missing,
    ) = values
    parser_eligible_due_slots = max(0, due_slots - on_time_upstream_pending)
    covered = schedule_coverage_ratio >= 1.0 and temporal_coverage_ratio >= 1.0
    measurement_status = "observed" if covered else "collecting"
    objective_status = "collecting"
    if measurement_status == "observed" and due_slots > 0:
        objective_status = (
            "meeting" if _ratio_meets_target(on_time_fresh, due_slots) else "breached"
        )
    parser_objective_status = "collecting"
    if measurement_status == "observed" and parser_eligible_due_slots > 0:
        parser_objective_status = (
            "meeting"
            if _ratio_meets_target(on_time_fresh, parser_eligible_due_slots)
            else "breached"
        )
    return {
        "ledger_status": "covered" if covered else "partial",
        "measurement_status": measurement_status,
        "schedule_coverage_ratio": schedule_coverage_ratio,
        "temporal_coverage_ratio": temporal_coverage_ratio,
        "coverage_started_at": _iso_from_epoch(coverage_started_at),
        "materialized_through": _iso_from_epoch(materialized_through),
        "tracked_schedules": tracked_schedules,
        "catalog_schedules": catalog_schedules,
        "expected_slots": expected_slots,
        "eligible_slots": eligible_slots,
        "excluded_slots": excluded_slots,
        "pending_slots": pending_slots,
        "due_slots": due_slots,
        "on_time_fresh": on_time_fresh,
        "on_time_upstream_pending": on_time_upstream_pending,
        "on_time_nonfresh": on_time_nonfresh,
        "late": late,
        "missing": missing,
        "on_time_fresh_rate_pct": _percentage(on_time_fresh, due_slots),
        "parser_eligible_due_slots": parser_eligible_due_slots,
        "parser_on_time_fresh_rate_pct": _percentage(
            on_time_fresh,
            parser_eligible_due_slots,
        ),
        "target_rate_pct": SLO_TARGET_RATE_PCT,
        "objective_status": objective_status,
        "parser_objective_status": parser_objective_status,
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
            "parsesunix_rollout_rates_cover_observed_instrumented_attempts_only",
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
        "independently_ineligible_method": (
            "verified_upstream_absence_is_excluded_from_parser_slo_but_included_"
            "in_end_to_end_freshness"
        ),
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


def _macro_source_ratio_meets_target(
    per_source_counts: Mapping[str, tuple[int, ...]],
    *,
    instrumented_sources: int,
    target_pct: float = SLO_TARGET_RATE_PCT,
) -> bool:
    """Compare the unweighted per-source mean without display rounding."""

    if instrumented_sources <= 0:
        return False
    ratio_sum = sum(
        (
            Fraction(counts[1], counts[0])
            for counts in per_source_counts.values()
            if len(counts) >= 2 and counts[0] > 0
        ),
        start=Fraction(),
    )
    macro_ratio = ratio_sum / instrumented_sources
    return macro_ratio >= Fraction(str(target_pct)) / 100


def _ratio_meets_target(
    numerator: int,
    denominator: int,
    *,
    target_pct: float = SLO_TARGET_RATE_PCT,
) -> bool:
    """Compare exact counts; rounded percentages are presentation only."""

    return denominator > 0 and Fraction(numerator, denominator) >= (
        Fraction(str(target_pct)) / 100
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()
