from __future__ import annotations

import pytest

from app.convergence_policy import (
    RECOVERY_COHORT_BY_ID,
    RECOVERY_COHORTS,
    SOURCE_TO_RECOVERY_COHORT,
    decide_recovery,
)
from app.hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from app.reliability_telemetry import FAILURE_REASONS
from app.sources import SOURCE_BY_ID
from app.trinket_slices import TRINKET_SLICE_SOURCE_IDS


def test_every_source_belongs_to_exactly_one_recovery_cohort() -> None:
    flattened = [
        source_id
        for cohort in RECOVERY_COHORTS
        for source_id in cohort.source_ids
    ]

    assert set(flattened) == set(SOURCE_BY_ID)
    assert len(flattened) == len(set(flattened))
    assert set(SOURCE_TO_RECOVERY_COHORT) == set(SOURCE_BY_ID)


def test_shared_fetch_families_are_kept_together() -> None:
    card_periods = RECOVERY_COHORT_BY_ID["hsreplay-card-periods"]
    trinkets = RECOVERY_COHORT_BY_ID["hsreplay-trinkets"]

    assert set(card_periods.source_ids) == set(HSREPLAY_CARD_PERIOD_SOURCE_IDS)
    assert set(TRINKET_SLICE_SOURCE_IDS).issubset(trinkets.source_ids)
    assert RECOVERY_COHORT_BY_ID["hsguru-meta-slices"].provider == "hsguru"
    assert RECOVERY_COHORT_BY_ID["vicious-reports"].probe_strategy == "version"


@pytest.mark.parametrize("reason_code", FAILURE_REASONS)
def test_every_failure_reason_has_a_bounded_deterministic_decision(
    reason_code: str,
) -> None:
    decision = decide_recovery(outcome="failed", reason_code=reason_code)

    assert decision.action
    assert decision.reason_class
    assert decision.max_attempts <= 6
    if decision.paid_fetch_allowed:
        assert decision.delays_seconds


def test_provisional_uses_maturation_backoff_instead_of_rapid_http_retry() -> None:
    decision = decide_recovery(outcome="provisional", reason_code="none")

    assert decision.action == "retry_candidate"
    assert decision.delays_seconds == (900, 2700, 5400, 10800, 21600, 43200)
    assert decision.paid_fetch_allowed is False


def test_verified_upstream_delay_never_authorizes_a_paid_fetch() -> None:
    decision = decide_recovery(
        outcome="lkg_served",
        reason_code="unknown",
        upstream_pending=True,
    )

    assert decision.action == "probe_upstream"
    assert decision.reason_class == "upstream_not_published"
    assert decision.paid_fetch_allowed is False


@pytest.mark.parametrize("reason_code", ["proxy_payment", "authentication"])
def test_auth_and_payment_failures_open_the_provider_circuit(
    reason_code: str,
) -> None:
    decision = decide_recovery(outcome="failed", reason_code=reason_code)

    assert decision.action == "pause_provider"
    assert decision.max_attempts == 0
    assert decision.paid_fetch_allowed is False


def test_publication_failure_retries_locally_without_provider_cost() -> None:
    decision = decide_recovery(
        outcome="lkg_served",
        reason_code="publication_sync",
    )

    assert decision.action == "retry_publication"
    assert decision.delays_seconds == (60, 300, 900)
    assert decision.paid_fetch_allowed is False


@pytest.mark.parametrize(
    "reason_code",
    ["contract", "regression", "preflight", "dependency", "backend_policy"],
)
def test_local_and_candidate_failures_cannot_trigger_paid_escalation(
    reason_code: str,
) -> None:
    decision = decide_recovery(outcome="failed", reason_code=reason_code)

    assert decision.paid_fetch_allowed is False


def test_unknown_failure_fails_closed_for_automatic_spend() -> None:
    decision = decide_recovery(outcome="failed", reason_code="unknown")

    assert decision.action == "diagnose"
    assert decision.max_attempts == 0
    assert decision.paid_fetch_allowed is False
