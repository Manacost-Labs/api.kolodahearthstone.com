from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..parser_control import load_resolved_public_dataset
from ..reliability_telemetry import build_reliability_report
from ..sources import SOURCES
from ..storage import load_status
from .models import ApiMeta, Envelope, freshest_timestamp, timestamp_is_stale

router = APIRouter(prefix="/v1/system", tags=["v1-system"])
canonical_router = APIRouter(prefix="/v1", tags=["v1-system"])


class SourceSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    site: str
    category: str
    url: str
    has_dataset: bool
    dataset_fetched_at: str | None = None


class DatasetSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    has_dataset: bool
    fetched_at: str | None = None
    state: str | None = None


class ReliabilityCounts(BaseModel):
    fresh_published: int = Field(ge=0)
    provisional: int = Field(ge=0)
    lkg_served: int = Field(ge=0)
    failed: int = Field(ge=0)
    timed_out: int = Field(ge=0)
    skipped: int = Field(ge=0)


class ReliabilityOutcomeRecoveryCounts(BaseModel):
    events: int = Field(ge=0)
    recovered_to_fresh: int = Field(ge=0)
    reclassified_upstream_pending: int = Field(ge=0)
    unresolved: int = Field(ge=0)


class ReliabilityOutcomeRecovery(BaseModel):
    provisional: ReliabilityOutcomeRecoveryCounts
    lkg_served: ReliabilityOutcomeRecoveryCounts


class ReliabilityFailureReasons(BaseModel):
    proxy_payment: int = Field(ge=0)
    authentication: int = Field(ge=0)
    rate_limited: int = Field(ge=0)
    access_blocked: int = Field(ge=0)
    upstream_4xx: int = Field(ge=0)
    upstream_5xx: int = Field(ge=0)
    timeout: int = Field(ge=0)
    transport: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    contract: int = Field(ge=0)
    parse_error: int = Field(ge=0)
    regression: int = Field(ge=0)
    backend_policy: int = Field(ge=0)
    ai_quarantine: int = Field(ge=0)
    publication_sync: int = Field(ge=0)
    preflight: int = Field(ge=0)
    dependency: int = Field(ge=0)
    unknown: int = Field(ge=0)


class ReliabilitySLO(BaseModel):
    target_rate_pct: float = Field(ge=0.0, le=100.0)
    objective_status: Literal["collecting", "meeting", "breached"]
    good_attempts: int = Field(ge=0)
    bad_attempts: int = Field(ge=0)
    allowed_bad_attempts: float = Field(ge=0.0)
    bad_attempts_over_budget: int = Field(ge=0)
    error_budget_remaining_attempts: float
    error_budget_consumed_pct: float | None = Field(default=None, ge=0.0)


class VerifiedCompletenessStates(BaseModel):
    complete: int = Field(ge=0)
    incomplete: int = Field(ge=0)
    unknown: int = Field(ge=0)


class VerifiedCompletenessSummary(BaseModel):
    instrumented_sources: int = Field(ge=0)
    catalog_sources: int = Field(ge=0)
    source_catalog_coverage_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    observed_instrumented_sources: int = Field(ge=0)
    instrumented_source_observation_coverage_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    sources_meeting_target: int = Field(ge=0)
    sources_below_target: int = Field(ge=0)
    sources_without_observations: int = Field(ge=0)
    source_target_attainment_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    macro_complete_fresh_rate_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description=(
            "Unweighted mean across every instrumented source; sources without "
            "observations contribute zero."
        ),
    )
    macro_target_met: bool = Field(
        description=(
            "Whether the exact, unrounded mean of per-source complete-fresh "
            "ratios meets the target."
        )
    )
    worst_observed_source_rate_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    tracked_attempts: int = Field(ge=0)
    complete_fresh: int = Field(ge=0)
    states: VerifiedCompletenessStates
    coverage_of_all_parser_attempts_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    complete_fresh_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    target_rate_pct: float = Field(ge=0.0, le=100.0)
    objective_status: Literal["collecting", "met", "miss"]


class AIReviewVerdicts(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    pass_: int = Field(alias="pass", ge=0)
    fail: int = Field(ge=0)
    uncertain: int = Field(ge=0)


class AICandidateReviewSummary(BaseModel):
    all_parser_attempts: int = Field(ge=0)
    attempted: int = Field(ge=0)
    completed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)
    coverage_of_all_parser_attempts_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    valid_response_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    verdicts: AIReviewVerdicts
    quarantined: int = Field(ge=0)


class AIDiagnosisClassifications(BaseModel):
    healthy: int = Field(ge=0)
    anomalous: int = Field(ge=0)
    inconclusive: int = Field(ge=0)


class AIDiagnosisDomains(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    identity: int = Field(ge=0)
    protection: int = Field(ge=0)
    auth: int = Field(ge=0)
    scope: int = Field(ge=0)
    schema_: int = Field(alias="schema", ge=0)
    completeness: int = Field(ge=0)
    semantics: int = Field(ge=0)
    freshness: int = Field(ge=0)
    regression: int = Field(ge=0)
    backend_policy: int = Field(ge=0)
    unknown: int = Field(ge=0)


class AIFailureDiagnosisSummary(BaseModel):
    all_problem_attempts: int = Field(ge=0)
    attempted: int = Field(ge=0)
    completed: int = Field(ge=0)
    errors: int = Field(ge=0)
    coverage_of_all_problem_attempts_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    valid_response_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    classifications: AIDiagnosisClassifications
    failure_domains: AIDiagnosisDomains


class AICalibrationSummary(BaseModel):
    status: Literal["not_calibrated"]
    human_labeled_examples: int = Field(ge=0)
    precision_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    recall_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    false_positive_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    limitation: Literal["human_labels_not_collected"]


class AIQualitySummary(BaseModel):
    candidate_review: AICandidateReviewSummary
    failure_diagnosis: AIFailureDiagnosisSummary
    calibration: AICalibrationSummary


class ScheduledReliabilitySummary(BaseModel):
    ledger_status: Literal["absent", "partial", "covered"]
    measurement_status: Literal["collecting", "observed"]
    schedule_coverage_ratio: float = Field(ge=0.0, le=1.0)
    temporal_coverage_ratio: float = Field(ge=0.0, le=1.0)
    coverage_started_at: str | None = None
    materialized_through: str | None = None
    tracked_schedules: int = Field(ge=0)
    catalog_schedules: int = Field(ge=0)
    expected_slots: int = Field(ge=0)
    eligible_slots: int = Field(ge=0)
    excluded_slots: int = Field(ge=0)
    pending_slots: int = Field(ge=0)
    due_slots: int = Field(ge=0)
    on_time_fresh: int = Field(ge=0)
    on_time_upstream_pending: int = Field(ge=0)
    on_time_nonfresh: int = Field(ge=0)
    late: int = Field(ge=0)
    missing: int = Field(ge=0)
    on_time_fresh_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    parser_eligible_due_slots: int = Field(ge=0)
    parser_on_time_fresh_rate_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    target_rate_pct: float = Field(ge=0.0, le=100.0)
    objective_status: Literal["collecting", "meeting", "breached"]
    parser_objective_status: Literal["collecting", "meeting", "breached"]


class ParsesUnixRolloutSummary(BaseModel):
    observed_attempts: int = Field(ge=0)
    observed_sources: int = Field(ge=0)
    shadow_attempts: int = Field(ge=0)
    active_attempts: int = Field(ge=0)
    transport_checked: int = Field(ge=0)
    transport_validated: int = Field(ge=0)
    transport_validated_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    candidate_checked: int = Field(ge=0)
    candidate_validated: int = Field(ge=0)
    candidate_validated_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    publication_checked: int = Field(ge=0)
    publication_validated: int = Field(ge=0)
    publication_validated_rate_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    http_status_compared: int = Field(ge=0)
    http_status_matches: int = Field(ge=0)
    http_status_match_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    content_hash_compared: int = Field(ge=0)
    content_hash_matches: int = Field(ge=0)
    content_hash_match_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    paid_requests_known_attempts: int = Field(ge=0)
    paid_requests: int | None = Field(default=None, ge=0)
    paid_cost_known_attempts: int = Field(ge=0)
    paid_cost_usd: str | None = Field(default=None, pattern=r"^\d+\.\d{6}$")


class ReliabilityWindow(BaseModel):
    window: Literal["24h", "7d", "30d"]
    from_at: str
    to_at: str
    measurement_status: Literal["collecting", "observed"]
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    physical_attempts: int | None = Field(default=None, ge=0)
    total_attempts: int = Field(ge=0)
    observed_eligible_attempts: int = Field(ge=0)
    missing_terminal_windows: int = Field(ge=0)
    eligible_attempts: int = Field(ge=0)
    upstream_pending_attempts: int = Field(ge=0)
    end_to_end_attempts: int = Field(ge=0)
    counts: ReliabilityCounts
    outcome_recovery: ReliabilityOutcomeRecovery
    failure_reasons: ReliabilityFailureReasons
    full_fresh_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    end_to_end_fresh_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    accepted_fresh_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    data_available_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    freshness_slo: ReliabilitySLO
    end_to_end_freshness_slo: ReliabilitySLO
    availability_slo: ReliabilitySLO
    verified_completeness: VerifiedCompletenessSummary
    parsesunix_rollout: ParsesUnixRolloutSummary
    ai_quality: AIQualitySummary
    scheduled_reliability: ScheduledReliabilitySummary


class ReliabilityMethodology(BaseModel):
    version: str
    unit: str
    scope: Literal["observed_scrape_and_pipeline_sources"]
    completeness: Literal["observed_attempts_plus_recorded_run_deficits"]
    limitations: list[str]
    coverage_method: Literal["complete_generic_refresh_per_24h_bucket"]
    coverage_scope: Literal["generic_scrape_sources_only"]
    coverage_max_gap_hours: float = Field(gt=0.0)
    coverage_cohort_method: Literal["current_canonical_scrape_registry_hash"]
    combined_slo_readiness: Literal[
        "collecting_pipeline_schedule_ledger",
        "ready",
    ]
    eligible_outcomes: list[str]
    excluded_outcomes: list[str]
    independently_ineligible_method: Literal[
        "verified_upstream_absence_is_excluded_from_parser_slo_but_included_"
        "in_end_to_end_freshness"
    ]
    slo_target_rate_pct: float = Field(ge=0.0, le=100.0)
    failure_reason_values: list[str]
    physical_attempts_method: str | None = None
    missing_terminal_method: Literal[
        "sum_positive_expected_minus_distinct_terminal_rows_per_recorded_logical_refresh"
    ]
    ai_accuracy_method: Literal["human_labels_required"]


class ReliabilityReport(BaseModel):
    methodology: ReliabilityMethodology
    generated_at: str
    coverage_cohort_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    coverage_started_at: str | None = None
    windows: list[ReliabilityWindow]


@router.get(
    "/sources",
    response_model=Envelope[list[SourceSummary]],
    response_model_exclude_none=True,
    deprecated=True,
)
def sources(
    site: str | None = Query(None, min_length=1, max_length=80),
    category: str | None = Query(None, min_length=1, max_length=80),
) -> Envelope[list[SourceSummary]]:
    selected = [
        source
        for source in SOURCES
        if (not site or source.site == site)
        and (not category or source.category == category)
    ]
    rows: list[dict[str, Any]] = []
    for source in selected:
        dataset = load_resolved_public_dataset(source.id)
        rows.append(
            {
                "id": source.id,
                "site": source.site,
                "category": source.category,
                "url": source.url,
                "has_dataset": dataset is not None,
                "dataset_fetched_at": dataset.get("fetched_at") if dataset else None,
            }
        )
    fetched_at = freshest_timestamp(rows, "dataset_fetched_at")
    return Envelope(
        data=[SourceSummary.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="source_registry",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=len(rows),
        ),
    )


@router.get(
    "/datasets",
    response_model=Envelope[list[DatasetSummary]],
    response_model_exclude_none=True,
    deprecated=True,
)
def datasets() -> Envelope[list[DatasetSummary]]:
    rows: list[dict[str, Any]] = []
    for source in SOURCES:
        dataset = load_resolved_public_dataset(source.id)
        status = load_status(source.id) or {}
        rows.append(
            {
                "source_id": source.id,
                "has_dataset": dataset is not None,
                "fetched_at": dataset.get("fetched_at") if dataset else None,
                "state": status.get("state"),
            }
        )
    fetched_at = freshest_timestamp(rows, "fetched_at")
    return Envelope(
        data=[DatasetSummary.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="all_datasets",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=len(rows),
        ),
    )


@router.get(
    "/health",
    response_model=Envelope[dict[str, Any]],
    deprecated=True,
)
def health() -> Envelope[dict[str, Any]]:
    from ..main import cached_health_diagnostics

    diagnostics = cached_health_diagnostics()
    fetched_at = freshest_timestamp(
        [
            {
                "fetched_at": (load_resolved_public_dataset(source.id) or {}).get(
                    "fetched_at"
                )
            }
            for source in SOURCES
        ],
        "fetched_at",
    )
    return Envelope(
        data=diagnostics,
        meta=ApiMeta(
            source_id="system_health",
            fetched_at=fetched_at,
            stale=bool(diagnostics.get("stale_count")),
            count=len(SOURCES),
        ),
    )


@router.get(
    "/parsing-reliability",
    response_model=Envelope[ReliabilityReport],
    response_model_exclude_none=True,
)
def parsing_reliability(response: Response) -> Envelope[ReliabilityReport]:
    response.headers["Cache-Control"] = "no-store"
    report = ReliabilityReport.model_validate(build_reliability_report())
    day = next((window for window in report.windows if window.window == "24h"), None)
    eligible_attempts = day.eligible_attempts if day is not None else 0
    return Envelope(
        data=report,
        meta=ApiMeta(
            source_id="parser_reliability",
            fetched_at=report.generated_at,
            stale=eligible_attempts == 0,
            count=eligible_attempts,
        ),
    )


canonical_router.add_api_route(
    "/sources",
    sources,
    methods=["GET"],
    response_model=Envelope[list[SourceSummary]],
    response_model_exclude_none=True,
)
canonical_router.add_api_route(
    "/datasets",
    datasets,
    methods=["GET"],
    response_model=Envelope[list[DatasetSummary]],
    response_model_exclude_none=True,
)
canonical_router.add_api_route(
    "/health",
    health,
    methods=["GET"],
    response_model=Envelope[dict[str, Any]],
)
