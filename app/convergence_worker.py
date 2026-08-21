"""Bounded, off-by-default execution of safe freshness recovery chains."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from .convergence_policy import decide_recovery
from .convergence_store import ConvergenceClaim, ConvergenceStore

WorkerMode = Literal["off", "active"]
RecoveryExecutor = Callable[[ConvergenceClaim], Awaitable["RecoveryExecution"]]

# Candidate confirmation, publication-only repair, scheduler repair, and
# upstream probes need their own no-paid execution paths. Until those paths are
# implemented, the active worker may only repeat a retryable transport fetch.
SAFE_EXECUTABLE_ACTIONS = frozenset({"retry_transport"})

_OUTCOME_PRIORITY = {
    "timed_out": 5,
    "failed": 4,
    "lkg_served": 3,
    "provisional": 2,
    "skipped": 1,
    "fresh_published": 0,
}


@dataclass(frozen=True)
class RecoveryExecution:
    parser_run_id: str | None
    results: tuple[Mapping[str, object], ...]
    paid_requests: int = 0
    paid_cost_microusd: int = 0


@dataclass(frozen=True)
class WorkerSummary:
    mode: WorkerMode
    claimed: bool
    chain_id: str | None = None
    attempt_id: str | None = None
    parser_run_id: str | None = None
    outcome: str | None = None
    reason_code: str | None = None
    final_state: str | None = None


def worker_mode() -> WorkerMode:
    value = os.environ.get("HS_CONVERGENCE_WORKER_MODE", "off").strip().lower()
    return cast(WorkerMode, value) if value in {"off", "active"} else "off"


def _source_id(result: Mapping[str, object]) -> str:
    return str(result.get("sourceId") or result.get("source_id") or "").strip()


def _classify_execution(
    claim: ConvergenceClaim,
    execution: RecoveryExecution,
) -> tuple[str, str, bool]:
    expected = set(claim.chain.source_ids)
    observed_ids = [_source_id(result) for result in execution.results]
    if (
        not execution.results
        or any(not source_id for source_id in observed_ids)
        or len(observed_ids) != len(set(observed_ids))
        or set(observed_ids) != expected
    ):
        return "failed", "dependency", False

    normalized: list[tuple[str, str, bool]] = []
    for result in execution.results:
        outcome = str(
            result.get("outcome") or result.get("terminalOutcome") or "failed"
        ).strip()
        if outcome not in _OUTCOME_PRIORITY:
            outcome = "failed"
        reason_code = str(
            result.get("reasonCode") or result.get("reason_code") or "unknown"
        ).strip()
        upstream_pending = bool(
            result.get("upstreamPending")
            or result.get("independently_ineligible_reason")
            == "upstream_not_published"
        )
        normalized.append((outcome, reason_code or "unknown", upstream_pending))

    if all(outcome == "fresh_published" for outcome, _reason, _pending in normalized):
        return "fresh_published", "none", True
    if any(pending for _outcome, _reason, pending in normalized):
        return "skipped", "unavailable", True
    outcome, reason_code, _pending = max(
        normalized,
        key=lambda item: _OUTCOME_PRIORITY[item[0]],
    )
    if outcome in {"fresh_published", "provisional"}:
        reason_code = "none"
    return outcome, reason_code, True


async def run_once(
    *,
    store: ConvergenceStore,
    executor: RecoveryExecutor,
    now: datetime | None = None,
    owner: str,
    mode: WorkerMode | None = None,
) -> WorkerSummary:
    effective_mode = mode or worker_mode()
    if effective_mode == "off":
        return WorkerSummary(mode="off", claimed=False)

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    moment = moment.astimezone(UTC)
    claim = store.claim_due(
        owner=owner,
        now=moment,
        actions=SAFE_EXECUTABLE_ACTIONS,
    )
    if claim is None:
        return WorkerSummary(mode="active", claimed=False)

    try:
        execution = await executor(claim)
        outcome, reason_code, execution_succeeded = _classify_execution(
            claim,
            execution,
        )
    except Exception:  # noqa: BLE001 - boundary fails closed into local recovery
        execution = RecoveryExecution(parser_run_id=None, results=())
        outcome, reason_code, execution_succeeded = "failed", "dependency", False

    decision = decide_recovery(
        outcome=outcome,
        reason_code=reason_code,
        upstream_pending=outcome == "skipped" and reason_code == "unavailable",
    )
    final_chain = store.finish_attempt(
        attempt_id=claim.attempt_id,
        owner=owner,
        outcome=outcome,
        reason_code=reason_code,
        decision=decision,
        finished_at=moment,
        execution_succeeded=execution_succeeded,
        parser_run_id=execution.parser_run_id,
        paid_requests=execution.paid_requests,
        paid_cost_microusd=execution.paid_cost_microusd,
    )
    return WorkerSummary(
        mode="active",
        claimed=True,
        chain_id=claim.chain.chain_id,
        attempt_id=claim.attempt_id,
        parser_run_id=execution.parser_run_id,
        outcome=outcome,
        reason_code=reason_code,
        final_state=final_chain.state,
    )
