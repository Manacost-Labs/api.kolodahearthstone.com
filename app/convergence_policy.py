"""Deterministic, cost-safe policy for converging parser outcomes to fresh.

The policy is deliberately side-effect free. It groups sources by upstream
fetch family and decides whether an outcome may be retried, probed, paused, or
quarantined. Execution and durable state live in separate modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from .parsesunix_contracts import SPECIALIZED_API_SOURCE_IDS
from .post_patch_policy import EARLY_SOURCE_IDS
from .source_state import SourceState
from .sources import SOURCE_BY_ID
from .trinket_slices import TRINKET_SLICE_SOURCE_IDS

RecoveryAction = Literal[
    "complete",
    "probe_upstream",
    "retry_transport",
    "retry_candidate",
    "retry_publication",
    "retry_scheduler",
    "retry_local",
    "pause_provider",
    "quarantine",
    "diagnose",
]
RecoveryReasonClass = Literal[
    "fresh",
    "upstream_not_published",
    "transport_retryable",
    "candidate_not_ready",
    "publication_failure",
    "scheduler_gap",
    "auth_or_quota",
    "local_failure",
    "deterministic_failure",
    "unknown",
]
TransportClass = Literal["scrape", "pipeline", "specialized_api"]
ProbeStrategy = Literal["none", "availability", "version"]


@dataclass(frozen=True)
class RecoveryCohort:
    id: str
    provider: str
    source_ids: tuple[str, ...]
    transport_class: TransportClass
    probe_strategy: ProbeStrategy
    max_parallel: int = 1


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason_class: RecoveryReasonClass
    delays_seconds: tuple[int, ...]
    paid_fetch_allowed: bool

    @property
    def max_attempts(self) -> int:
        return len(self.delays_seconds)


_TRANSPORT_DELAYS = (5 * 60, 20 * 60, 60 * 60)
_CANDIDATE_DELAYS = (
    35 * 60,
    55 * 60,
    90 * 60,
    3 * 60 * 60,
    6 * 60 * 60,
    12 * 60 * 60,
)
_UPSTREAM_PROBE_DELAYS = (30 * 60, 60 * 60, 2 * 60 * 60, 4 * 60 * 60)
_LOCAL_DELAYS = (60, 5 * 60, 15 * 60)

_PAUSE_REASONS = frozenset({"proxy_payment", "authentication"})
_TRANSPORT_REASONS = frozenset(
    {
        "rate_limited",
        "access_blocked",
        "upstream_5xx",
        "timeout",
        "transport",
    }
)
_CANDIDATE_REASONS = frozenset({"contract", "regression"})
_LOCAL_REASONS = frozenset({"preflight", "dependency", "backend_policy"})
_DETERMINISTIC_REASONS = frozenset({"parse_error", "ai_quarantine"})
_UPSTREAM_REASONS = frozenset({"unavailable", "upstream_4xx"})


def decide_recovery(
    *,
    outcome: str,
    reason_code: str = "unknown",
    upstream_pending: bool = False,
) -> RecoveryDecision:
    """Return a bounded action without performing any network or storage I/O."""

    normalized_outcome = str(outcome or "").strip().lower()
    normalized_reason = str(reason_code or "unknown").strip().lower()

    if normalized_outcome == "fresh_published":
        return RecoveryDecision("complete", "fresh", (), False)
    if normalized_outcome == "missing":
        return RecoveryDecision(
            "retry_scheduler",
            "scheduler_gap",
            _LOCAL_DELAYS,
            False,
        )
    if upstream_pending or normalized_reason in _UPSTREAM_REASONS:
        return RecoveryDecision(
            "probe_upstream",
            "upstream_not_published",
            _UPSTREAM_PROBE_DELAYS,
            False,
        )
    if normalized_reason in _PAUSE_REASONS:
        return RecoveryDecision("pause_provider", "auth_or_quota", (), False)
    if normalized_outcome == "provisional" or normalized_reason in _CANDIDATE_REASONS:
        return RecoveryDecision(
            "retry_candidate",
            "candidate_not_ready",
            _CANDIDATE_DELAYS,
            False,
        )
    if normalized_reason == "publication_sync":
        return RecoveryDecision(
            "retry_publication",
            "publication_failure",
            _LOCAL_DELAYS,
            False,
        )
    if normalized_reason in _LOCAL_REASONS:
        return RecoveryDecision(
            "retry_local",
            "local_failure",
            _LOCAL_DELAYS,
            False,
        )
    if (
        normalized_outcome == SourceState.TIMED_OUT
        or normalized_reason in _TRANSPORT_REASONS
    ):
        return RecoveryDecision(
            "retry_transport",
            "transport_retryable",
            _TRANSPORT_DELAYS,
            True,
        )
    if normalized_reason in _DETERMINISTIC_REASONS:
        return RecoveryDecision(
            "quarantine",
            "deterministic_failure",
            (),
            False,
        )
    return RecoveryDecision("diagnose", "unknown", (), False)


def _explicit_source_families() -> tuple[tuple[str, frozenset[str]], ...]:
    hsguru_meta = frozenset(
        source_id
        for source_id in SOURCE_BY_ID
        if source_id.startswith("hsguru_meta_") and source_id != "hsguru_meta_matrix"
    )
    hsguru_matchups = frozenset(
        source_id
        for source_id in SOURCE_BY_ID
        if source_id.startswith("hsguru_matchups_")
    )
    hsreplay_trinkets = frozenset(
        {
            "hsreplay_battlegrounds_trinkets_lesser",
            "hsreplay_battlegrounds_trinkets_greater",
            *TRINKET_SLICE_SOURCE_IDS,
        }
    )
    hsreplay_meta_firecrawl = frozenset(
        source_id
        for source_id in SOURCE_BY_ID
        if source_id.startswith("hsreplay_meta_") and source_id.endswith("_firecrawl")
    )
    return (
        ("hsguru-meta-slices", hsguru_meta),
        ("hsguru-matchups", hsguru_matchups),
        ("hsreplay-card-periods", frozenset(HSREPLAY_CARD_PERIOD_SOURCE_IDS)),
        ("hsreplay-trinkets", hsreplay_trinkets),
        ("hsreplay-meta-firecrawl", hsreplay_meta_firecrawl),
        (
            "firestone-arena",
            frozenset(
                source_id
                for source_id in SOURCE_BY_ID
                if source_id.startswith("firestone_arena_")
            ),
        ),
        (
            "metastats-ranked",
            frozenset({"metastats_decks", "metastats_matchups"}),
        ),
        (
            "vicious-reports",
            frozenset(
                {"vicious_syndicate_live_beta", "vicious_syndicate_radars"}
            ),
        ),
    )


def _transport_class(source_ids: frozenset[str]) -> TransportClass:
    if source_ids and source_ids.issubset(SPECIALIZED_API_SOURCE_IDS):
        return "specialized_api"
    if any(SOURCE_BY_ID[source_id].kind == "pipeline" for source_id in source_ids):
        return "pipeline"
    return "scrape"


def _probe_strategy(source_ids: frozenset[str]) -> ProbeStrategy:
    if source_ids & EARLY_SOURCE_IDS or "vicious_syndicate_radars" in source_ids:
        return "version"
    if source_ids & SPECIALIZED_API_SOURCE_IDS:
        return "availability"
    return "none"


def _cohort(
    cohort_id: str,
    source_ids: frozenset[str],
) -> RecoveryCohort:
    providers = {SOURCE_BY_ID[source_id].site for source_id in source_ids}
    if len(providers) != 1:
        raise RuntimeError(f"Recovery cohort {cohort_id} spans multiple providers")
    return RecoveryCohort(
        id=cohort_id,
        provider=next(iter(providers)),
        source_ids=tuple(sorted(source_ids)),
        transport_class=_transport_class(source_ids),
        probe_strategy=_probe_strategy(source_ids),
    )


def _build_cohorts() -> tuple[RecoveryCohort, ...]:
    cohorts: list[RecoveryCohort] = []
    assigned: set[str] = set()
    for cohort_id, source_ids in _explicit_source_families():
        if not source_ids:
            continue
        duplicates = assigned & source_ids
        if duplicates:
            raise RuntimeError(
                "Sources belong to multiple recovery cohorts: "
                + ", ".join(sorted(duplicates))
            )
        cohorts.append(_cohort(cohort_id, source_ids))
        assigned.update(source_ids)

    fallback_groups: dict[tuple[str, str, str], set[str]] = {}
    for source_id, source in SOURCE_BY_ID.items():
        if source_id in assigned:
            continue
        key = (source.site, source.category, source.kind)
        fallback_groups.setdefault(key, set()).add(source_id)
    for key, values in sorted(fallback_groups.items()):
        provider, category, kind = key
        cohort_id = f"{provider}-{category}-{kind}".replace("_", "-")
        cohorts.append(_cohort(cohort_id, frozenset(values)))
        assigned.update(values)

    missing = set(SOURCE_BY_ID) - assigned
    if missing:
        raise RuntimeError(
            "Sources without a recovery cohort: " + ", ".join(sorted(missing))
        )
    return tuple(sorted(cohorts, key=lambda cohort: cohort.id))


RECOVERY_COHORTS = _build_cohorts()
RECOVERY_COHORT_BY_ID = {cohort.id: cohort for cohort in RECOVERY_COHORTS}
SOURCE_TO_RECOVERY_COHORT = {
    source_id: cohort.id
    for cohort in RECOVERY_COHORTS
    for source_id in cohort.source_ids
}
