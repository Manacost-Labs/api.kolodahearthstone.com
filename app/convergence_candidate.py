"""Free direct-only confirmation for bounded HSReplay provisional cohorts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .convergence_store import ConvergenceClaim
from .convergence_worker import RecoveryExecution
from .post_patch_policy import (
    HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS,
    TRINKET_EARLY_SOURCE_IDS,
)
from .refresh_context import direct_only_candidate_confirmation

DIRECT_CANDIDATE_SOURCE_IDS = frozenset(
    HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS | TRINKET_EARLY_SOURCE_IDS
)


def _telemetry_run_id(claim: ConvergenceClaim) -> str:
    material = f"direct-candidate:{claim.attempt_id}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


async def execute_direct_candidate_confirmation(
    claim: ConvergenceClaim,
    *,
    telemetry_path: Path | None = None,
) -> RecoveryExecution:
    """Repeat an early candidate fetch without paid or residential transports."""

    if claim.chain.action != "retry_candidate":
        raise ValueError("Convergence claim is not a candidate retry")
    source_ids = frozenset(claim.chain.source_ids)
    if not source_ids or not source_ids <= DIRECT_CANDIDATE_SOURCE_IDS:
        raise ValueError("Candidate source is not eligible for direct confirmation")

    from .parser_control import execute_parser_run, summarize_parser_result
    from .reliability_telemetry import telemetry_db_path

    run_id = _telemetry_run_id(claim)
    with direct_only_candidate_confirmation():
        raw_results = await execute_parser_run(
            sorted(source_ids),
            telemetry_run_id=run_id,
            telemetry_path=telemetry_path or telemetry_db_path(),
            attempt_purpose="recovery",
            origin_occurrence_id=claim.chain.origin_occurrence_id,
            recovery_chain_id=claim.chain.chain_id,
            refresh_window_id=claim.chain.origin_occurrence_id,
        )
    results = tuple(summarize_parser_result(result) for result in raw_results)
    return RecoveryExecution(
        parser_run_id=run_id,
        results=results,
        paid_requests=0,
        paid_cost_microusd=0,
    )
