from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import random
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .ai_review_evidence import PreparedAIReviewEvidence

from .api_only_sources import blocks_browser_fallback
from .config import (
    ai_review_candidate_max_concurrency,
    ai_review_diagnose_failures_enabled,
    ai_review_diagnosis_max_concurrency,
    ai_review_enabled,
    ai_review_max_failures_per_refresh,
    ai_review_max_per_refresh,
    ai_review_mode,
    ai_review_post_refresh_timeout_seconds,
    ai_review_source_ids,
    fetch_backends,
    fetch_direct_enabled,
    fetch_proxy_url,
    firecrawl_fallback_max_attempts_per_refresh,
    firecrawl_fallback_max_attempts_per_source,
    firecrawl_fallback_source_ids,
    firecrawl_primary_source_ids,
    flaresolverr_session_per_source,
    http_retry_attempts,
    parsesunix_mode_for_source,
    proxy_check_url,
    refresh_delay_browser_only,
    refresh_parallel_light,
    refresh_parallel_medium,
    refresh_parallel_stagger_max,
    refresh_parallel_stagger_min,
    request_delay_seconds,
    request_timeout_seconds,
    source_operationally_enabled,
    user_agent,
)
from .dataset_regression import check_dataset_regression, estimate_metric_count
from .fetch_routes import source_can_run_without_residential_proxy
from .parser import parse_html
from .parsesunix_transport import (
    ParsesUnixExecutionError,
    ParsesUnixIntegrationError,
    ParsesUnixTransportRejected,
    TransportEvidence,
)
from .parsesunix_transport import (
    fetch_direct as fetch_with_parsesunix,
)
from .post_patch_policy import (
    EARLY_SOURCE_IDS,
    POST_PATCH_BASELINE_LABEL,
    STABLE_PUBLICATION_BASELINE_LABEL,
    build_provisional_metadata,
    capture_publication_policy,
    early_policy_changed_since_capture,
)
from .proxy_errors import ProxyPaymentRequiredError
from .publish_gate import (
    validate_candidate_for_publish,
    validate_existing_publication_for_serving,
)
from .refresh_log import (
    activate_source_trace,
    complete_source_trace,
    deactivate_source_trace,
    log_action,
    new_run_id,
    runtime_version_info,
    set_refresh_context,
)
from .resource_locks import ResourceLocked, ResourceLockSet
from .scrapers.browser_pool import PatchrightPool
from .scrapers.flaresolverr import (
    set_active_flaresolverr_session,
    set_flaresolverr_source,
)
from .scrapers.flaresolverr_session import FlareSolverrSession
from .scrapers.http_resilience import is_session_blocked, resilient_http_get
from .scrapers.proxy import burn_proxy_session, proxy_url_for_source
from .scrapers.quality import is_cloudflare_challenge, quality_metrics
from .scrapers.rotator import fetch_html, record_residential_proxy_failure
from .source_state import EFFECTIVE_OK_CACHED, FAILURE_STATES, SourceState
from .source_tiers import (
    API_FIRST_SOURCE_IDS,
    API_FIRST_TIERS,
    SourceTier,
    partition_sources,
    tier_for,
    validate_tier_registry,
)
from .sources import SOURCE_BY_ID, SOURCES, Source
from .storage import (
    load_dataset,
    load_status,
    save_baseline,
    save_baseline_once,
    save_dataset,
    save_status,
)
from .telegram_alerts import mark_alert_sent, should_send_alert

logger = logging.getLogger(__name__)
_firecrawl_fallback_attempts = 0
# Fairness cap: один сбойный источник не должен съедать весь глобальный
# бюджет fallback-попыток (он же бюджет Firecrawl-кредитов) за refresh.
_firecrawl_fallback_attempts_by_source: dict[str, int] = {}
_standard_publication_attempt: ContextVar[Any | None] = ContextVar(
    "standard_publication_attempt", default=None
)


@dataclass(frozen=True)
class _DeferredAIJob:
    source_id: str
    review_kind: str
    execute: Callable[[], Awaitable[Any]]
    affected_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _GenericHtmlFetch:
    body: str
    http_status: int
    final_url: str
    backend: str
    page_snapshot: Any | None
    parsesunix_mode: str
    parsesunix_observation: dict[str, object] | None = None


_deferred_ai_jobs: ContextVar[list[_DeferredAIJob] | None] = ContextVar(
    "deferred_ai_jobs",
    default=None,
)


class _RefreshProxyCircuit:
    """Refresh-scoped state shared by parallel and serial source phases."""

    def __init__(self) -> None:
        self.error: ProxyPaymentRequiredError | None = None

    @property
    def is_open(self) -> bool:
        return self.error is not None

    def open(self, error: ProxyPaymentRequiredError) -> None:
        if self.error is None:
            self.error = error

    def open_from_status(self, status: dict[str, Any]) -> None:
        if not _status_reports_proxy_failure(status):
            return
        raw_status = status.get("proxy_status") or status.get(
            "last_refresh_proxy_status"
        )
        if isinstance(raw_status, int):
            proxy_status = raw_status
        elif isinstance(raw_status, str):
            try:
                proxy_status = int(raw_status)
            except ValueError:
                proxy_status = 407
        else:
            proxy_status = 407
        if proxy_status not in {402, 407}:
            proxy_status = 407
        self.open(
            ProxyPaymentRequiredError(
                f"Residential proxy CONNECT tunnel is unavailable (HTTP {proxy_status})",
                status_code=proxy_status,
            )
        )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _best_effort_log_action(action: str, **kwargs: Any) -> None:
    """Keep post-commit telemetry from changing a durable fetch outcome."""

    try:
        log_action(action, **kwargs)
    except Exception as exc:
        logger.debug(
            "Refresh telemetry failed after commit for %s: %s",
            action,
            exc,
        )


def _begin_deferred_ai_collection() -> None:
    _deferred_ai_jobs.set([])


def _enqueue_deferred_ai_job(job: _DeferredAIJob) -> bool | None:
    jobs = _deferred_ai_jobs.get()
    if jobs is None:
        return None
    kind_limit = (
        ai_review_max_failures_per_refresh()
        if job.review_kind == "failure_diagnosis"
        else ai_review_max_per_refresh()
    )
    if sum(existing.review_kind == job.review_kind for existing in jobs) >= kind_limit:
        return False
    jobs.append(job)
    return True


def _update_reliability_ai_best_effort(
    run_id: str,
    results: list[dict[str, Any]],
) -> None:
    try:
        from .reliability_telemetry import update_terminal_ai_results

        update_terminal_ai_results(run_id, results)
    except Exception as exc:  # noqa: BLE001 - advisory telemetry is fail-open
        logger.warning(
            "Reliability AI telemetry update failed for run %s: %s",
            run_id,
            type(exc).__name__,
        )


def _terminal_ai_reason_code(status: dict[str, Any]) -> str:
    state = str(status.get("last_refresh_state") or status.get("state") or "")
    failure_class = str(
        status.get("last_refresh_failure_class")
        or status.get("failure_class")
        or ""
    ).casefold()
    raw_http_status = status.get("last_refresh_http_status") or status.get(
        "http_status"
    )
    try:
        http_status = int(raw_http_status)
    except (TypeError, ValueError):
        http_status = 0
    if state == SourceState.TIMED_OUT:
        return "timeout"
    if http_status == 401 or "auth" in failure_class:
        return "login_wall"
    if failure_class.startswith("proxy_"):
        return "provider_exhausted"
    if state == SourceState.BLOCKED_BY_PROTECTION:
        return "challenge_page"
    if 400 <= http_status <= 499:
        return "http_4xx"
    if 500 <= http_status <= 599:
        return "http_5xx"
    if state == SourceState.PROXY_REQUIRED:
        return "provider_exhausted"
    if state == SourceState.QUALITY_ERROR:
        return "semantic_failure"
    if state == SourceState.FETCH_ERROR:
        return "transport_error"
    return "unknown"


def _is_verified_vicious_pending_temporal_lkg(status: dict[str, Any]) -> bool:
    """Recognize the exact deterministic state that needs no repeated AI audit."""

    if not (
        status.get("source_id") == "vicious_syndicate_radars"
        and status.get("state") == SourceState.OK
        and status.get("effective_state") == EFFECTIVE_OK_CACHED
        and status.get("serving_cached_dataset") is True
        and status.get("cached_after_failure") is True
        and status.get("fresh_candidate_published") is False
        and status.get("cached_content_temporally_grandfathered") is True
        and status.get("last_refresh_state") == SourceState.QUALITY_ERROR
        and status.get("failure_reason_code") == "unavailable"
        and status.get("upstream_state") == "upstream_publication_pending"
        and status.get("last_refresh_upstream_state")
        == "upstream_publication_pending"
    ):
        return False

    from .vicious_syndicate import verified_upstream_pending_readiness

    return bool(
        verified_upstream_pending_readiness(
            status.get("last_refresh_upstream_readiness")
        )
    )


async def _enqueue_terminal_failure_ai_jobs(
    statuses: list[dict[str, Any]],
) -> None:
    if not ai_review_enabled() or not ai_review_diagnose_failures_enabled():
        return
    jobs = _deferred_ai_jobs.get()
    if jobs is None:
        return
    already_scheduled = {
        job.source_id
        for job in jobs
        if job.review_kind == "failure_diagnosis"
    }
    for status in statuses:
        source_id = str(status.get("source_id") or "")
        review = status.get("ai_review") or status.get("latest_ai_review")
        if (
            not source_id
            or source_id in already_scheduled
            or status.get("skipped") is True
            or (isinstance(review, dict) and review.get("quarantine") is True)
        ):
            continue
        if _is_verified_vicious_pending_temporal_lkg(status):
            _best_effort_log_action(
                "ai.diagnosis.skipped",
                source_id=source_id,
                level="info",
                extra={"reason": "verified_upstream_publication_pending"},
            )
            continue
        state = str(status.get("state") or "")
        failed_live_refresh = state in FAILURE_STATES or bool(
            status.get("serving_cached_dataset")
            and status.get("last_refresh_state") not in (None, SourceState.OK)
        )
        if not failed_live_refresh:
            continue
        source = SOURCE_BY_ID.get(source_id)
        if source is None:
            continue
        reason_code = _terminal_ai_reason_code(status)
        quality = status.get("quality")
        await _diagnose_candidate_with_ai(
            source,
            {},
            backend=str(
                status.get("last_refresh_transport_backend")
                or status.get("last_refresh_backend")
                or status.get("backend")
                or "unknown"
            ),
            stage="fetch",
            deterministic_reason=reason_code,
            deterministic_extra={"reason_code": reason_code},
            quality=quality if isinstance(quality, dict) else None,
        )
        already_scheduled.add(source_id)


async def _flush_deferred_ai_jobs(
    run_id: str,
    statuses: list[dict[str, Any]],
    *,
    enqueue_terminal_failures: bool = True,
    persist_statuses: bool = True,
) -> None:
    if enqueue_terminal_failures:
        await _enqueue_terminal_failure_ai_jobs(statuses)
    jobs = list(_deferred_ai_jobs.get() or [])
    _deferred_ai_jobs.set(None)
    if not jobs:
        return

    lane_semaphores = {
        "candidate": asyncio.Semaphore(ai_review_candidate_max_concurrency()),
        "failure_diagnosis": asyncio.Semaphore(
            ai_review_diagnosis_max_concurrency()
        ),
    }

    async def execute_job(job: _DeferredAIJob) -> Any:
        async with lane_semaphores[job.review_kind]:
            return await job.execute()

    tasks = {asyncio.create_task(execute_job(job)): job for job in jobs}
    done, pending = await asyncio.wait(
        tasks,
        timeout=ai_review_post_refresh_timeout_seconds(),
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    statuses_by_source = {
        str(status.get("source_id") or ""): status for status in statuses
    }
    attached = 0
    for task in done:
        job = tasks[task]
        try:
            result = task.result()
        except Exception as exc:  # noqa: BLE001 - advisory job is fail-open
            _best_effort_log_action(
                "ai.audit.error",
                source_id=job.source_id,
                level="warn",
                error_type=type(exc).__name__,
            )
            continue
        telemetry = (
            result[0]
            if job.review_kind == "candidate"
            and isinstance(result, tuple)
            and result
            else result
            if job.review_kind == "failure_diagnosis" and isinstance(result, dict)
            else None
        )
        target_ids = job.affected_source_ids or (job.source_id,)
        for target_id in target_ids:
            status = statuses_by_source.get(target_id)
            if status is None:
                continue
            if job.review_kind == "candidate":
                _attach_ai_review_status(status, telemetry)
            else:
                _attach_ai_diagnosis_status(status, telemetry)
            if telemetry:
                attached += 1
                if not persist_statuses:
                    continue
                try:
                    save_status(target_id, status)
                except Exception as exc:  # noqa: BLE001 - outcome already durable
                    logger.debug(
                        "Deferred AI status save failed for %s: %s",
                        target_id,
                        type(exc).__name__,
                    )

    if attached:
        _update_reliability_ai_best_effort(run_id, statuses)
    _best_effort_log_action(
        "ai.audit.complete",
        level="warn" if pending else "info",
        extra={
            "run_id": run_id,
            "scheduled": len(jobs),
            "completed": len(done),
            "timed_out": len(pending),
            "attached": attached,
        },
    )


async def _review_candidate_with_ai(
    source: Source,
    parsed: dict[str, Any],
    *,
    backend: str | None,
    deterministic_reason: str,
    deterministic_extra: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    _defer_if_refresh: bool = True,
    _prepared_evidence: PreparedAIReviewEvidence | None = None,
) -> tuple[dict[str, Any] | None, bool, str | None]:
    """Run the optional passive reviewer without making parsing depend on it."""

    if not ai_review_enabled():
        return None, False, None
    selected_sources = ai_review_source_ids()
    if not selected_sources or (
        "*" not in selected_sources and source.id not in selected_sources
    ):
        return None, False, None
    if _defer_if_refresh and ai_review_mode() != "quarantine":

        try:
            from .ai_review_evidence import build_ai_review_evidence_v2

            prepared_evidence = build_ai_review_evidence_v2(
                source,
                parsed,
                backend=backend,
                stage="candidate_validation",
                deterministic_ok=True,
                deterministic_extra=deterministic_extra,
                quality=quality,
            )
        except Exception as exc:  # noqa: BLE001 - advisory evidence is fail-open
            _best_effort_log_action(
                "ai.review.evidence_error",
                source_id=source.id,
                backend=backend,
                level="warn",
                error_type=type(exc).__name__,
            )
            return None, False, None

        async def execute() -> tuple[dict[str, Any] | None, bool, str | None]:
            return await _review_candidate_with_ai(
                source,
                {},
                backend=backend,
                deterministic_reason="prepared_evidence",
                deterministic_extra=None,
                quality=None,
                _defer_if_refresh=False,
                _prepared_evidence=prepared_evidence,
            )

        queued = _enqueue_deferred_ai_job(
            _DeferredAIJob(
                source_id=source.id,
                review_kind="candidate",
                execute=execute,
            )
        )
        if queued is not None:
            return None, False, None

    try:
        from .ai_review import review_candidate

        result = await review_candidate(
            source,
            parsed,
            backend=backend,
            deterministic_ok=True,
            deterministic_reason=deterministic_reason,
            deterministic_extra=deterministic_extra,
            quality=quality,
            prepared_evidence=_prepared_evidence,
        )
        telemetry = result.telemetry()
        if result.state == "disabled":
            return None, False, None
        if result.state == "skipped" and result.error_type in {
            "source_allowlist_empty",
            "source_not_selected",
        }:
            return None, False, None
        if result.should_quarantine:
            action = "ai.review.quarantine"
            level = "warn"
        elif result.state == "ok" and result.verdict is not None:
            action = f"ai.review.{result.verdict.verdict}"
            level = "warn" if result.verdict.verdict != "pass" else "info"
        else:
            action = f"ai.review.{result.state}"
            level = "warn" if result.state == "error" else "info"
        _best_effort_log_action(
            action,
            source_id=source.id,
            backend=backend,
            level=level,
            state=(SourceState.QUALITY_ERROR if result.should_quarantine else None),
            extra={"ai_review": telemetry},
        )
        if not result.should_quarantine:
            return telemetry, False, None
        reason_codes = telemetry.get("reason_codes")
        safe_codes = ",".join(reason_codes) if isinstance(reason_codes, list) else "unknown"
        return (
            telemetry,
            True,
            f"AI semantic review quarantined candidate ({safe_codes})",
        )
    except Exception as exc:  # noqa: BLE001 - optional reviewer must fail open
        telemetry = {
            "state": "error",
            "model": "configured",
            "error_type": f"internal_{type(exc).__name__}",
            "quarantine": False,
        }
        _best_effort_log_action(
            "ai.review.error",
            source_id=source.id,
            backend=backend,
            level="warn",
            extra={"ai_review": telemetry},
        )
        return telemetry, False, None


async def _diagnose_candidate_with_ai(
    source: Source,
    parsed: dict[str, Any],
    *,
    backend: str | None,
    stage: str,
    deterministic_reason: str,
    deterministic_extra: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    regression: dict[str, Any] | None = None,
    lkg: dict[str, Any] | None = None,
    post_patch: dict[str, Any] | None = None,
    _defer_if_refresh: bool = True,
    _prepared_evidence: PreparedAIReviewEvidence | None = None,
    _affected_source_ids: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Diagnose a rejected candidate without changing its terminal outcome."""

    if not ai_review_enabled() or not ai_review_diagnose_failures_enabled():
        return None
    if _defer_if_refresh:

        try:
            from .ai_review_evidence import build_ai_review_evidence_v2

            prepared_evidence = build_ai_review_evidence_v2(
                source,
                parsed,
                backend=backend,
                stage=stage,
                deterministic_ok=False,
                deterministic_extra=deterministic_extra,
                quality=quality,
                regression=regression,
                lkg=lkg,
                post_patch=post_patch,
            )
        except Exception as exc:  # noqa: BLE001 - advisory evidence is fail-open
            _best_effort_log_action(
                "ai.diagnosis.evidence_error",
                source_id=source.id,
                backend=backend,
                level="warn",
                error_type=type(exc).__name__,
            )
            return None

        async def execute() -> dict[str, Any] | None:
            return await _diagnose_candidate_with_ai(
                source,
                {},
                backend=backend,
                stage=stage,
                deterministic_reason="prepared_evidence",
                deterministic_extra=None,
                quality=None,
                regression=None,
                lkg=None,
                post_patch=None,
                _defer_if_refresh=False,
                _prepared_evidence=prepared_evidence,
            )

        queued = _enqueue_deferred_ai_job(
            _DeferredAIJob(
                source_id=source.id,
                review_kind="failure_diagnosis",
                execute=execute,
                affected_source_ids=_affected_source_ids,
            )
        )
        if queued is not None:
            return None

    try:
        from .ai_review import review_candidate

        result = await review_candidate(
            source,
            parsed,
            backend=backend,
            deterministic_ok=False,
            deterministic_reason=deterministic_reason,
            deterministic_extra=deterministic_extra,
            quality=quality,
            review_kind="failure_diagnosis",
            stage=stage,
            regression=regression,
            lkg=lkg,
            post_patch=post_patch,
            prepared_evidence=_prepared_evidence,
        )
        if result.state == "disabled":
            return None
        telemetry = result.telemetry()
        if result.state == "ok" and result.diagnosis is not None:
            action = f"ai.diagnosis.{result.diagnosis.classification}"
            level = "warn" if result.diagnosis.classification == "anomalous" else "info"
        else:
            action = f"ai.diagnosis.{result.state}"
            level = "warn" if result.state == "error" else "info"
        _best_effort_log_action(
            action,
            source_id=source.id,
            backend=backend,
            level=level,
            extra={"ai_diagnosis": telemetry},
        )
        return telemetry
    except Exception as exc:  # noqa: BLE001 - diagnosis must remain fail-open
        telemetry = {
            "state": "error",
            "model": "configured",
            "review_kind": "failure_diagnosis",
            "error_type": f"internal_{type(exc).__name__}",
            "quarantine": False,
        }
        _best_effort_log_action(
            "ai.diagnosis.error",
            source_id=source.id,
            backend=backend,
            level="warn",
            extra={"ai_diagnosis": telemetry},
        )
        return telemetry


async def _diagnose_refresh_failure_with_ai(
    selected: list[Source],
    *,
    phase: str,
    backend: str,
) -> None:
    """Schedule one safe AI diagnosis for a failure affecting a whole refresh."""

    if not selected:
        return
    affected_ids = tuple(source.id for source in selected)
    affected_tiers = len({tier_for(source.id) for source in selected})
    await _diagnose_candidate_with_ai(
        selected[0],
        {},
        backend=backend,
        stage="fetch",
        deterministic_reason=phase,
        deterministic_extra={"reason_code": phase},
        quality={
            "affected_sources": len(affected_ids),
            "affected_tiers": affected_tiers,
        },
        _affected_source_ids=affected_ids,
    )


def _attach_ai_review_status(
    status: dict[str, Any], telemetry: dict[str, Any] | None
) -> dict[str, Any]:
    if telemetry:
        status["ai_review"] = telemetry
    return status


def _attach_ai_diagnosis_status(
    status: dict[str, Any], telemetry: dict[str, Any] | None
) -> dict[str, Any]:
    if telemetry:
        status["ai_diagnosis"] = telemetry
    return status


def _published_data_for_ai(source_id: str) -> dict[str, Any] | None:
    """Return the published payload; the evidence builder keeps only safe metrics."""

    try:
        from .parser_control import load_resolved_public_dataset

        dataset = load_resolved_public_dataset(source_id) or {}
    except Exception:  # noqa: BLE001 - optional diagnostic context
        try:
            dataset = load_dataset(source_id) or {}
        except Exception:  # noqa: BLE001 - optional context must be total
            return None
    data = dataset.get("data") if isinstance(dataset, dict) else None
    return data if isinstance(data, dict) else None


def _regression_evidence_for_ai(
    source: Source,
    parsed: dict[str, Any],
    *,
    authoritative_reason: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    lkg = _published_data_for_ai(source.id)
    try:
        detected, message, extra = check_dataset_regression(
            source,
            previous_data=lkg,
            new_data=parsed,
        )
    except Exception:  # noqa: BLE001 - optional diagnosis must remain fail-open
        detected, message, extra = False, None, {}
    if authoritative_reason:
        detected = True
        message = authoritative_reason
    if isinstance(extra.get("collections"), dict):
        reason_code = "collection_drop"
    elif message and "policy changed" in message.casefold():
        reason_code = "policy_changed"
    elif message and "filled metric count" in message.casefold():
        reason_code = "filled_metric_drop"
    elif detected:
        reason_code = "row_count_drop"
    else:
        reason_code = "none"
    return {"detected": detected, "reason_code": reason_code, "extra": extra}, lkg


PROVISIONAL_STATUS_KEYS = (
    "data_phase",
    "provisional",
    "accepted_rows",
    "baseline_rows",
    "coverage_ratio",
    "minimum_sample",
    "patch_window",
)


def _provisional_metadata_from_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    for key in ("structured", "hsreplay_extracted"):
        structured = parsed.get(key)
        if not isinstance(structured, dict):
            continue
        if structured.get("provisional") is not True:
            continue
        if structured.get("data_phase") != "post_patch_early":
            continue
        return {
            field: structured[field]
            for field in PROVISIONAL_STATUS_KEYS
            if field in structured
        }
    return {}


def _attach_provisional_status(
    status: dict[str, Any], metadata: dict[str, object]
) -> dict[str, Any]:
    if not metadata:
        return status
    status.update(metadata)
    quality = status.get("quality")
    if isinstance(quality, dict):
        quality.update(metadata)
    return status


def _status_payload(
    source: Source,
    state: str,
    *,
    fetched_at: str,
    http_status: int | None = None,
    final_url: str | None = None,
    error: str | None = None,
    detail: str | None = None,
    content_length: int | None = None,
    backend: str | None = None,
    transport_backend: str | None = None,
    used_residential_proxy: bool | None = None,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "state": state,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "final_url": final_url,
        "error": error,
        "detail": detail,
        "content_length": content_length,
        "runtime": runtime_version_info(),
    }
    if backend:
        payload["backend"] = backend
    if transport_backend:
        payload["transport_backend"] = transport_backend
    if used_residential_proxy is not None:
        payload["used_residential_proxy"] = used_residential_proxy
    if quality:
        payload["quality"] = quality
        if quality.get("quality_score") is not None:
            payload["quality_score"] = quality.get("quality_score")
        if quality.get("metric_availability_score") is not None:
            payload["metric_availability_score"] = quality.get(
                "metric_availability_score"
            )
        if quality.get("retrieval_completeness_score") is not None:
            payload["retrieval_completeness_score"] = quality.get(
                "retrieval_completeness_score"
            )
        if quality.get("retrieval_complete") is None and "retrieval_complete" in quality:
            payload["retrieval_complete"] = None
        elif isinstance(quality.get("retrieval_complete"), bool):
            payload["retrieval_complete"] = quality["retrieval_complete"]
        if quality.get("rows_total") is not None:
            payload["rows_total"] = quality.get("rows_total")
    return payload


def _attach_failure_class(
    status: dict[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    if isinstance(exc, ProxyPaymentRequiredError):
        status["failure_class"] = f"proxy_{exc.status_code}"
        status["proxy_status"] = exc.status_code
    return status


_TRANSPORT_BACKENDS = frozenset(
    {
        "brightdata_web_unlocker",
        "firecrawl",
        "proxyless_curl_cffi",
        "proxyless_direct",
        "proxyless_flaresolverr",
        "proxyless_jina",
        "residential_httpx",
        "scrape_do",
        "scrape_do_super",
        "scrapfly",
        "wordpress_rest_direct",
    }
)


def _sanitize_transport_backend(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate in _TRANSPORT_BACKENDS:
        return candidate
    if not candidate.startswith("mixed[") or not candidate.endswith("]"):
        return None
    parts = candidate[6:-1].split(",")
    if not parts or any(part not in _TRANSPORT_BACKENDS for part in parts):
        return None
    normalized = sorted(set(parts))
    if len(normalized) == 1:
        return normalized[0]
    return f"mixed[{','.join(normalized)}]"


def _source_uses_residential_proxy(source: Source, backend: str | None) -> bool:
    if not fetch_proxy_url() or not backend:
        return False
    transport_backend = _sanitize_transport_backend(backend)
    if transport_backend is not None:
        return "residential_httpx" in transport_backend
    if backend == "flaresolverr":
        from .scrapers.proxy import source_can_use_flaresolverr_without_proxy

        return not source_can_use_flaresolverr_without_proxy(source)
    if backend == "hsreplay_flaresolverr":
        from .scrapers.proxy import source_can_use_flaresolverr_without_proxy

        return not source_can_use_flaresolverr_without_proxy(source)
    if backend == "hsreplay_premium_flaresolverr":
        return False
    if backend in {"direct", "patchright", "scrapling", "curl_cffi", "cloudscraper", "cloakbrowser"}:
        return True
    if backend == "firestone_api":
        return False
    return backend in {
        "heartharena_api",
        "metastats_api",
        "hearthstone_decks_api",
        "vicious_syndicate_api",
        "hsreplay_api",
        "hsreplay_cards_api",
        "hsreplay_jina_markdown",
    } or backend.startswith("hsreplay_")


def _looks_like_hsreplay_auth_error(message: str) -> bool:
    lower = message.lower()
    return any(
        marker in lower
        for marker in (
            "session not authenticated",
            "not authenticated",
            "premium data",
            "premium data unavailable",
            "login required",
            "subscription",
        )
    )


def _preserve_cached_ok_status(source: Source, failed_status: dict[str, Any]) -> dict[str, Any] | None:
    """Keep the last valid dataset visible when a live refresh attempt fails.

    The refresh job should not turn /health red just because a temporary proxy/CF
    failure prevented a fresh snapshot, as long as we still have a valid cached dataset.
    """
    # The mutable candidate may still contain a small provisional snapshot after
    # the early window has expired.  Preserve the dataset that is actually
    # published (normally the stable baseline), not that hidden candidate.
    from .parser_control import load_resolved_public_dataset

    dataset = load_resolved_public_dataset(source.id)
    if not dataset:
        return None
    parsed = dataset.get("data")
    if not isinstance(parsed, dict) or not parsed:
        return None
    try:
        gate = validate_existing_publication_for_serving(
            source,
            parsed,
            backend=dataset.get("backend"),
        )
        ok, reason = gate.ok, gate.reason
    except Exception as exc:
        ok, reason = False, f"cached validation raised {type(exc).__name__}: {exc}"
    if not ok:
        log_action(
            "dataset.cached.invalid",
            source_id=source.id,
            level="warn",
            detail=reason,
        )
        return None

    cached_at = str(dataset.get("fetched_at") or failed_status.get("fetched_at") or now_iso())
    transport_backend = _sanitize_transport_backend(dataset.get("transport_backend"))
    cached_proxy_usage = dataset.get("used_residential_proxy")
    used_residential_proxy = (
        cached_proxy_usage
        if isinstance(cached_proxy_usage, bool)
        else (
            "residential_httpx" in transport_backend
            if transport_backend is not None
            else None
        )
    )
    try:
        cached_quality = quality_metrics(source, parsed)
    except Exception as exc:
        logger.warning(
            "Could not calculate cached publication quality for %s: %s",
            source.id,
            exc,
        )
        cached_quality = None
    status = _status_payload(
        source,
        SourceState.OK,
        fetched_at=cached_at,
        http_status=dataset.get("http_status"),
        final_url=dataset.get("final_url") or source.url,
        content_length=dataset.get("content_length"),
        backend=dataset.get("backend"),
        transport_backend=transport_backend,
        used_residential_proxy=used_residential_proxy,
        quality=cached_quality,
        detail="Serving cached dataset; latest live refresh failed.",
    )
    _attach_provisional_status(status, _provisional_metadata_from_parsed(parsed))
    status["serving_cached_dataset"] = True
    status["cached_after_failure"] = True
    status["fresh_candidate_published"] = False
    status["cached_backend_policy_grandfathered"] = bool(
        gate.extra.get("backend_policy_grandfathered")
    )
    status["cached_content_temporally_grandfathered"] = bool(
        gate.extra.get("lkg_temporal_grandfathered")
    )
    status["effective_state"] = EFFECTIVE_OK_CACHED
    status["last_refresh_state"] = failed_status.get("state")
    status["last_refresh_at"] = failed_status.get("fetched_at")
    status["last_refresh_error"] = (
        failed_status.get("detail") or failed_status.get("error") or "live refresh failed"
    )
    if isinstance(failed_status.get("quality"), dict):
        status["last_refresh_quality"] = dict(failed_status["quality"])
    if isinstance(failed_status.get("ai_review"), dict):
        status["latest_ai_review"] = failed_status["ai_review"]
    if isinstance(failed_status.get("ai_diagnosis"), dict):
        status["latest_ai_diagnosis"] = failed_status["ai_diagnosis"]
    if failed_status.get("failure_class"):
        status["last_refresh_failure_class"] = failed_status["failure_class"]
    if isinstance(failed_status.get("http_status"), int):
        status["last_refresh_http_status"] = failed_status["http_status"]
    if failed_status.get("backend"):
        status["last_refresh_backend"] = failed_status["backend"]
    if failed_status.get("transport_backend"):
        status["last_refresh_transport_backend"] = failed_status[
            "transport_backend"
        ]
    from .reliability_telemetry import FAILURE_REASONS

    failure_reason = str(failed_status.get("failure_reason_code") or "").strip()
    if failure_reason in FAILURE_REASONS:
        status["failure_reason_code"] = failure_reason
    if failed_status.get("upstream_state") == "upstream_publication_pending":
        status["upstream_state"] = "upstream_publication_pending"
        status["last_refresh_upstream_state"] = "upstream_publication_pending"
    if source.id == "vicious_syndicate_radars":
        from .vicious_syndicate import sanitize_upstream_readiness

        readiness = sanitize_upstream_readiness(
            failed_status.get("upstream_readiness")
            or failed_status.get("last_refresh_upstream_readiness")
        )
        if readiness:
            status["last_refresh_upstream_readiness"] = readiness
    if failed_status.get("proxy_status") in {402, 407}:
        status["last_refresh_proxy_status"] = failed_status["proxy_status"]
    for evidence_key in ("parsesunix_transport", "parsesunix_shadow"):
        evidence = failed_status.get(evidence_key)
        if isinstance(evidence, dict):
            status[f"last_refresh_{evidence_key}"] = dict(evidence)
    try:
        cached_dt = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=UTC)
        status["cached_dataset_age_hours"] = round(
            max(0.0, (datetime.now(UTC) - cached_dt).total_seconds() / 3600),
            2,
        )
    except ValueError:
        pass
    save_status(source.id, status)
    log_action(
        "dataset.preserve_previous_good",
        source_id=source.id,
        state=SourceState.OK,
        backend=status.get("backend"),
        level="warn",
        detail=str(status["last_refresh_error"])[:500],
        extra={
            "last_refresh_state": status.get("last_refresh_state"),
            "transport_backend": transport_backend,
            "backend_policy_grandfathered": status.get(
                "cached_backend_policy_grandfathered"
            ),
        },
    )
    return status


def _save_failure_status(
    source: Source,
    status: dict[str, Any],
    *,
    publication_attempt: Any | None = None,
) -> dict[str, Any]:
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
        PublicationUnavailable,
    )

    if source.id == STANDARD_CARDS_SOURCE_ID:
        attempt = publication_attempt or _standard_publication_attempt.get()
        try:
            reconciliation = DatasetPublicationStore().reconcile_current_publication(
                source.id,
                candidate_dataset_version=None,
                expected_dataset_version=None,
                status=status,
                attempt_generation=(attempt.generation if attempt else None),
                attempt_id=(attempt.attempt_id if attempt else None),
                attempt_started_at=(attempt.started_at if attempt else None),
            )
            return reconciliation.status
        except PublicationUnavailable:
            # Cold start has no LKG to reconcile; retain the ordinary failure
            # status until the first successful publication exists. The second
            # manifest check and status generation CAS share the same lock.
            return DatasetPublicationStore().record_status_without_publication(
                source.id,
                status=status,
                attempt_generation=(attempt.generation if attempt else None),
                attempt_id=(attempt.attempt_id if attempt else None),
                attempt_started_at=(attempt.started_at if attempt else None),
            )
    preserved = _preserve_cached_ok_status(source, status)
    if preserved is not None:
        return preserved
    save_status(source.id, status)
    return status


async def send_telegram_alert(source_id: str, state: str, detail: str, url: str) -> None:
    from .config import telegram_bot_token, telegram_chat_id

    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        log_action(
            "alert.skipped",
            source_id=source_id,
            level="warn",
            detail="Telegram token/chat_id not configured",
            extra={"state": state},
        )
        return
    if not should_send_alert(source_id, state):
        log_action(
            "alert.skipped",
            source_id=source_id,
            detail="Telegram alert deduped",
            extra={"state": state},
        )
        return

    safe_source_id = html.escape(source_id)
    safe_url = html.escape(url)
    safe_state = html.escape(state)
    safe_detail = html.escape(detail or "N/A")
    text_message = (
        f"⚠️ <b>Hearthstone Parser Alert</b>\n\n"
        f"<b>Source ID:</b> <code>{safe_source_id}</code>\n"
        f"<b>URL:</b> {safe_url}\n"
        f"<b>State:</b> 🟥 <code>{safe_state}</code>\n"
        f"<b>Detail:</b> {safe_detail}\n"
        f"<b>Time:</b> {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text_message,
        "parse_mode": "HTML",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(api_url, json=payload)
            response.raise_for_status()
        mark_alert_sent(source_id, state)
        log_action(
            "alert.sent",
            source_id=source_id,
            detail="Telegram alert sent",
            extra={"state": state},
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Failed to send Telegram notification: %s", e)
        log_action(
            "alert.failed",
            source_id=source_id,
            level="error",
            detail=str(e)[:500],
            extra={"state": state},
        )


async def _maybe_cached_after_failure_alert(source: Source, status: dict[str, Any]) -> None:
    if not status.get("serving_cached_dataset"):
        return
    if status.get("last_refresh_state") in (None, SourceState.OK):
        return
    detail = (
        "Serving cached dataset after live refresh failed; "
        f"last_state={status.get('last_refresh_state')}; "
        f"reason={status.get('last_refresh_error') or status.get('detail') or 'unknown'}"
    )
    log_action(
        "dataset.cached_after_failure.alert",
        source_id=source.id,
        level="warn",
        detail=detail[:1000],
        extra={
            "last_refresh_state": status.get("last_refresh_state"),
            "last_refresh_at": status.get("last_refresh_at"),
            "cached_dataset_age_hours": status.get("cached_dataset_age_hours"),
        },
    )
    await send_telegram_alert(source.id, "cached_after_failure", detail, source.url)


async def _fetch_direct(_client: httpx.AsyncClient, source: Source) -> tuple[str, int, str]:
    """FIX: resilient direct fetch — sticky proxy, session burn, exponential backoff."""
    from .scrapers.http_resilience import build_fetch_headers
    from .scrapers.proxy import httpx_client_kwargs

    url = source.fetch_url
    def _client_kwargs() -> dict[str, Any]:
        if fetch_proxy_url():
            return httpx_client_kwargs(
                source.id, page_url=url, timeout=request_timeout_seconds()
            )
        return {
            "timeout": request_timeout_seconds(),
            "follow_redirects": True,
        }

    kwargs = _client_kwargs()
    proxy_url = proxy_url_for_source(source.id, page_url=url) if fetch_proxy_url() else None

    headers = build_fetch_headers(url, extra={"User-Agent": user_agent()})

    def _burn() -> None:
        nonlocal proxy_url
        burn_proxy_session(source.id, page_url=url, reason="direct_fetch_blocked")
        kwargs.clear()
        kwargs.update(_client_kwargs())
        proxy_url = proxy_url_for_source(source.id, page_url=url) if fetch_proxy_url() else None

    return await resilient_http_get(
        url,
        source_id=source.id,
        client_kwargs=kwargs,
        headers=headers,
        max_attempts=http_retry_attempts(),
        proxy_url=proxy_url,
        proxy_check_url=proxy_check_url(),
        on_session_burn=_burn,
        validate_body=lambda code, body: not is_session_blocked(code, body),
    )


def _attach_parsesunix_observation(
    payload: dict[str, Any],
    *,
    mode: str,
    observation: dict[str, object] | None,
    publication_validated: bool | None = None,
) -> None:
    if observation is None or mode not in {"parsesunix", "shadow"}:
        return
    evidence = dict(observation)
    if mode == "parsesunix" and publication_validated is not None:
        evidence["publication_validated"] = publication_validated
    key = "parsesunix_transport" if mode == "parsesunix" else "parsesunix_shadow"
    payload[key] = evidence


async def _complete_parsesunix_shadow(
    task: asyncio.Task[TransportEvidence],
    source: Source,
    *,
    legacy_body: str | None,
    legacy_http_status: int | None,
    legacy_backend: str | None,
) -> dict[str, object]:
    """Compare a shadow response without publishing or mutating source state."""

    try:
        evidence = await task
    except Exception as exc:  # noqa: BLE001 - shadow failures must not affect legacy
        return {
            "mode": "shadow",
            "error_type": type(exc).__name__,
            "transport_validated": False,
            "publication_validated": None,
            "paid_requests": 0,
            "paid_cost_usd": "0",
            "cost_certainty": "exact",
        }

    observation = evidence.telemetry()
    observation.update(
        {
            "mode": "shadow",
            "legacy_backend": legacy_backend,
            "legacy_http_status": legacy_http_status,
            "legacy_body_bytes": (
                len(legacy_body.encode("utf-8", errors="replace"))
                if legacy_body is not None
                else None
            ),
            "http_status_match": (
                evidence.http_status == legacy_http_status
                if legacy_http_status is not None
                else None
            ),
            "content_hash_match": (
                evidence.content_sha256
                == hashlib.sha256(
                    legacy_body.encode("utf-8", errors="replace")
                ).hexdigest()
                if legacy_body is not None
                else None
            ),
            "candidate_validated": None,
        }
    )
    if not evidence.transport_validated:
        return observation

    try:
        parsed = parse_html(source, evidence.body)
        gate = validate_candidate_for_publish(
            source,
            parsed,
            backend=evidence.backend,
        )
        observation["candidate_validated"] = gate.ok
        observation["candidate_reason"] = str(gate.reason)[:500]
        observation["candidate_metric_count"] = estimate_metric_count(parsed)
    except Exception as exc:  # noqa: BLE001 - parsers and gates are plugin boundaries
        observation["candidate_validated"] = False
        observation["candidate_error_type"] = type(exc).__name__
    return observation


async def _fetch_generic_html(
    client: httpx.AsyncClient | None,
    source: Source,
    *,
    preferred_backend: str,
) -> _GenericHtmlFetch:
    try:
        mode = parsesunix_mode_for_source(source.id)
    except ValueError as exc:
        raise ParsesUnixExecutionError(
            f"Invalid ParsesUnix rollout configuration: {exc}"
        ) from exc
    shadow_task: asyncio.Task[TransportEvidence] | None = None
    if mode == "shadow":
        shadow_task = asyncio.create_task(
            fetch_with_parsesunix(source.fetch_url),
            name=f"parsesunix-shadow:{source.id}",
        )

    try:
        if mode == "parsesunix":
            try:
                evidence = await fetch_with_parsesunix(source.fetch_url)
            except Exception as exc:
                raise ParsesUnixExecutionError(
                    f"ParsesUnix direct transport failed: {type(exc).__name__}"
                ) from exc
            observation = evidence.telemetry()
            log_action(
                "parsesunix.transport.observe",
                source_id=source.id,
                backend=evidence.backend,
                http_status=evidence.http_status,
                level="info" if evidence.transport_validated else "warn",
                extra=observation,
            )
            if not evidence.transport_validated:
                raise ParsesUnixTransportRejected(evidence)
            return _GenericHtmlFetch(
                body=evidence.body,
                http_status=evidence.http_status or 0,
                final_url=evidence.final_url,
                backend=evidence.backend,
                page_snapshot=None,
                parsesunix_mode=mode,
                parsesunix_observation=observation,
            )

        if fetch_direct_enabled() and client is not None:
            log_action(
                "http.request.begin",
                source_id=source.id,
                backend="direct",
                url=source.fetch_url,
            )
            body, http_status, final_url = await _fetch_direct(client, source)
            backend = "direct"
            page_snapshot = None
            log_action(
                "http.request.ok",
                source_id=source.id,
                backend=backend,
                http_status=http_status,
                url=str(final_url),
                bytes_out=len(body.encode("utf-8", errors="replace")),
            )
        else:
            result = await fetch_html(
                source,
                preferred_backend=preferred_backend,
                parse_preview=lambda html: parse_html(source, html),
            )
            body = result.html
            http_status = result.http_status
            final_url = result.final_url
            backend = result.backend
            page_snapshot = result.snapshot
    except asyncio.CancelledError:
        if shadow_task is not None:
            shadow_task.cancel()
            await asyncio.gather(shadow_task, return_exceptions=True)
        raise
    except Exception:
        if shadow_task is not None:
            observation = await _complete_parsesunix_shadow(
                shadow_task,
                source,
                legacy_body=None,
                legacy_http_status=None,
                legacy_backend=None,
            )
            log_action(
                "parsesunix.shadow.observe",
                source_id=source.id,
                level="info",
                extra=observation,
            )
        raise

    observation = None
    if shadow_task is not None:
        observation = await _complete_parsesunix_shadow(
            shadow_task,
            source,
            legacy_body=body,
            legacy_http_status=http_status,
            legacy_backend=backend,
        )
        log_action(
            "parsesunix.shadow.observe",
            source_id=source.id,
            level="info",
            extra=observation,
        )

    return _GenericHtmlFetch(
        body=body,
        http_status=http_status,
        final_url=final_url,
        backend=backend,
        page_snapshot=page_snapshot,
        parsesunix_mode=mode,
        parsesunix_observation=observation,
    )


def _parsesunix_allows_paid_fallback(error: Exception) -> bool:
    if isinstance(error, ParsesUnixTransportRejected):
        return error.evidence.paid_escalation_allowed
    return not isinstance(error, ParsesUnixIntegrationError)


def _save_dataset_with_checks(
    source: Source,
    dataset: dict[str, Any],
    *,
    fetched_at: str,
) -> tuple[bool, str | None, dict[str, object]]:
    """
    Save dataset; run regression check against previous snapshot.
    Returns (regression_detected, regression_message, provisional_metadata).
    """
    previous = load_dataset(source.id)
    dataset.setdefault("runtime", runtime_version_info())
    prev_data = (previous or {}).get("data")
    new_data = dataset.get("data") or {}
    policy_changed, captured_policy, current_policy = early_policy_changed_since_capture(
        source.id
    )
    if policy_changed:
        message = (
            "Publication policy changed after early validation; candidate was not saved"
        )
        log_action(
            "dataset.save.skip_policy_change",
            source_id=source.id,
            state=SourceState.PARTIAL,
            level="warn",
            detail=message,
            extra={
                "captured_mode": captured_policy.effective_mode if captured_policy else None,
                "captured_revision": captured_policy.revision if captured_policy else None,
                "captured_token": captured_policy.token if captured_policy else None,
                "current_mode": (current_policy or {}).get("effectiveMode"),
                "current_revision": (current_policy or {}).get("revision"),
                "current_token": (current_policy or {}).get("token"),
                "reason": "early_to_stable_race_guard",
            },
        )
        return True, message, {}
    reg, msg, extra = check_dataset_regression(
        source, previous_data=prev_data, new_data=new_data
    )
    if reg:
        log_action(
            "quality.regression.warn",
            source_id=source.id,
            level="warn",
            detail=msg,
            extra=extra,
        )
        log_action(
            "dataset.preserve_previous_good",
            source_id=source.id,
            level="warn",
            detail=msg,
            extra={**extra, "reason": "regression_gate"},
        )
        log_action(
            "dataset.save.skip_regression",
            source_id=source.id,
            level="warn",
            detail=msg,
            extra=extra,
        )
        return reg, msg, {}
    previous_structured = (prev_data or {}).get("structured") or {}
    previous_baseline = previous_structured.get("baseline_rows")
    if previous_structured.get("provisional") and isinstance(previous_baseline, int):
        baseline_rows = previous_baseline
    else:
        baseline_rows = estimate_metric_count(source, prev_data or {})
    accepted_rows = estimate_metric_count(source, new_data)
    try:
        metadata_time = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        metadata_time = None
    provisional_metadata = build_provisional_metadata(
        source.id,
        accepted_rows=accepted_rows,
        baseline_rows=baseline_rows or accepted_rows,
        at=metadata_time,
    )
    if provisional_metadata:
        if previous is not None and not previous_structured.get("provisional"):
            # Keep an always-current stable publication channel. The dated
            # baseline remains for backwards compatibility and audit/history.
            save_baseline(
                source.id,
                STABLE_PUBLICATION_BASELINE_LABEL,
                previous,
            )
            baseline_created = save_baseline_once(
                source.id,
                POST_PATCH_BASELINE_LABEL,
                previous,
            )
            if baseline_created:
                log_action(
                    "dataset.baseline.preserve",
                    source_id=source.id,
                    state=SourceState.OK,
                    extra={"label": POST_PATCH_BASELINE_LABEL},
                )
        for key in ("structured", "hsreplay_extracted"):
            structured = new_data.get(key)
            if isinstance(structured, dict):
                structured.update(provisional_metadata)
    elif source.id in EARLY_SOURCE_IDS:
        # Any fully validated non-provisional dataset becomes the new stable
        # channel for sources that can publish provisional rows. Early
        # snapshots never overwrite this file.
        save_baseline(
            source.id,
            STABLE_PUBLICATION_BASELINE_LABEL,
            dataset,
        )
    save_dataset(source.id, dataset)
    log_extra = dict(extra)
    log_extra.update(provisional_metadata)
    log_action(
        "dataset.save",
        source_id=source.id,
        state=SourceState.OK,
        backend=dataset.get("backend"),
        bytes_out=dataset.get("content_length"),
        extra=log_extra or None,
    )
    return reg, msg, provisional_metadata


async def _maybe_stale_data_alert(source: Source, status: dict[str, Any]) -> None:
    if status.get("state") not in FAILURE_STATES:
        return
    from .config import stale_dataset_hours

    dataset = load_dataset(source.id)
    if not dataset:
        return
    fetched_at = dataset.get("fetched_at")
    if not fetched_at:
        return
    try:
        prev_ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if prev_ts.tzinfo is None:
            prev_ts = prev_ts.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - prev_ts).total_seconds() / 3600
    except ValueError:
        return
    if age_hours >= stale_dataset_hours():
        detail = (
            f"Serving stale dataset ({age_hours:.1f}h old); latest fetch failed: "
            f"{status.get('detail', '')[:500]}"
        )
        log_action(
            "dataset.stale.warn",
            source_id=source.id,
            level="warn",
            detail=detail,
            extra={"age_hours": round(age_hours, 1)},
        )
        await send_telegram_alert(source.id, "stale_data", detail, source.url)


def _dataset_from_structured(
    source: Source,
    structured: dict[str, Any],
    *,
    backend: str,
) -> dict[str, Any]:
    import json as json_mod

    from .structured_schema import validate_structured_schema

    public_structured = dict(structured)
    candidate_transport = public_structured.pop("_fetch_backend", None)
    if candidate_transport is None and source.site == "hsreplay":
        from .hsreplay_client import consume_hsreplay_json_transport_backend

        candidate_transport = consume_hsreplay_json_transport_backend(source.id)
    transport_backend = _sanitize_transport_backend(candidate_transport)
    schema_validation = validate_structured_schema(public_structured)
    body = json_mod.dumps(public_structured, ensure_ascii=False)
    parsed = {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "title": source.description or source.id,
        "tables": [],
        "json_scripts": [],
        "hsreplay_bootstrap": None,
        "structured": public_structured,
        "hsreplay_extracted": public_structured,
        "schema_validation": schema_validation,
        "deck_codes": [],
        "links": [],
        "text_preview": [],
        "counts": {
            "tables": 0,
            "json_scripts": 0,
            "deck_codes": 0,
            "links": 0,
            "text_lines": 0,
            "api_bytes": len(body.encode("utf-8")),
        },
        "_backend": backend,
    }
    if transport_backend is not None:
        parsed["_transport_backend"] = transport_backend
    return parsed


def _dedupe_streamer_decks_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    from .deck_decode import first_deck_code_from_text

    tables = parsed.get("tables") or []
    if not tables:
        return parsed
    table = tables[0]
    headers = table.get("headers") or []
    objects = table.get("objects") or []
    if not isinstance(objects, list):
        return parsed

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in objects:
        if not isinstance(row, dict):
            continue
        deck_text = str(row.get("Deck") or "")
        deck_code = str(row.get("deck_code") or "").strip() or (first_deck_code_from_text(deck_text) or "")
        if deck_code:
            row["deck_code"] = deck_code
            key = f"code:{deck_code}"
        else:
            key = "|".join(
                [
                    str(row.get("Format") or "").strip().lower(),
                    str(row.get("Streamer") or "").strip().lower(),
                    deck_text[:160].strip().lower(),
                ]
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    table["objects"] = deduped
    if headers:
        table["rows"] = [[row.get(header, "") for header in headers] for row in deduped]
    structured = parsed.get("structured")
    if isinstance(structured, dict) and structured.get("type") == "streamer_decks":
        structured["rows"] = deduped
    return parsed


def _enrich_firecrawl_trinkets_from_cache(source: Source, parsed: dict[str, Any]) -> dict[str, Any]:
    from .structured import (
        enrich_trinket_variant_fields,
        normalize_trinket_tribe,
        trinket_identity_key,
        trinket_variant_key,
    )
    from .trinket_slices import TRINKET_SLICE_SOURCE_IDS

    if source.id not in {
        "hsreplay_battlegrounds_trinkets_lesser",
        "hsreplay_battlegrounds_trinkets_greater",
    } | TRINKET_SLICE_SOURCE_IDS:
        return parsed
    structured = parsed.get("structured")
    if not isinstance(structured, dict) or structured.get("type") != "bg_trinkets":
        return parsed
    rows = structured.get("trinkets") or []
    if not isinstance(rows, list) or not rows:
        return parsed
    source_meta = structured.get("source") or {}
    if source_meta.get("backend") == "hsreplay_json_api":
        return parsed

    previous = load_dataset(source.id) or {}
    previous_structured = (previous.get("data") or {}).get("structured") or {}
    canonical_rows = previous_structured.get("trinkets") or []
    trinket_type = "Lesser" if source.id.endswith("_lesser") else "Greater"
    normalized_canonical = [
        enrich_trinket_variant_fields(dict(row), trinket_type=trinket_type)
        for row in canonical_rows
        if isinstance(row, dict) and row.get("name")
    ]
    by_id = {
        str(row.get("trinket_id") or row.get("id") or "").strip().lower(): row
        for row in normalized_canonical
        if row.get("trinket_id") or row.get("id")
    }
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in normalized_canonical:
        by_name.setdefault(str(row.get("name") or "").strip().lower(), []).append(row)

    previous_active = [
        enrich_trinket_variant_fields(dict(row), trinket_type=trinket_type)
        for row in normalized_canonical
        if isinstance(row, dict) and (row.get("pick_rate") or row.get("avg_placement"))
    ]
    enriched_by_key: dict[str, dict[str, Any]] = {
        trinket_variant_key(row, trinket_type): row
        for row in previous_active
        if row.get("name")
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = enrich_trinket_variant_fields(dict(row), trinket_type=trinket_type)
        name_key = str(row.get("name") or "").strip().lower()
        id_key = str(row.get("trinket_id") or row.get("id") or "").strip().lower()
        tribe, _ = normalize_trinket_tribe(row.get("tribe") or row.get("race"))
        candidates = by_name.get(name_key) or []
        canonical = by_id.get(id_key) if id_key else None
        if not canonical and tribe:
            canonical = next((item for item in candidates if item.get("tribe") == tribe), None)
        if not canonical and len(candidates) == 1:
            canonical = candidates[0]
        canonical = canonical or {}
        if not canonical:
            continue
        merged = {**canonical, **row}
        if not merged.get("id") and merged.get("trinket_id"):
            merged["id"] = merged["trinket_id"]
        if not merged.get("type"):
            merged["type"] = trinket_type
        if canonical.get("trinket_id") and not merged.get("trinket_id"):
            merged["trinket_id"] = canonical["trinket_id"]
        if canonical.get("id") and not merged.get("id"):
            merged["id"] = canonical["id"]
        if canonical.get("dbfId") and not merged.get("dbfId"):
            merged["dbfId"] = canonical["dbfId"]
        if canonical.get("type") and not merged.get("type"):
            merged["type"] = canonical["type"]
        if canonical.get("localized_name") and not merged.get("localized_name"):
            merged["localized_name"] = canonical["localized_name"]
        if canonical.get("description") and not merged.get("description"):
            merged["description"] = canonical["description"]
        merged = enrich_trinket_variant_fields(merged, trinket_type=trinket_type)
        if merged.get("trinket_id") and (merged.get("pick_rate") or merged.get("avg_placement")):
            identity = trinket_identity_key(merged, trinket_type)
            for existing_key, existing in list(enriched_by_key.items()):
                if trinket_identity_key(existing, trinket_type) == identity:
                    enriched_by_key.pop(existing_key, None)
            enriched_by_key[trinket_variant_key(merged, trinket_type)] = merged

    enriched = list(enriched_by_key.values())
    if enriched:
        structured["trinkets"] = enriched
        structured["active_trinkets"] = len(enriched)
        structured["source"] = {
            **(structured.get("source") or {}),
            "backend": "firecrawl",
            "canonical_enriched_from_cache": True,
            "firecrawl_rows_merged_with_previous_active_cache": True,
        }
    return parsed


def _enrich_firecrawl_bg_heroes_from_cache(source: Source, parsed: dict[str, Any]) -> dict[str, Any]:
    if source.id != "hsreplay_battlegrounds_heroes":
        return parsed
    structured = parsed.get("structured")
    if not isinstance(structured, dict) or structured.get("type") != "bg_heroes":
        return parsed
    rows = structured.get("heroes") or []
    if not isinstance(rows, list) or not rows:
        return parsed

    previous = load_dataset(source.id) or {}
    previous_structured = (previous.get("data") or {}).get("structured") or {}
    canonical_rows = previous_structured.get("heroes") or []
    if not isinstance(canonical_rows, list) or not canonical_rows:
        return parsed

    by_dbf: dict[int, dict[str, Any]] = {}
    for row in canonical_rows:
        if not isinstance(row, dict) or row.get("dbfId") is None:
            continue
        try:
            by_dbf[int(row.get("dbfId"))] = row
        except (TypeError, ValueError):
            continue
    by_name = {
        str(row.get("hero") or row.get("name") or "").strip().lower(): row
        for row in canonical_rows
        if isinstance(row, dict) and (row.get("hero") or row.get("name"))
    }
    enriched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key_dbf = row.get("dbfId")
        canonical = None
        if key_dbf is not None:
            try:
                canonical = by_dbf.get(int(key_dbf))
            except (TypeError, ValueError):
                canonical = None
        if not canonical:
            canonical = by_name.get(str(row.get("hero") or row.get("name") or "").strip().lower())
        merged = {**(canonical or {}), **row}
        identity = str(merged.get("dbfId") or merged.get("hero") or merged.get("name") or "").strip().lower()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        enriched.append(merged)

    if len(enriched) >= len(rows):
        structured["heroes"] = enriched
        structured["source"] = {
            **(structured.get("source") or {}),
            "backend": "firecrawl",
            "canonical_enriched_from_cache": True,
        }
    return parsed


async def _try_firecrawl_html(
    source: Source,
    *,
    fetched_at: str,
    reason: str,
) -> dict[str, Any] | None:
    global _firecrawl_fallback_attempts
    is_primary = reason == "primary"
    if source.id not in (firecrawl_primary_source_ids() | firecrawl_fallback_source_ids()):
        return None
    if not is_primary:
        max_attempts = firecrawl_fallback_max_attempts_per_refresh()
        if _firecrawl_fallback_attempts >= max_attempts:
            log_action(
                "firecrawl.fetch.skip",
                source_id=source.id,
                backend="firecrawl",
                level="warn",
                detail=f"Firecrawl fallback attempt cap reached ({max_attempts})",
                extra={"reason": reason},
            )
            return None
        per_source_cap = firecrawl_fallback_max_attempts_per_source()
        source_attempts = _firecrawl_fallback_attempts_by_source.get(source.id, 0)
        if source_attempts >= per_source_cap:
            log_action(
                "firecrawl.fetch.skip",
                source_id=source.id,
                backend="firecrawl",
                level="warn",
                detail=f"Firecrawl per-source fallback cap reached ({per_source_cap})",
                extra={"reason": reason},
            )
            return None
        _firecrawl_fallback_attempts += 1
        _firecrawl_fallback_attempts_by_source[source.id] = source_attempts + 1
    try:
        from .firecrawl_backend import scrape_source

        accepted_candidate: dict[str, Any] = {}

        def _parse_provider_candidate(scraped: Any) -> dict[str, Any]:
            snapshot = None
            if scraped.markdown:
                snapshot = {
                    "lines": [
                        line.strip()
                        for line in scraped.markdown.splitlines()
                        if line.strip()
                    ]
                }
            candidate = parse_html(source, scraped.html, snapshot=snapshot)
            if not candidate.get("title"):
                candidate["title"] = source.description or source.id
            if source.id == "hsguru_streamer_decks_legend_1000":
                candidate = _dedupe_streamer_decks_parsed(candidate)
            candidate = _enrich_firecrawl_trinkets_from_cache(source, candidate)
            return _enrich_firecrawl_bg_heroes_from_cache(source, candidate)

        def _accept_provider_candidate(scraped: Any) -> bool:
            try:
                candidate = _parse_provider_candidate(scraped)
                candidate_gate = validate_candidate_for_publish(
                    source,
                    candidate,
                    backend=scraped.backend,
                )
            except Exception:  # noqa: BLE001 - provider acceptance fails closed
                return False
            if not candidate_gate.ok:
                return False
            accepted_candidate.update(
                {
                    "scraped": scraped,
                    "parsed": candidate,
                    "gate": candidate_gate,
                }
            )
            return True

        log_action(
            "firecrawl.fetch.begin",
            source_id=source.id,
            backend="firecrawl",
            level="warn" if reason != "primary" else "info",
            detail=reason,
        )
        scraped = await scrape_source(
            source,
            accept_result=_accept_provider_candidate,
        )
        backend = scraped.backend
        if accepted_candidate.get("scraped") is scraped:
            parsed = accepted_candidate["parsed"]
            gate = accepted_candidate["gate"]
        else:
            parsed = _parse_provider_candidate(scraped)
            gate = validate_candidate_for_publish(source, parsed, backend=backend)
        ok, validation_reason = gate.ok, gate.reason
        qmetrics = quality_metrics(source, parsed)
        ai_telemetry: dict[str, Any] | None = None
        ai_quarantine = False
        if ok and ai_review_mode() == "quarantine":
            ai_telemetry, ai_quarantine, ai_reason = await _review_candidate_with_ai(
                source,
                parsed,
                backend=backend,
                deterministic_reason=validation_reason,
                deterministic_extra=gate.extra,
                quality=qmetrics,
            )
            if ai_quarantine:
                ok = False
                validation_reason = ai_reason or "AI semantic review quarantined candidate"
        if not ok:
            log_action(
                "firecrawl.validate.fail",
                source_id=source.id,
                backend=backend,
                state=SourceState.QUALITY_ERROR,
                level="warn",
                detail=validation_reason,
                extra={
                    "quality_metrics": qmetrics,
                    "publish_gate": gate.extra,
                    "ai_review": ai_telemetry,
                },
            )
            if is_primary:
                return None
            ai_diagnosis = None
            if not ai_quarantine:
                ai_diagnosis = await _diagnose_candidate_with_ai(
                    source,
                    parsed,
                    backend=backend,
                    stage="deterministic_rejection",
                    deterministic_reason=validation_reason,
                    deterministic_extra=gate.extra,
                    quality=qmetrics,
                    lkg=_published_data_for_ai(source.id),
                )
            status = _status_payload(
                source,
                SourceState.QUALITY_ERROR,
                fetched_at=fetched_at,
                http_status=scraped.status_code,
                final_url=scraped.final_url,
                content_length=scraped.content_length,
                backend=backend,
                detail=validation_reason,
                used_residential_proxy=False,
                quality=qmetrics,
            )
            _attach_ai_review_status(status, ai_telemetry)
            _attach_ai_diagnosis_status(status, ai_diagnosis)
            status["firecrawl_credits_used"] = scraped.firecrawl_credits_used
            status["scrape_do_credits_used"] = scraped.scrape_do_credits_used
            status["scrapfly_credits_used"] = scraped.scrapfly_credits_used
            status["brightdata_credits_used"] = scraped.brightdata_credits_used
            status["brightdata_requests_used"] = scraped.brightdata_requests_used
            return _save_failure_status(source, status)

        dataset = {
            "state": SourceState.OK,
            "fetched_at": fetched_at,
            "http_status": scraped.status_code,
            "final_url": scraped.final_url,
            "content_length": scraped.content_length,
            "backend": backend,
            "used_residential_proxy": False,
            "data": parsed,
        }
        reg, reg_msg, provisional_metadata = _save_dataset_with_checks(
            source, dataset, fetched_at=fetched_at
        )
        if not reg and ai_review_mode() != "quarantine":
            ai_telemetry, _, _ = await _review_candidate_with_ai(
                source,
                parsed,
                backend=backend,
                deterministic_reason=validation_reason,
                deterministic_extra=gate.extra,
                quality=qmetrics,
            )
        ai_diagnosis = None
        if reg:
            regression_evidence, lkg = _regression_evidence_for_ai(
                source,
                parsed,
                authoritative_reason=reg_msg,
            )
            ai_diagnosis = await _diagnose_candidate_with_ai(
                source,
                parsed,
                backend=backend,
                stage="regression_rejection",
                deterministic_reason=reg_msg or "dataset regression",
                deterministic_extra=None,
                quality=qmetrics,
                regression=regression_evidence,
                lkg=lkg,
                post_patch=provisional_metadata,
            )
        state = SourceState.PARTIAL if reg else SourceState.OK
        status = _status_payload(
            source,
            state,
            fetched_at=fetched_at,
            http_status=scraped.status_code,
            final_url=scraped.final_url,
            content_length=scraped.content_length,
            backend=backend,
            detail=reg_msg if reg else None,
            used_residential_proxy=False,
            quality=qmetrics,
        )
        status["firecrawl_credits_used"] = scraped.firecrawl_credits_used
        status["scrape_do_credits_used"] = scraped.scrape_do_credits_used
        status["scrapfly_credits_used"] = scraped.scrapfly_credits_used
        status["brightdata_credits_used"] = scraped.brightdata_credits_used
        status["brightdata_requests_used"] = scraped.brightdata_requests_used
        status["brightdata_budget_remaining"] = scraped.metadata.get(
            "brightDataBudgetRemaining"
        )
        status["brightdata_request_id"] = scraped.metadata.get(
            "brightDataRequestId"
        )
        status["brightdata_rendered"] = scraped.metadata.get(
            "brightDataRendered"
        )
        status["firecrawl_cache_state"] = scraped.metadata.get("cacheState")
        _attach_ai_review_status(status, ai_telemetry)
        _attach_ai_diagnosis_status(status, ai_diagnosis)
        _attach_provisional_status(status, provisional_metadata)
        if reg:
            status = _save_failure_status(source, status)
        else:
            save_status(source.id, status)
        log_action(
            "firecrawl.fetch.ok",
            source_id=source.id,
            backend=backend,
            state=state,
            bytes_out=scraped.content_length,
            extra={
                "credits_used": scraped.request_credits,
                "brightdata_budget_remaining": scraped.metadata.get(
                    "brightDataBudgetRemaining"
                ),
                "brightdata_request_id": scraped.metadata.get(
                    "brightDataRequestId"
                ),
                "cache_state": scraped.metadata.get("cacheState"),
                "reason": reason,
                "quality_metrics": qmetrics,
                **provisional_metadata,
            },
        )
        return status
    except Exception as exc:
        log_action(
            "firecrawl.fetch.fail",
            source_id=source.id,
            backend="firecrawl",
            state=SourceState.FETCH_ERROR,
            level="warn",
            error_type=type(exc).__name__,
            detail=str(exc)[:1000],
            extra={"reason": reason},
        )
        return None


async def _fetch_hsreplay_api_source(source: Source) -> dict[str, Any] | None:
    if source.id == "hsreplay_decks_trending":
        from .hsreplay_trending import fetch_hsreplay_trending

        structured = await fetch_hsreplay_trending(source)
        backend = structured.get("source", {}).get(
            "backend", "hsreplay_trending_api"
        )
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_arena_winning_decks":
        from .hsreplay_arena_api import fetch_winning_decks

        structured = await fetch_winning_decks(source_id=source.id)
        backend = structured.get("source", {}).get("backend", "hsreplay_api")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_battlegrounds_comps":
        from .battlegrounds_comps_parse import fetch_battlegrounds_comps

        structured = await fetch_battlegrounds_comps(source_id=source.id, detail_limit=40)
        backend = structured.get("source", {}).get("backend", "hsreplay_jina_markdown")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_battlegrounds_heroes":
        from .hsreplay_bg_heroes import fetch_hsreplay_battlegrounds_heroes

        structured = await fetch_hsreplay_battlegrounds_heroes(source)
        backend = structured.get("source", {}).get("backend", "hsreplay_premium_flaresolverr")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_battlegrounds_minions":
        from .hsreplay_bg_stats import fetch_battlegrounds_minions

        structured = await fetch_battlegrounds_minions(source.id)
        backend = structured.get("source", {}).get("backend", "hsreplay_bg_api")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_battlegrounds_compositions":
        from .hsreplay_bg_stats import fetch_battlegrounds_compositions

        structured = await fetch_battlegrounds_compositions(source.id)
        backend = structured.get("source", {}).get("backend", "hsreplay_bg_api")
        return _dataset_from_structured(source, structured, backend=backend)
    from .trinket_slices import (
        LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
        TRINKET_SLICE_SOURCE_IDS,
    )

    if source.id in set(LEGACY_DEFAULT_TRINKET_SOURCE_IDS) | TRINKET_SLICE_SOURCE_IDS:
        from .hsreplay_bg_trinkets import fetch_battlegrounds_trinkets

        structured = await fetch_battlegrounds_trinkets(source)
        backend = structured.get("source", {}).get(
            "backend",
            "hsreplay_trinkets_api",
        )
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_arena_legendaries":
        from .hsreplay_legendaries_api import fetch_legendary_groups

        structured = await fetch_legendary_groups(source_id=source.id)
        backend = structured.get("source", {}).get("backend", "hsreplay_api")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_arena":
        from .hsreplay_arena_api import fetch_class_stats

        structured = await fetch_class_stats(source_id=source.id)
        backend = structured.get("source", {}).get("backend", "hsreplay_api")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_arena_class_pages_firecrawl":
        from .hsreplay_arena_classes_firecrawl import fetch_arena_class_pages_firecrawl

        structured = await fetch_arena_class_pages_firecrawl(source.id)
        backend = structured.get("source", {}).get("backend", "firecrawl+hsreplay_arena_api")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "hsreplay_arena_cards_advanced":
        from .hsreplay_arena_api import fetch_arena_card_tiers

        structured = await fetch_arena_card_tiers(source_id=source.id)
        backend = structured.get("source", {}).get("backend", "hsreplay_api")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id == "firestone_battlegrounds_comps":
        from .firestone_comps import fetch_firestone_comps

        structured = await fetch_firestone_comps(source)
        return _dataset_from_structured(source, structured, backend="firestone_api")
    if source.id == "firestone_standard":
        from .firestone_standard import fetch_firestone_standard

        structured = await fetch_firestone_standard(source)
        return _dataset_from_structured(source, structured, backend="firestone_api")
    if source.id == "firestone_battlegrounds_cards":
        from .firestone_comps import fetch_firestone_cards

        structured = await fetch_firestone_cards(source)
        return _dataset_from_structured(source, structured, backend="firestone_api")
    if source.id == "firestone_battlegrounds_spells":
        from .firestone_comps import fetch_firestone_cards

        structured = await fetch_firestone_cards(source)
        return _dataset_from_structured(source, structured, backend="firestone_api")
    if source.id in (
        "firestone_arena_cards_normal",
        "firestone_arena_cards_underground",
        "firestone_arena_legendaries_underground",
        "firestone_arena_legendaries_normal",
    ):
        from .firestone_comps import fetch_firestone_arena

        structured = await fetch_firestone_arena(source)
        return _dataset_from_structured(source, structured, backend="firestone_api")
    if source.id == "heartharena_tierlist":
        from .heartharena import fetch_heartharena_tierlist

        structured = await fetch_heartharena_tierlist(source)
        return _dataset_from_structured(source, structured, backend="heartharena_api")
    if source.id == "metastats_decks":
        from .metastats import fetch_metastats_decks

        structured = await fetch_metastats_decks(source)
        return _dataset_from_structured(source, structured, backend="metastats_api")
    if source.id == "metastats_matchups":
        from .metastats import fetch_metastats_matchups

        structured = await fetch_metastats_matchups(source)
        return _dataset_from_structured(source, structured, backend="metastats_api")
    if source.id == "hearthstone_decks":
        from .hearthstone_decks import fetch_hearthstone_decks

        structured = await fetch_hearthstone_decks(source)
        return _dataset_from_structured(source, structured, backend="hearthstone_decks_api")
    if source.id == "vicious_syndicate_radars":
        from .vicious_syndicate import fetch_vicious_syndicate_radars

        structured = await fetch_vicious_syndicate_radars(source)
        return _dataset_from_structured(source, structured, backend="vicious_syndicate_api")
    if source.id == "vicious_syndicate_live_beta":
        from .vicious_live import fetch_vicious_live

        structured = await fetch_vicious_live(source)
        return _dataset_from_structured(source, structured, backend="vicious_live_firebase")
    if source.id.startswith("hsreplay_cards_"):
        from .hsreplay_cards_api import fetch_hsreplay_ranked_cards

        structured = await fetch_hsreplay_ranked_cards(source)
        backend = structured.get("source", {}).get("backend", "hsreplay_cards_browser")
        return _dataset_from_structured(source, structured, backend=backend)
    if source.id in {
        "hsreplay_meta_archetypes_legend_eu_1d",
        "hsreplay_meta_top_1000_legend_1d_firecrawl",
        "hsreplay_meta_legend_1d_firecrawl",
        "hsreplay_meta_diamond_4to1_1d_firecrawl",
    }:
        from .hsreplay_meta_api import fetch_hsreplay_meta_archetypes

        structured = await fetch_hsreplay_meta_archetypes(source)
        backend = structured.get("source", {}).get("backend", "hsreplay_meta_api")
        return _dataset_from_structured(source, structured, backend=backend)
    return None


async def _fetch_source_with_active_lifecycle(
    client: httpx.AsyncClient | None,
    source: Source,
    retry_on_auth_failure: bool,
    *,
    started: float,
    fetched_at: str,
    publication_attempt: Any | None,
    previous: dict[str, Any],
    preferred_backend: str,
    source_tier: str,
    trace_id: str,
) -> dict[str, Any]:
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
    )

    def _finish(status: dict[str, Any]) -> dict[str, Any]:
        try:
            complete_source_trace(
                source.id,
                status,
                tier=source_tier,
                started_monotonic=started,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.debug(
                "Source completion telemetry failed for %s: %s",
                source.id,
                exc,
            )
        return status

    if source.id in firecrawl_primary_source_ids():
        firecrawl_status = await _try_firecrawl_html(
            source,
            fetched_at=fetched_at,
            reason="primary",
        )
        if firecrawl_status is not None:
            return _finish(firecrawl_status)

    if (
        tier_for(source.id) in API_FIRST_TIERS
        or source.id in API_FIRST_SOURCE_IDS
    ):
        log_action("api.route.begin", source_id=source.id, tier=source_tier)
        try:
            parsed = await _fetch_hsreplay_api_source(source)
            if parsed is not None:
                backend = str(parsed.pop("_backend", "hsreplay_api") or "hsreplay_api")
                transport_backend = (
                    _sanitize_transport_backend(parsed.pop("_transport_backend", None))
                    or backend
                )
                used_residential_proxy = _source_uses_residential_proxy(
                    source,
                    transport_backend,
                )
                content_length = parsed.get("counts", {}).get("api_bytes", 0)
                dataset = {
                    "source_id": source.id,
                    "fetched_at": fetched_at,
                    "data": parsed,
                    "backend": backend,
                    "transport_backend": transport_backend,
                    "content_length": content_length,
                    "used_residential_proxy": used_residential_proxy,
                    "runtime": runtime_version_info(),
                }
                publication_decision = None
                from .dataset_publication_store import (
                    STANDARD_CARDS_SOURCE_ID,
                    DatasetPublicationStore,
                    validate_and_publish_standard_cards_candidate,
                    validate_standard_cards_snapshot,
                )

                if source.id == STANDARD_CARDS_SOURCE_ID:
                    prepublication = validate_standard_cards_snapshot(source, dataset)
                    ok = prepublication.accepted
                    reason = prepublication.reason
                    gate_extra = prepublication.diagnostics
                else:
                    gate = validate_candidate_for_publish(source, parsed, backend=backend)
                    ok, reason = gate.ok, gate.reason
                    gate_extra = gate.extra
                qmetrics = quality_metrics(source, parsed)
                ai_telemetry: dict[str, Any] | None = None
                ai_quarantine = False
                if ok and ai_review_mode() == "quarantine":
                    ai_telemetry, ai_quarantine, ai_reason = (
                        await _review_candidate_with_ai(
                            source,
                            parsed,
                            backend=backend,
                            deterministic_reason=reason,
                            deterministic_extra=gate_extra,
                            quality=qmetrics,
                        )
                    )
                    if ai_quarantine:
                        ok = False
                        reason = ai_reason or "AI semantic review quarantined candidate"
                if source.id == STANDARD_CARDS_SOURCE_ID and not ai_quarantine:
                    publication_decision = validate_and_publish_standard_cards_candidate(
                        source,
                        dataset,
                        publication_attempt=publication_attempt,
                    )
                    ok = publication_decision.accepted
                    reason = publication_decision.reason
                    gate_extra = publication_decision.diagnostics
                if ok:
                    validation_log = {
                        "source_id": source.id,
                        "backend": backend,
                        "bytes_out": content_length,
                        "extra": {
                            "transport_backend": transport_backend,
                            "quality_metrics": qmetrics,
                            "ai_review": ai_telemetry,
                        },
                    }
                    if publication_decision is not None:
                        _best_effort_log_action("api.validate.ok", **validation_log)
                    else:
                        log_action("api.validate.ok", **validation_log)
                    if publication_decision is not None:
                        reg, reg_msg, provisional_metadata = False, None, {}
                    else:
                        reg, reg_msg, provisional_metadata = _save_dataset_with_checks(
                            source, dataset, fetched_at=fetched_at
                        )
                    if not reg and ai_review_mode() != "quarantine":
                        ai_telemetry, _, _ = await _review_candidate_with_ai(
                            source,
                            parsed,
                            backend=backend,
                            deterministic_reason=reason,
                            deterministic_extra=gate_extra,
                            quality=qmetrics,
                        )
                    ai_diagnosis = None
                    if reg:
                        regression_evidence, lkg = _regression_evidence_for_ai(
                            source,
                            parsed,
                            authoritative_reason=reg_msg,
                        )
                        ai_diagnosis = await _diagnose_candidate_with_ai(
                            source,
                            parsed,
                            backend=backend,
                            stage="regression_rejection",
                            deterministic_reason=reg_msg or "dataset regression",
                            deterministic_extra=None,
                            quality=qmetrics,
                            regression=regression_evidence,
                            lkg=lkg,
                            post_patch=provisional_metadata,
                        )
                    state = SourceState.PARTIAL if reg else SourceState.OK
                    status = _status_payload(
                        source,
                        state,
                        fetched_at=fetched_at,
                        http_status=200,
                        final_url=source.url,
                        content_length=content_length,
                        backend=backend,
                        transport_backend=transport_backend,
                        detail=reg_msg if reg else None,
                        used_residential_proxy=used_residential_proxy,
                        quality=qmetrics,
                    )
                    _attach_ai_review_status(status, ai_telemetry)
                    _attach_ai_diagnosis_status(status, ai_diagnosis)
                    _attach_provisional_status(status, provisional_metadata)
                    if reg:
                        status = _save_failure_status(source, status)
                    elif publication_decision is not None:
                        try:
                            reconciliation = DatasetPublicationStore().reconcile_current_publication(
                                source.id,
                                candidate_dataset_version=publication_decision.dataset_version,
                                expected_dataset_version=publication_decision.dataset_version,
                                status=status,
                                attempt_generation=(
                                    publication_attempt.generation
                                    if publication_attempt
                                    else None
                                ),
                                attempt_id=(
                                    publication_attempt.attempt_id
                                    if publication_attempt
                                    else None
                                ),
                                attempt_started_at=(
                                    publication_attempt.started_at
                                    if publication_attempt
                                    else None
                                ),
                            )
                            status = reconciliation.status
                        except Exception as exc:
                            sync_error = f"{type(exc).__name__}: {exc}"[:1000]
                            try:
                                authoritative_version = DatasetPublicationStore().pointer_dataset_version(
                                    source.id
                                )
                            except Exception:
                                authoritative_version = publication_decision.dataset_version
                            status.update(
                                {
                                    "state": SourceState.OK,
                                    "serving_cached_dataset": True,
                                    "effective_state": EFFECTIVE_OK_CACHED,
                                    "last_refresh_state": "cache_sync_error",
                                    "last_refresh_at": fetched_at,
                                    "last_refresh_error": sync_error,
                                    "candidate_dataset_version": publication_decision.dataset_version,
                                    "published_dataset_version": authoritative_version,
                                    "dataset_version": authoritative_version,
                                    "cache_synced": False,
                                    "status_synced": False,
                                    "warnings": [sync_error],
                                }
                            )
                            try:
                                log_action(
                                    "dataset.publication.sync.fail",
                                    source_id=source.id,
                                    state=SourceState.OK,
                                    backend=backend,
                                    level="error",
                                    detail=sync_error,
                                    extra={
                                        "published_dataset_version": authoritative_version,
                                        "operation": "fetcher",
                                        "transport_backend": transport_backend,
                                    },
                                )
                            except Exception:
                                pass
                    else:
                        save_status(source.id, status)
                    route_log = {
                        "source_id": source.id,
                        "state": state,
                        "backend": backend,
                        "tier": source_tier,
                        "bytes_out": content_length,
                        "extra": {
                            "transport_backend": transport_backend,
                            **provisional_metadata,
                        },
                    }
                    if publication_decision is not None:
                        _best_effort_log_action("api.route.ok", **route_log)
                    else:
                        log_action("api.route.ok", **route_log)
                    if reg and reg_msg:
                        await send_telegram_alert(
                            source.id, "dataset_regression", reg_msg, source.url
                        )
                    return _finish(status)
                if (
                    source.site == "hsreplay"
                    and retry_on_auth_failure
                    and _looks_like_hsreplay_auth_error(reason)
                ):
                    from .hsreplay_auth import (
                        force_relogin_hsreplay,
                        hsreplay_email,
                        hsreplay_password,
                    )

                    if hsreplay_email() and hsreplay_password() and await force_relogin_hsreplay():
                        return await fetch_source(client, source, retry_on_auth_failure=False)
                upstream_metadata: dict[str, Any] | None = None
                if source.id == "vicious_syndicate_radars":
                    from .vicious_syndicate import upstream_publication_metadata

                    structured = parsed.get("structured")
                    if isinstance(structured, dict):
                        upstream_metadata = upstream_publication_metadata(
                            structured,
                            semantic_issues=qmetrics.get("semantic_issues") or [],
                        )
                if (
                    upstream_metadata is None
                    and source.id in firecrawl_fallback_source_ids()
                ):
                    firecrawl_status = await _try_firecrawl_html(
                        source,
                        fetched_at=fetched_at,
                        reason=f"api_quality_error:{backend}",
                    )
                    if firecrawl_status is not None:
                        return _finish(firecrawl_status)
                rejected_state = (
                    SourceState.PARTIAL
                    if publication_decision is not None
                    and publication_decision.rejection_kind == "regression"
                    else SourceState.QUALITY_ERROR
                )
                rejection_is_regression = bool(
                    publication_decision is not None
                    and publication_decision.rejection_kind == "regression"
                )
                ai_diagnosis = None
                if not ai_quarantine and upstream_metadata is None:
                    ai_diagnosis = await _diagnose_candidate_with_ai(
                        source,
                        parsed,
                        backend=backend,
                        stage=(
                            "regression_rejection"
                            if rejection_is_regression
                            else "deterministic_rejection"
                        ),
                        deterministic_reason=reason,
                        deterministic_extra=gate_extra,
                        quality=qmetrics,
                        regression=(
                            {
                                "detected": True,
                                "reason_code": "row_count_drop",
                                "extra": gate_extra,
                            }
                            if rejection_is_regression
                            else None
                        ),
                        lkg=_published_data_for_ai(source.id),
                    )
                status = _status_payload(
                    source,
                    rejected_state,
                    fetched_at=fetched_at,
                    http_status=200,
                    final_url=source.url,
                    detail=reason,
                    content_length=content_length,
                    backend=backend,
                    transport_backend=transport_backend,
                    used_residential_proxy=used_residential_proxy,
                    quality=qmetrics,
                )
                _attach_ai_review_status(status, ai_telemetry)
                _attach_ai_diagnosis_status(status, ai_diagnosis)
                if upstream_metadata is not None:
                    status.update(upstream_metadata)
                if publication_decision is not None:
                    try:
                        reconciliation = DatasetPublicationStore().reconcile_current_publication(
                            source.id,
                            candidate_dataset_version=publication_decision.dataset_version,
                            expected_dataset_version=None,
                            status=status,
                            attempt_generation=(
                                publication_attempt.generation
                                if publication_attempt
                                else None
                            ),
                            attempt_id=(
                                publication_attempt.attempt_id
                                if publication_attempt
                                else None
                            ),
                            attempt_started_at=(
                                publication_attempt.started_at
                                if publication_attempt
                                else None
                            ),
                        )
                        status = reconciliation.status
                    except Exception:
                        status["candidate_dataset_version"] = (
                            publication_decision.dataset_version
                        )
                        status["published_dataset_version"] = None
                        status.pop("dataset_version", None)
                        status = DatasetPublicationStore().record_status_without_publication(
                            source.id,
                            status=status,
                            attempt_generation=(
                                publication_attempt.generation
                                if publication_attempt
                                else None
                            ),
                            attempt_id=(
                                publication_attempt.attempt_id
                                if publication_attempt
                                else None
                            ),
                            attempt_started_at=(
                                publication_attempt.started_at
                                if publication_attempt
                                else None
                            ),
                        )
                else:
                    status = _save_failure_status(source, status)
                log_action(
                    "api.validate.fail",
                    source_id=source.id,
                    state=rejected_state,
                    backend=backend,
                    detail=reason,
                    tier=source_tier,
                    level="warn",
                    extra={
                        "transport_backend": transport_backend,
                        "quality_metrics": qmetrics,
                        "publish_gate": gate_extra,
                        "ai_review": ai_telemetry,
                        "ai_diagnosis": ai_diagnosis,
                    },
                )
                if status.get("state") != SourceState.OK:
                    await send_telegram_alert(source.id, rejected_state, status["detail"], source.url)
                return _finish(status)
            log_action(
                "api.route.skip",
                source_id=source.id,
                detail="no API handler for this source_id",
                tier=source_tier,
                level="warn",
            )
        except Exception as exc:
            import logging

            api_detail = str(exc)[:2000]
            skip_browser_fallback = getattr(exc, "skip_browser_fallback", False) is True
            if isinstance(exc, ProxyPaymentRequiredError):
                # API collectors and browser fallbacks share one paid proxy.
                # A typed CONNECT rejection must stop browser backends from
                # repeating the same failing tunnel within this refresh.
                record_residential_proxy_failure(exc)
            if (
                source.site == "hsreplay"
                and retry_on_auth_failure
                and _looks_like_hsreplay_auth_error(api_detail)
            ):
                from .hsreplay_auth import (
                    force_relogin_hsreplay,
                    hsreplay_email,
                    hsreplay_password,
                )

                if hsreplay_email() and hsreplay_password() and await force_relogin_hsreplay():
                    return await fetch_source(client, source, retry_on_auth_failure=False)
            if not skip_browser_fallback:
                log_action(
                    "api.route.fail",
                    source_id=source.id,
                    state=SourceState.FETCH_ERROR,
                    error_type=type(exc).__name__,
                    detail=api_detail,
                    tier=source_tier,
                    level="error",
                )
            if skip_browser_fallback:
                transport_backend = _sanitize_transport_backend(
                    getattr(exc, "transport_backend", None)
                )
                status = _status_payload(
                    source,
                    SourceState.QUALITY_ERROR,
                    fetched_at=fetched_at,
                    error=type(exc).__name__,
                    detail=api_detail,
                    backend="vicious_syndicate_api",
                    transport_backend=transport_backend,
                    used_residential_proxy=(
                        "residential_httpx" in transport_backend
                        if transport_backend is not None
                        else None
                    ),
                )
                failure_reason = str(
                    getattr(exc, "failure_reason_code", "") or ""
                ).strip()
                if failure_reason:
                    status["failure_reason_code"] = failure_reason
                upstream_state = str(getattr(exc, "upstream_state", "") or "")
                if upstream_state:
                    status["upstream_state"] = upstream_state
                upstream_readiness = getattr(exc, "upstream_readiness", None)
                if isinstance(upstream_readiness, dict):
                    status["upstream_readiness"] = upstream_readiness
                status = _save_failure_status(source, status)
                log_action(
                    "api.fallback.skipped",
                    source_id=source.id,
                    state=SourceState.QUALITY_ERROR,
                    detail=api_detail,
                    tier=source_tier,
                    level="warn",
                    extra={"reason": "verified_upstream_publication_pending"},
                )
                return _finish(status)
            if blocks_browser_fallback(source.id):
                logging.getLogger(__name__).warning(
                    "API-only source %s failed (no browser fallback): %s",
                    source.id,
                    exc,
                )
                status = _status_payload(
                    source,
                    SourceState.FETCH_ERROR,
                    fetched_at=fetched_at,
                    error=type(exc).__name__,
                    detail=f"API fetch failed (browser fallback disabled): {api_detail}",
                )
                _attach_failure_class(status, exc)
                status = _save_failure_status(source, status)
                log_action(
                    "api.fallback.blocked",
                    source_id=source.id,
                    state=SourceState.FETCH_ERROR,
                    detail=api_detail,
                    tier=source_tier,
                    level="error",
                )
                if status.get("state") != SourceState.OK:
                    await send_telegram_alert(source.id, SourceState.FETCH_ERROR, status["detail"], source.url)
                    await _maybe_stale_data_alert(source, status)
                return _finish(status)
            logging.getLogger(__name__).warning(
                "API fetch failed for %s, falling back to browser: %s",
                source.id,
                exc,
            )
            log_action(
                "api.fallback.browser",
                source_id=source.id,
                detail=api_detail,
                tier=source_tier,
                level="warn",
            )

    set_flaresolverr_source(source.id)
    parsesunix_mode = "legacy"
    parsesunix_observation: dict[str, object] | None = None
    log_action(
        "browser.fetch.begin",
        source_id=source.id,
        tier=source_tier,
        extra={"preferred_backend": preferred_backend, "direct": fetch_direct_enabled()},
    )
    try:
        fetched = await _fetch_generic_html(
            client,
            source,
            preferred_backend=preferred_backend,
        )
        body = fetched.body
        http_status = fetched.http_status
        final_url = fetched.final_url
        backend = fetched.backend
        page_snapshot = fetched.page_snapshot
        parsesunix_mode = fetched.parsesunix_mode
        parsesunix_observation = fetched.parsesunix_observation
    except Exception as exc:
        if isinstance(exc, ParsesUnixTransportRejected):
            parsesunix_mode = "parsesunix"
            parsesunix_observation = exc.evidence.telemetry()
        log_action(
            "browser.fetch.end",
            source_id=source.id,
            state=SourceState.FETCH_ERROR,
            error_type=type(exc).__name__,
            detail=str(exc)[:2000],
            level="error",
        )
        firecrawl_status = None
        if _parsesunix_allows_paid_fallback(exc):
            firecrawl_status = await _try_firecrawl_html(
                source,
                fetched_at=fetched_at,
                reason=f"browser_exception:{type(exc).__name__}",
            )
        else:
            log_action(
                "parsesunix.paid_fallback.skipped",
                source_id=source.id,
                level="warn",
                detail="ParsesUnix verdict or integration failure does not authorize paid escalation",
                extra=parsesunix_observation,
            )
        if firecrawl_status is not None:
            _attach_parsesunix_observation(
                firecrawl_status,
                mode=parsesunix_mode,
                observation=parsesunix_observation,
                publication_validated=False,
            )
            return _finish(firecrawl_status)
        status = _status_payload(
            source,
            SourceState.FETCH_ERROR,
            fetched_at=fetched_at,
            error=type(exc).__name__,
            detail=str(exc)[:2000],
        )
        _attach_failure_class(status, exc)
        _attach_parsesunix_observation(
            status,
            mode=parsesunix_mode,
            observation=parsesunix_observation,
            publication_validated=False,
        )
        status = _save_failure_status(source, status)
        if status.get("state") != SourceState.OK:
            await send_telegram_alert(source.id, SourceState.FETCH_ERROR, status["detail"], source.url)
            await _maybe_stale_data_alert(source, status)
        return _finish(status)
    finally:
        set_flaresolverr_source(None)

    log_action(
        "browser.fetch.end",
        source_id=source.id,
        state=SourceState.OK,
        backend=backend,
        http_status=http_status,
        bytes_out=len(body.encode("utf-8", errors="replace")),
    )

    content_length = len(body.encode("utf-8", errors="replace"))
    if is_cloudflare_challenge(body):
        log_action(
            "protection.cloudflare",
            source_id=source.id,
            state=SourceState.BLOCKED_BY_PROTECTION,
            backend=backend,
            level="error",
        )
        firecrawl_status = await _try_firecrawl_html(
            source,
            fetched_at=fetched_at,
            reason="cloudflare_challenge",
        )
        if firecrawl_status is not None:
            _attach_parsesunix_observation(
                firecrawl_status,
                mode=parsesunix_mode,
                observation=parsesunix_observation,
                publication_validated=False,
            )
            return _finish(firecrawl_status)
        status = _status_payload(
            source,
            SourceState.BLOCKED_BY_PROTECTION,
            fetched_at=fetched_at,
            http_status=http_status,
            final_url=final_url,
            detail="Cloudflare challenge after all backends.",
            content_length=content_length,
            backend=backend,
            used_residential_proxy=_source_uses_residential_proxy(source, backend),
        )
        _attach_parsesunix_observation(
            status,
            mode=parsesunix_mode,
            observation=parsesunix_observation,
            publication_validated=False,
        )
        status = _save_failure_status(source, status)
        if status.get("state") != SourceState.OK:
            await send_telegram_alert(source.id, SourceState.BLOCKED_BY_PROTECTION, status["detail"], source.url)
        return _finish(status)

    if http_status >= 400:
        log_action(
            "http.status.error",
            source_id=source.id,
            http_status=http_status,
            backend=backend,
            level="error",
        )
        firecrawl_status = await _try_firecrawl_html(
            source,
            fetched_at=fetched_at,
            reason=f"http_status:{http_status}",
        )
        if firecrawl_status is not None:
            _attach_parsesunix_observation(
                firecrawl_status,
                mode=parsesunix_mode,
                observation=parsesunix_observation,
                publication_validated=False,
            )
            return _finish(firecrawl_status)
        status = _status_payload(
            source,
            SourceState.HTTP_ERROR,
            fetched_at=fetched_at,
            http_status=http_status,
            final_url=final_url,
            detail="HTTP error from origin",
            content_length=content_length,
            backend=backend,
            used_residential_proxy=_source_uses_residential_proxy(source, backend),
        )
        _attach_parsesunix_observation(
            status,
            mode=parsesunix_mode,
            observation=parsesunix_observation,
            publication_validated=False,
        )
        status = _save_failure_status(source, status)
        if status.get("state") != SourceState.OK:
            await send_telegram_alert(source.id, SourceState.HTTP_ERROR, status["detail"], source.url)
        return _finish(status)

    log_action("parse.html", source_id=source.id, backend=backend, bytes_out=content_length)
    parsed = parse_html(source, body, page_snapshot)
    gate = validate_candidate_for_publish(source, parsed, backend=backend)
    ok, reason = gate.ok, gate.reason
    qmetrics = quality_metrics(source, parsed)
    ai_telemetry: dict[str, Any] | None = None
    ai_quarantine = False
    if ok and ai_review_mode() == "quarantine":
        ai_telemetry, ai_quarantine, ai_reason = await _review_candidate_with_ai(
            source,
            parsed,
            backend=backend,
            deterministic_reason=reason,
            deterministic_extra=gate.extra,
            quality=qmetrics,
        )
        if ai_quarantine:
            ok = False
            reason = ai_reason or "AI semantic review quarantined candidate"
    if not ok:
        log_action(
            "quality.validate.fail",
            source_id=source.id,
            state=SourceState.QUALITY_ERROR,
            backend=backend,
            detail=reason,
            level="warn",
            extra={
                "quality_metrics": qmetrics,
                "publish_gate": gate.extra,
                "ai_review": ai_telemetry,
            },
        )
        is_auth_error = source.site == "hsreplay" and any(
            k in reason.lower() for k in ("session not authenticated", "premium data", "login required")
        )
        if is_auth_error and retry_on_auth_failure:
            from .hsreplay_auth import (
                force_relogin_hsreplay,
                hsreplay_email,
                hsreplay_password,
            )
            if hsreplay_email() and hsreplay_password():
                import logging
                logging.getLogger(__name__).warning(
                    "Detected invalid/expired HSReplay session for %s (%s). Forcing automatic relogin and retry...",
                    source.id,
                    reason,
                )
                log_action(
                    "auth.hsreplay.relogin",
                    source_id=source.id,
                    level="warn",
                    detail=reason,
                )
                relogin_success = await force_relogin_hsreplay()
                if relogin_success:
                    logging.getLogger(__name__).info("Relogin successful, retrying fetch for %s...", source.id)
                    return await fetch_source(client, source, retry_on_auth_failure=False)

        firecrawl_status = await _try_firecrawl_html(
            source,
            fetched_at=fetched_at,
            reason=f"quality_error:{reason[:120]}",
        )
        if firecrawl_status is not None:
            _attach_parsesunix_observation(
                firecrawl_status,
                mode=parsesunix_mode,
                observation=parsesunix_observation,
                publication_validated=False,
            )
            return _finish(firecrawl_status)

        ai_diagnosis = None
        if not ai_quarantine:
            ai_diagnosis = await _diagnose_candidate_with_ai(
                source,
                parsed,
                backend=backend,
                stage="deterministic_rejection",
                deterministic_reason=reason,
                deterministic_extra=gate.extra,
                quality=qmetrics,
                lkg=_published_data_for_ai(source.id),
            )

        status = _status_payload(
            source,
            SourceState.QUALITY_ERROR,
            fetched_at=fetched_at,
            http_status=http_status,
            final_url=final_url,
            detail=reason,
            content_length=content_length,
            backend=backend,
            used_residential_proxy=_source_uses_residential_proxy(source, backend),
        )
        _attach_ai_review_status(status, ai_telemetry)
        _attach_ai_diagnosis_status(status, ai_diagnosis)
        _attach_parsesunix_observation(
            status,
            mode=parsesunix_mode,
            observation=parsesunix_observation,
            publication_validated=False,
        )
        status = _save_failure_status(source, status)
        if status.get("state") != SourceState.OK:
            await send_telegram_alert(source.id, SourceState.QUALITY_ERROR, reason, source.url)
        return _finish(status)

    dataset = {
        "state": SourceState.OK,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "final_url": final_url,
        "content_length": content_length,
        "backend": backend,
        "used_residential_proxy": _source_uses_residential_proxy(source, backend),
        "data": parsed,
    }
    _attach_parsesunix_observation(
        dataset,
        mode=parsesunix_mode,
        observation=parsesunix_observation,
    )
    reg, reg_msg, provisional_metadata = _save_dataset_with_checks(
        source, dataset, fetched_at=fetched_at
    )
    if not reg and ai_review_mode() != "quarantine":
        ai_telemetry, _, _ = await _review_candidate_with_ai(
            source,
            parsed,
            backend=backend,
            deterministic_reason=reason,
            deterministic_extra=gate.extra,
            quality=qmetrics,
        )
    ai_diagnosis = None
    if reg:
        regression_evidence, lkg = _regression_evidence_for_ai(
            source,
            parsed,
            authoritative_reason=reg_msg,
        )
        ai_diagnosis = await _diagnose_candidate_with_ai(
            source,
            parsed,
            backend=backend,
            stage="regression_rejection",
            deterministic_reason=reg_msg or "dataset regression",
            deterministic_extra=None,
            quality=qmetrics,
            regression=regression_evidence,
            lkg=lkg,
            post_patch=provisional_metadata,
        )
    log_action(
        "quality.validate.ok",
        source_id=source.id,
        backend=backend,
        extra={
            "quality_metrics": qmetrics,
            "ai_review": ai_telemetry,
            **provisional_metadata,
        },
    )
    state = SourceState.PARTIAL if reg else SourceState.OK
    status = _status_payload(
        source,
        state,
        fetched_at=fetched_at,
        http_status=http_status,
        final_url=final_url,
        content_length=content_length,
        backend=backend,
        detail=reg_msg if reg else None,
        used_residential_proxy=_source_uses_residential_proxy(source, backend),
        quality=qmetrics,
    )
    _attach_ai_review_status(status, ai_telemetry)
    _attach_ai_diagnosis_status(status, ai_diagnosis)
    _attach_provisional_status(status, provisional_metadata)
    _attach_parsesunix_observation(
        status,
        mode=parsesunix_mode,
        observation=parsesunix_observation,
        publication_validated=not reg,
    )
    if reg:
        status = _save_failure_status(source, status)
    else:
        save_status(source.id, status)
    if reg and reg_msg:
        await send_telegram_alert(source.id, "dataset_regression", reg_msg, source.url)
    return _finish(status)


async def _fetch_source_with_captured_policy(
    client: httpx.AsyncClient | None,
    source: Source,
    retry_on_auth_failure: bool = True,
) -> dict[str, Any]:
    """Own every per-fetch ContextVar/trace token for the whole lifecycle."""

    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
    )
    from .scrapers.preferred_backend import preferred_browser_backend

    started = time.monotonic()
    fetched_at = now_iso()
    previous_attempt_context = _standard_publication_attempt.get()
    publication_attempt = None
    publication_attempt_token = None
    trace_tokens = None
    trace_id = ""
    try:
        if source.id == STANDARD_CARDS_SOURCE_ID:
            publication_attempt = DatasetPublicationStore().begin_publication_attempt(
                source.id
            )
        publication_attempt_token = _standard_publication_attempt.set(
            publication_attempt
        )
        try:
            previous = load_status(source.id) or {}
        except (OSError, UnicodeError, ValueError):
            if source.id != STANDARD_CARDS_SOURCE_ID:
                raise
            # A damaged mutable status is telemetry/cache metadata, never a
            # reason to abort a fully validated durable publication attempt.
            previous = {}

        preferred_backend = preferred_browser_backend(previous)
        source_tier = tier_for(source.id).value
        trace_id, tok_trace, tok_source, tok_step = activate_source_trace(
            source.id,
            tier=source_tier,
            url=source.fetch_url,
        )
        trace_tokens = (tok_trace, tok_source, tok_step)
        return await _fetch_source_with_active_lifecycle(
            client,
            source,
            retry_on_auth_failure,
            started=started,
            fetched_at=fetched_at,
            publication_attempt=publication_attempt,
            previous=previous,
            preferred_backend=preferred_backend,
            source_tier=source_tier,
            trace_id=trace_id,
        )
    finally:
        if trace_tokens is not None:
            try:
                deactivate_source_trace(trace_tokens)
            except Exception as exc:
                logger.debug(
                    "Source trace cleanup failed for %s: %s",
                    source.id,
                    exc,
                )
        if publication_attempt_token is not None:
            try:
                _standard_publication_attempt.reset(publication_attempt_token)
            except Exception:
                # A reset should only fail after cross-context misuse. Restore
                # the entry value explicitly so the next fetch cannot inherit
                # this attempt even under injected telemetry failures.
                _standard_publication_attempt.set(previous_attempt_context)


async def fetch_source(
    client: httpx.AsyncClient | None,
    source: Source,
    retry_on_auth_failure: bool = True,
) -> dict[str, Any]:
    # A single fetch validates and publishes against one immutable policy
    # context. If an admin switches Early -> Stable before the write, the save
    # gate detects the token change and leaves the stable channel untouched.
    with capture_publication_policy(source.id):
        from .hsguru_post_patch import source_for_active_post_patch

        source = source_for_active_post_patch(source)
        return await _fetch_source_with_captured_policy(
            client,
            source,
            retry_on_auth_failure=retry_on_auth_failure,
        )


def _attach_proxy_egress(status: dict[str, Any], proxy_info: dict[str, str]) -> dict[str, Any]:
    if proxy_info and status.get("state") == SourceState.OK and status.get("used_residential_proxy"):
        status["proxy_egress_ip"] = proxy_info.get("egress_ip")
    return status


def _refresh_outcome_counts(
    results: list[dict[str, Any]],
) -> dict[str, int]:
    """Classify live publication separately from LKG availability.

    A cached dataset keeps the API serviceable, but it is not a successful
    parsing attempt. Keeping the buckets mutually exclusive makes both logs
    and dashboards report the live success percentage honestly.
    """

    counts = {
        "fresh_ok": 0,
        "provisional": 0,
        "cached_lkg": 0,
        "skipped": 0,
        "fail": 0,
    }
    for status in results:
        state = str(status.get("state") or "").strip().lower()
        if status.get("skipped") is True or state in {"locked", "skipped"}:
            counts["skipped"] += 1
        elif status.get("serving_cached_dataset") is True:
            counts["cached_lkg"] += 1
        elif state == SourceState.OK and status.get("provisional") is True:
            counts["provisional"] += 1
        elif state == SourceState.OK:
            counts["fresh_ok"] += 1
        else:
            counts["fail"] += 1

    counts["ok"] = counts["fresh_ok"] + counts["provisional"]
    counts["available"] = counts["ok"] + counts["cached_lkg"]
    counts["total"] = len(results)
    return counts


def _refresh_traffic_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Best-effort refresh traffic lower bound from final source statuses.

    This measures response bodies that reached parser code. Provider billing can be
    higher because proxy plans count protocol overhead, redirects, retries, and
    browser assets that are not represented by a final dataset/status body.
    """
    body_bytes = 0
    iproyal_marked_bytes = 0
    by_tier: Counter[str] = Counter()
    by_backend: Counter[str] = Counter()
    by_site: Counter[str] = Counter()
    top_sources: list[dict[str, Any]] = []
    skipped_cached_sources = 0

    for status in results:
        if status.get("serving_cached_dataset"):
            skipped_cached_sources += 1
            continue
        raw_bytes = status.get("content_length")
        if not isinstance(raw_bytes, int) or raw_bytes <= 0:
            continue

        source_id = str(status.get("source_id") or "")
        site = str(status.get("site") or "unknown")
        backend = str(status.get("backend") or "unknown")
        try:
            tier = tier_for(source_id).value
        except KeyError:
            tier = "unknown"

        body_bytes += raw_bytes
        by_tier[tier] += raw_bytes
        by_backend[backend] += raw_bytes
        by_site[site] += raw_bytes
        if status.get("used_residential_proxy"):
            iproyal_marked_bytes += raw_bytes
        top_sources.append(
            {
                "source_id": source_id,
                "site": site,
                "tier": tier,
                "backend": backend,
                "body_bytes": raw_bytes,
                "body_mb": round(raw_bytes / 1024 / 1024, 3),
                "iproyal_marked": bool(status.get("used_residential_proxy")),
            }
        )

    top_sources.sort(key=lambda item: int(item["body_bytes"]), reverse=True)

    def _mb(value: int) -> float:
        return round(value / 1024 / 1024, 3)

    def _counter_mb(counter: Counter[str]) -> dict[str, float]:
        return {key: _mb(value) for key, value in counter.most_common()}

    return {
        "body_bytes_lower_bound": body_bytes,
        "body_mb_lower_bound": _mb(body_bytes),
        "iproyal_body_bytes_estimate": iproyal_marked_bytes,
        "iproyal_body_mb_estimate": _mb(iproyal_marked_bytes),
        "sources_with_body": len(top_sources),
        "iproyal_marked_sources": sum(1 for item in top_sources if item["iproyal_marked"]),
        "skipped_cached_sources": skipped_cached_sources,
        "by_tier_mb": _counter_mb(by_tier),
        "by_backend_mb": _counter_mb(by_backend),
        "by_site_mb": _counter_mb(by_site),
        "top_sources": top_sources[:15],
        "billing_exact": False,
        "note": (
            "Lower bound from live final response bodies. IPRoyal estimate includes only statuses "
            "marked used_residential_proxy=true. Billing can be higher because retries, redirects, "
            "headers, TLS/HTTP overhead, and browser assets are not counted here."
        ),
    }


def _record_reliability_results_best_effort(
    run_id: str,
    results: list[dict[str, Any]],
    *,
    coverage_scope: str = "partial",
    expected_source_count: int | None = None,
    refresh_window_id: str | None = None,
) -> None:
    """Persist aggregate telemetry without changing a parser outcome."""

    if not results:
        return
    try:
        from .reliability_telemetry import record_terminal_results

        if refresh_window_id is None:
            record_terminal_results(
                run_id,
                results,
                coverage_scope=coverage_scope,
                expected_source_count=expected_source_count,
            )
        else:
            record_terminal_results(
                run_id,
                results,
                coverage_scope=coverage_scope,
                expected_source_count=expected_source_count,
                refresh_window_id=refresh_window_id,
            )
    except Exception as exc:  # noqa: BLE001 - telemetry cannot invert parser outcomes
        logger.warning(
            "Reliability telemetry write failed for run %s: %s",
            run_id,
            type(exc).__name__,
        )


async def _browser_inter_source_delay() -> None:
    delay_seconds = request_delay_seconds()
    await asyncio.sleep(delay_seconds * random.uniform(0.75, 1.25))


async def _parallel_stagger_delay() -> None:
    await asyncio.sleep(
        random.uniform(refresh_parallel_stagger_min(), refresh_parallel_stagger_max())
    )


async def _inter_request_cooldown() -> None:
    """Short pause between parallel tier batches to ease proxy load."""
    await asyncio.sleep(random.uniform(1.0, 2.5))


async def _run_tier_after_cooldown(coro):
    await _inter_request_cooldown()
    return await coro


def _fetch_error_status(source: Source, exc: BaseException) -> dict[str, Any]:
    state = SourceState.TIMED_OUT if isinstance(exc, TimeoutError) else SourceState.FETCH_ERROR
    status = _status_payload(
        source,
        state,
        fetched_at=now_iso(),
        error=type(exc).__name__,
        detail=str(exc)[:2000],
    )
    return _attach_failure_class(status, exc)


def _status_reports_proxy_failure(status: dict[str, Any]) -> bool:
    raw_status = status.get("proxy_status") or status.get(
        "last_refresh_proxy_status"
    )
    try:
        if int(raw_status) in {402, 407}:
            return True
    except (TypeError, ValueError):
        pass
    return any(
        status.get(field) in {"proxy_402", "proxy_407"}
        for field in ("failure_class", "last_refresh_failure_class")
    )


async def _run_tier_parallel(
    sources: list[Source],
    *,
    phase: str,
    concurrency: int,
    client: httpx.AsyncClient | None,
    proxy_info: dict[str, str],
    proxy_circuit: _RefreshProxyCircuit | None = None,
) -> list[dict[str, Any]]:
    if not sources:
        return []

    logger = logging.getLogger(__name__)
    started = time.monotonic()
    semaphore = asyncio.Semaphore(concurrency)
    circuit = proxy_circuit or _RefreshProxyCircuit()
    sync_rotator_circuit = proxy_circuit is not None

    async def fetch_one(source: Source) -> dict[str, Any]:
        async with semaphore:
            await _parallel_stagger_delay()
            set_refresh_context(phase=phase)
            if (
                circuit.is_open
                and not source_can_run_without_residential_proxy(source)
            ):
                assert circuit.error is not None
                status = _fetch_error_status(
                    source,
                    circuit.error,
                )
                status = _save_failure_status(source, status)
                log_action(
                    "proxy.source.skip",
                    level="warn",
                    source_id=source.id,
                    detail="source has no configured proxyless route",
                    extra={
                        "phase": phase,
                        "failure_class": f"proxy_{circuit.error.status_code}",
                        "proxy_status": circuit.error.status_code,
                    },
                )
                return _attach_proxy_egress(status, proxy_info)
            try:
                status = await fetch_source(client, source)
            except ProxyPaymentRequiredError as exc:
                circuit.open(exc)
                logger.error(
                    "Proxy CONNECT %s for %s; independent sources continue: %s",
                    exc.status_code,
                    source.id,
                    exc,
                )
                log_action(
                    "proxy.health.fail",
                    level="error",
                    detail=str(exc)[:500],
                    source_id=source.id,
                    extra={
                        "phase": phase,
                        "phase_abort": False,
                        "proxy_status": exc.status_code,
                    },
                )
                status = _save_failure_status(source, _fetch_error_status(source, exc))
            except Exception as exc:
                logger.exception("Parallel fetch failed for %s", source.id)
                status = _fetch_error_status(source, exc)
            circuit.open_from_status(status)
            if sync_rotator_circuit:
                from .scrapers.rotator import residential_proxy_circuit_error

                rotator_error = residential_proxy_circuit_error()
                if rotator_error is not None:
                    circuit.open(rotator_error)
            return _attach_proxy_egress(status, proxy_info)

    raw = await asyncio.gather(*(fetch_one(source) for source in sources), return_exceptions=True)
    results: list[dict[str, Any]] = []
    for source, item in zip(sources, raw, strict=True):
        if isinstance(item, BaseException):
            logger.exception("Parallel gather failed for %s", source.id)
            results.append(_fetch_error_status(source, item))
        else:
            results.append(item)

    outcome_counts = _refresh_outcome_counts(results)
    logger.info(
        "refresh phase=%s duration=%.1fs fresh=%d provisional=%d cached=%d fail=%d skipped=%d concurrency=%d",
        phase,
        time.monotonic() - started,
        outcome_counts["fresh_ok"],
        outcome_counts["provisional"],
        outcome_counts["cached_lkg"],
        outcome_counts["fail"],
        outcome_counts["skipped"],
        concurrency,
    )
    return results


async def _run_tier_serial_browser(
    sources: list[Source],
    *,
    phase: str,
    client: httpx.AsyncClient | None,
    proxy_info: dict[str, str],
    use_flaresolverr: bool,
    apply_delay: bool,
    proxy_circuit: _RefreshProxyCircuit | None = None,
) -> list[dict[str, Any]]:
    if not sources:
        return []

    logger = logging.getLogger(__name__)
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    circuit = proxy_circuit or _RefreshProxyCircuit()
    sync_rotator_circuit = proxy_circuit is not None
    fs_session: FlareSolverrSession | None = None
    try:
        for source in sources:
            try:
                if (
                    circuit.is_open
                    and not source_can_run_without_residential_proxy(source)
                ):
                    assert circuit.error is not None
                    status = _save_failure_status(
                        source,
                        _fetch_error_status(source, circuit.error),
                    )
                    log_action(
                        "proxy.source.skip",
                        level="warn",
                        source_id=source.id,
                        detail="source has no configured proxyless route",
                        extra={
                            "phase": phase,
                            "failure_class": f"proxy_{circuit.error.status_code}",
                            "proxy_status": circuit.error.status_code,
                        },
                    )
                    results.append(_attach_proxy_egress(status, proxy_info))
                    if apply_delay:
                        await _browser_inter_source_delay()
                    continue
                if use_flaresolverr and not fetch_direct_enabled():
                    if flaresolverr_session_per_source() or fs_session is None:
                        if fs_session is not None:
                            previous_session = fs_session
                            fs_session = None
                            set_active_flaresolverr_session(None)
                            await previous_session.__aexit__(None, None, None)
                        candidate_session = FlareSolverrSession()
                        await candidate_session.__aenter__()
                        fs_session = candidate_session
                        set_active_flaresolverr_session(fs_session)
                status = await fetch_source(client, source)
            except ProxyPaymentRequiredError as exc:
                circuit.open(exc)
                logger.error(
                    "Proxy CONNECT %s for %s; independent sources continue: %s",
                    exc.status_code,
                    source.id,
                    exc,
                )
                status = _save_failure_status(
                    source,
                    _fetch_error_status(source, exc),
                )
            except Exception as exc:
                logger.exception("Serial browser fetch failed for %s", source.id)
                status = _save_failure_status(source, _fetch_error_status(source, exc))
            circuit.open_from_status(status)
            if sync_rotator_circuit:
                from .scrapers.rotator import residential_proxy_circuit_error

                rotator_error = residential_proxy_circuit_error()
                if rotator_error is not None:
                    circuit.open(rotator_error)
            results.append(_attach_proxy_egress(status, proxy_info))
            if apply_delay:
                await _browser_inter_source_delay()
    finally:
        if fs_session is not None:
            set_active_flaresolverr_session(None)
            try:
                await fs_session.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning(
                    "FlareSolverr session cleanup failed after %s: %s",
                    phase,
                    type(exc).__name__,
                )

    outcome_counts = _refresh_outcome_counts(results)
    logger.info(
        "refresh phase=%s duration=%.1fs fresh=%d provisional=%d cached=%d fail=%d skipped=%d concurrency=1",
        phase,
        time.monotonic() - started,
        outcome_counts["fresh_ok"],
        outcome_counts["provisional"],
        outcome_counts["cached_lkg"],
        outcome_counts["fail"],
        outcome_counts["skipped"],
    )
    return results


async def _refresh_sources_unlocked(
    source_ids: list[str] | None = None,
    *,
    tier_filter: str | None = None,
    respect_section_controls: bool = False,
    refresh_window_id: str | None = None,
) -> list[dict[str, Any]]:
    global _firecrawl_fallback_attempts
    _firecrawl_fallback_attempts = 0
    _firecrawl_fallback_attempts_by_source.clear()
    validate_tier_registry()
    from .ai_review import reset_ai_review_budget
    from .hsreplay_client import reset_hsreplay_refresh_state
    from .refresh_context import begin_refresh_run, end_refresh_run
    from .scrapers.rotator import reset_backend_circuits

    begin_refresh_run()
    reset_ai_review_budget()
    reset_hsreplay_refresh_state()
    reset_backend_circuits()
    run_id = new_run_id()
    log_action(
        "refresh.begin",
        extra={
            "source_ids": source_ids,
            "run_id": run_id,
            "tier_filter": tier_filter,
            "runtime": runtime_version_info(),
        },
    )

    canonical_scrape_source_ids = {
        source.id
        for source in SOURCES
        if source.kind == "scrape" and source_operationally_enabled(source.id)
    }
    selected = [
        source for source in SOURCES if source_operationally_enabled(source.id)
    ]
    if source_ids:
        selected = [SOURCE_BY_ID[source_id] for source_id in source_ids]

    if respect_section_controls:
        from .parser_control import filter_scheduled_source_ids

        enabled_source_ids = set(
            filter_scheduled_source_ids([source.id for source in selected])
        )
        disabled = [
            source.id for source in selected
            if source.id not in enabled_source_ids
        ]
        selected = [
            source for source in selected
            if source.id in enabled_source_ids
        ]
        if disabled:
            log_action(
                "refresh.skip_disabled_sections",
                level="info",
                detail="Sources disabled by parser control",
                extra={"source_ids": disabled, "run_id": run_id},
            )

    # kind="pipeline" sources are refreshed by dedicated systemd timers and
    # must never enter the scrape planner (they also have no tier mapping).
    pipeline_skipped = [s.id for s in selected if s.kind != "scrape"]
    if pipeline_skipped:
        selected = [s for s in selected if s.kind == "scrape"]
        log_action(
            "refresh.skip_pipeline_sources",
            level="warn" if source_ids else "info",
            detail="pipeline sources are refreshed by their dedicated timers/commands",
            extra={"source_ids": pipeline_skipped, "run_id": run_id},
        )

    if tier_filter:
        tier_enum = SourceTier(tier_filter)
        selected = [s for s in selected if tier_for(s.id) == tier_enum]

    selected_source_ids = {source.id for source in selected}
    full_refresh = bool(canonical_scrape_source_ids) and (
        source_ids is None
        and tier_filter is None
        and selected_source_ids == canonical_scrape_source_ids
    )
    reliability_expected_source_count = (
        len(canonical_scrape_source_ids) if full_refresh else len(selected)
    )

    def record_reliability(terminal_results: list[dict[str, Any]]) -> None:
        if refresh_window_id is None:
            _record_reliability_results_best_effort(
                run_id,
                terminal_results,
                coverage_scope="full" if full_refresh else "partial",
                expected_source_count=reliability_expected_source_count,
            )
        else:
            _record_reliability_results_best_effort(
                run_id,
                terminal_results,
                coverage_scope="full" if full_refresh else "partial",
                expected_source_count=reliability_expected_source_count,
                refresh_window_id=refresh_window_id,
            )

    parts_preview = partition_sources(selected)
    backends_lower_preview = [b.lower() for b in fetch_backends()]
    _begin_deferred_ai_collection()
    from .preflight import (
        ensure_refresh_preflight,
        selection_needs_flaresolverr_preflight,
        selection_needs_proxy_preflight,
    )

    try:
        proxy_info = await ensure_refresh_preflight(
            full_refresh=full_refresh,
            needs_proxy=selection_needs_proxy_preflight(
                selected,
                configured_backends=backends_lower_preview,
            ),
            needs_flaresolverr=selection_needs_flaresolverr_preflight(
                selected,
                configured_backends=backends_lower_preview,
            ),
        )
    except Exception as exc:
        terminal_state = (
            SourceState.TIMED_OUT
            if isinstance(exc, TimeoutError)
            else SourceState.FETCH_ERROR
        )
        terminal_results = [
            {
                "source_id": source.id,
                "state": terminal_state,
                "failure_reason_code": "preflight",
            }
            for source in selected
        ]
        record_reliability(terminal_results)
        try:
            _best_effort_log_action(
                "refresh.end",
                state=SourceState.FETCH_ERROR,
                level="error",
                error_type=type(exc).__name__,
                detail=str(exc)[:500],
                extra={
                    "ok": 0,
                    "fail": len(selected),
                    "run_id": run_id,
                    "phase": "preflight",
                },
            )
            try:
                await _diagnose_refresh_failure_with_ai(
                    selected,
                    phase="preflight",
                    backend="preflight",
                )
                await _flush_deferred_ai_jobs(
                    run_id,
                    terminal_results,
                    enqueue_terminal_failures=False,
                    persist_statuses=False,
                )
            except Exception as ai_exc:  # noqa: BLE001 - retain original failure
                logger.warning(
                    "Refresh-level AI diagnosis failed after preflight: %s",
                    type(ai_exc).__name__,
                )
        finally:
            end_refresh_run()
        raise

    if tier_filter is None and parts_preview.light_api:
        from .cards_index import prefetch_hearthstonejson_async

        try:
            await prefetch_hearthstonejson_async()
        except Exception as exc:
            terminal_state = (
                SourceState.TIMED_OUT
                if isinstance(exc, TimeoutError)
                else SourceState.FETCH_ERROR
            )
            terminal_results = [
                {
                    "source_id": source.id,
                    "state": terminal_state,
                    "failure_reason_code": "dependency",
                }
                for source in selected
            ]
            record_reliability(terminal_results)
            try:
                _best_effort_log_action(
                    "refresh.end",
                    state=terminal_state,
                    level="error",
                    error_type=type(exc).__name__,
                    detail=str(exc)[:500],
                    extra={
                        "ok": 0,
                        "fail": len(selected),
                        "run_id": run_id,
                        "phase": "prefetch_hearthstonejson",
                    },
                )
                try:
                    await _diagnose_refresh_failure_with_ai(
                        selected,
                        phase="dependency",
                        backend="hearthstonejson",
                    )
                    await _flush_deferred_ai_jobs(
                        run_id,
                        terminal_results,
                        enqueue_terminal_failures=False,
                        persist_statuses=False,
                    )
                except Exception as ai_exc:  # noqa: BLE001 - retain original failure
                    logger.warning(
                        "Refresh-level AI diagnosis failed after dependency error: %s",
                        type(ai_exc).__name__,
                    )
            finally:
                end_refresh_run()
            raise

    client: httpx.AsyncClient | None = None
    if fetch_direct_enabled():
        headers = {
            "User-Agent": user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        }
        timeout = httpx.Timeout(request_timeout_seconds())
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
        client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            limits=limits,
            http2=True,
        )

    parts = partition_sources(selected)
    backends_lower = [b.lower() for b in fetch_backends()]
    browser_tiers = bool(parts.browser_patchright or parts.browser_protected)
    use_patchright = bool(parts.browser_patchright) and not fetch_direct_enabled() and (
        "patchright" in backends_lower or "playwright" in backends_lower
    )
    use_cloakbrowser = browser_tiers and not fetch_direct_enabled() and (
        "cloakbrowser" in backends_lower
    )
    use_flaresolverr = bool(parts.browser_protected) and not fetch_direct_enabled() and (
        "flaresolverr" in backends_lower
    )
    browser_delay = refresh_delay_browser_only()
    proxy_circuit = _RefreshProxyCircuit()

    results: list[dict[str, Any]] = []
    phase_error: BaseException | None = None
    try:
        phase_plan = (
            (
                SourceTier.LIGHT_API.value,
                lambda: _run_tier_parallel(
                    parts.light_api,
                    phase=SourceTier.LIGHT_API.value,
                    concurrency=refresh_parallel_light(),
                    client=client,
                    proxy_info=proxy_info,
                    proxy_circuit=proxy_circuit,
                ),
            ),
            (
                SourceTier.MEDIUM_API.value,
                lambda: _run_tier_after_cooldown(
                    _run_tier_parallel(
                        parts.medium_api,
                        phase=SourceTier.MEDIUM_API.value,
                        concurrency=refresh_parallel_medium(),
                        client=client,
                        proxy_info=proxy_info,
                        proxy_circuit=proxy_circuit,
                    )
                ),
            ),
            (
                SourceTier.BROWSER_PATCHRIGHT.value,
                lambda: _run_tier_after_cooldown(
                    _run_browser_phase(
                        parts.browser_patchright,
                        phase=SourceTier.BROWSER_PATCHRIGHT.value,
                        client=client,
                        proxy_info=proxy_info,
                        use_flaresolverr=False,
                        apply_delay=browser_delay,
                        use_patchright=use_patchright,
                        proxy_circuit=proxy_circuit,
                    )
                ),
            ),
            (
                SourceTier.BROWSER_PROTECTED.value,
                lambda: _run_browser_phase(
                    parts.browser_protected,
                    phase=SourceTier.BROWSER_PROTECTED.value,
                    client=client,
                    proxy_info=proxy_info,
                    use_flaresolverr=use_flaresolverr,
                    apply_delay=browser_delay,
                    use_patchright=use_patchright,
                    proxy_circuit=proxy_circuit,
                ),
            ),
        )
        for phase_name, phase_factory in phase_plan:
            if tier_filter and phase_name != tier_filter:
                continue
            set_refresh_context(phase=phase_name, run_id=run_id)
            phase_started = time.monotonic()
            log_action("phase.begin", extra={"phase": phase_name})
            phase_results = await phase_factory()
            results.extend(phase_results)
            outcome_counts = _refresh_outcome_counts(phase_results)
            phase_degraded = bool(
                outcome_counts["cached_lkg"]
                or outcome_counts["skipped"]
                or outcome_counts["fail"]
            )
            log_action(
                "phase.end",
                state=SourceState.PARTIAL if phase_degraded else SourceState.OK,
                duration_ms=(time.monotonic() - phase_started) * 1000,
                level="warn" if phase_degraded else "info",
                extra={
                    "phase": phase_name,
                    **outcome_counts,
                },
            )
    except BaseException as exc:
        phase_error = exc
        raise
    finally:
        completed_source_ids = {
            str(result.get("source_id") or "") for result in results
        }
        missing_state = (
            SourceState.TIMED_OUT
            if isinstance(phase_error, TimeoutError)
            else SourceState.FETCH_ERROR
        )
        terminal_results = [
            *results,
            *(
                {
                    "source_id": source.id,
                    "state": missing_state,
                }
                for source in selected
                if source.id not in completed_source_ids
            ),
        ]
        record_reliability(terminal_results)
        if use_patchright or use_flaresolverr:
            await PatchrightPool.shutdown()
        if use_cloakbrowser:
            from .scrapers.cloakbrowser_pool import shutdown_cloakbrowser_pool

            await shutdown_cloakbrowser_pool()
        if client is not None:
            await client.aclose()
        end_refresh_run()
        outcome_counts = _refresh_outcome_counts(terminal_results)
        refresh_degraded = bool(
            outcome_counts["cached_lkg"]
            or outcome_counts["skipped"]
            or outcome_counts["fail"]
        )
        traffic = _refresh_traffic_summary(terminal_results)
        log_action(
            "refresh.end",
            state=SourceState.PARTIAL if refresh_degraded else SourceState.OK,
            level="warn" if refresh_degraded else "info",
            extra={
                **outcome_counts,
                "run_id": run_id,
                "traffic": traffic,
            },
        )
        if full_refresh:
            from .stale_monitor import alert_stale_sources

            try:
                stale_sent = await alert_stale_sources()
                if stale_sent:
                    log_action(
                        "refresh.stale_alerts",
                        level="warn",
                        extra={"count": stale_sent},
                    )
            except Exception as exc:
                logger.warning("Stale source alerts failed: %s", exc)
        await _flush_deferred_ai_jobs(run_id, terminal_results)
    return results


async def refresh_sources(
    source_ids: list[str] | None = None,
    *,
    tier: str | None = None,
    respect_section_controls: bool = False,
    refresh_window_id: str | None = None,
) -> list[dict[str, Any]]:
    lock_source_ids = _refresh_lock_source_ids(source_ids, tier=tier)
    available_source_ids: list[str] = []
    locked_outcomes: list[dict[str, Any]] = []
    with ExitStack() as acquired_locks:
        for source_id in lock_source_ids:
            try:
                acquired_locks.enter_context(
                    ResourceLockSet([source_id], metadata={"operation": "refresh"})
                )
            except ResourceLocked as exc:
                locked_outcomes.append({"source_id": source_id, **exc.as_outcome()})
            else:
                available_source_ids.append(source_id)

        if locked_outcomes:
            if refresh_window_id is None:
                _record_reliability_results_best_effort(
                    f"locked-{uuid.uuid4().hex}",
                    locked_outcomes,
                )
            else:
                _record_reliability_results_best_effort(
                    f"locked-{uuid.uuid4().hex}",
                    locked_outcomes,
                    refresh_window_id=refresh_window_id,
                )

        if not available_source_ids:
            return locked_outcomes

        refresh_source_ids = source_ids
        if locked_outcomes:
            refresh_source_ids = available_source_ids
        if refresh_window_id is None:
            refreshed = await _refresh_sources_unlocked(
                refresh_source_ids,
                tier_filter=tier,
                respect_section_controls=respect_section_controls,
            )
        else:
            refreshed = await _refresh_sources_unlocked(
                refresh_source_ids,
                tier_filter=tier,
                respect_section_controls=respect_section_controls,
                refresh_window_id=refresh_window_id,
            )
        return [*refreshed, *locked_outcomes]


def _refresh_lock_source_ids(
    source_ids: list[str] | None,
    *,
    tier: str | None,
) -> list[str]:
    if source_ids:
        return sorted(set(source_ids))
    selected = [source for source in SOURCES if source.kind == "scrape"]
    if tier is not None:
        tier_filter = SourceTier(tier)
        selected = [source for source in selected if tier_for(source.id) == tier_filter]
    return sorted(source.id for source in selected)


async def _run_browser_phase(
    sources: list[Source],
    *,
    phase: str,
    client: httpx.AsyncClient | None,
    proxy_info: dict[str, str],
    use_flaresolverr: bool,
    apply_delay: bool,
    use_patchright: bool,
    proxy_circuit: _RefreshProxyCircuit | None = None,
) -> list[dict[str, Any]]:
    if use_patchright:
        await PatchrightPool.get()
    return await _run_tier_serial_browser(
        sources,
        phase=phase,
        client=client,
        proxy_info=proxy_info,
        use_flaresolverr=use_flaresolverr,
        apply_delay=apply_delay,
        proxy_circuit=proxy_circuit,
    )
