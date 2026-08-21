"""Bounded, off-by-default execution of safe freshness recovery chains."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from .convergence_policy import decide_recovery
from .convergence_store import ConvergenceClaim, ConvergenceStore

WorkerMode = Literal["off", "active"]
RecoveryExecutor = Callable[[ConvergenceClaim], Awaitable["RecoveryExecution"]]

# Candidate confirmation, publication-only repair, and scheduler repair still
# need dedicated no-paid paths. Vicious Radars has an explicit free probe;
# transport retries remain a separate, paid-accounted path.
SAFE_EXECUTABLE_ACTIONS = frozenset(
    {"retry_candidate", "retry_transport", "probe_upstream"}
)
_DEFAULT_WORKER_ACTIONS = frozenset({"retry_transport"})

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


def eligible_transport_source_ids() -> frozenset[str]:
    """Return only explicitly active ParsesUnix sources for automatic retries."""

    from .config import parsesunix_active_source_ids, parsesunix_mode_for_source
    from .sources import SOURCE_BY_ID

    configured = parsesunix_active_source_ids()
    unknown = sorted(configured - set(SOURCE_BY_ID))
    if unknown:
        raise ValueError(
            "HS_PARSESUNIX_ACTIVE_SOURCE_IDS contains unknown sources: "
            + ", ".join(unknown)
        )
    return frozenset(
        source_id
        for source_id in configured
        if parsesunix_mode_for_source(source_id) == "parsesunix"
    )


def eligible_direct_candidate_source_ids() -> frozenset[str]:
    """Return the exact HSReplay early-data cohort with a free direct path."""

    from .convergence_candidate import DIRECT_CANDIDATE_SOURCE_IDS

    return DIRECT_CANDIDATE_SOURCE_IDS


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
    actions: frozenset[str] | None = None,
    eligible_source_ids: frozenset[str] | None = None,
    lease_seconds: int = 5 * 60,
) -> WorkerSummary:
    effective_mode = mode or worker_mode()
    if effective_mode == "off":
        return WorkerSummary(mode="off", claimed=False)

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    moment = moment.astimezone(UTC)
    selected_actions = _DEFAULT_WORKER_ACTIONS if actions is None else actions
    if not selected_actions <= SAFE_EXECUTABLE_ACTIONS:
        raise ValueError("actions contains an unsupported worker action")
    claim = store.claim_due(
        owner=owner,
        now=moment,
        lease_seconds=lease_seconds,
        actions=selected_actions,
        eligible_source_ids=eligible_source_ids,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--mode", choices=("off", "active"))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HS_CONVERGENCE_API_BASE_URL", "http://api:8000"),
    )
    args = parser.parse_args()
    mode = cast(WorkerMode, args.mode) if args.mode else worker_mode()

    if mode == "off":
        # The default path is deliberately side-effect free: no ledger file,
        # network client, or secret is touched until active mode is explicit.
        summary = WorkerSummary(mode="off", claimed=False)
    else:
        from .convergence_candidate import execute_direct_candidate_confirmation
        from .convergence_executor import (
            HttpParserControlClient,
            execute_parser_control_recovery,
        )
        from .convergence_probes import execute_upstream_probe

        store = ConvergenceStore(args.path)
        owner = f"convergence-worker-{os.getpid()}"
        summary = asyncio.run(
            run_once(
                store=store,
                executor=execute_direct_candidate_confirmation,
                owner=owner,
                mode="active",
                actions=frozenset({"retry_candidate"}),
                eligible_source_ids=eligible_direct_candidate_source_ids(),
                lease_seconds=10 * 60,
            )
        )
        if not summary.claimed:
            summary = asyncio.run(
                run_once(
                    store=store,
                    executor=execute_upstream_probe,
                    owner=owner,
                    mode="active",
                    actions=frozenset({"probe_upstream"}),
                    eligible_source_ids=frozenset({"vicious_syndicate_radars"}),
                    lease_seconds=60,
                )
            )
        if not summary.claimed:
            client = HttpParserControlClient(
                base_url=args.base_url,
                token=os.environ.get("HS_ORCHESTRATOR_API_KEY", ""),
            )

            async def execute(claim: ConvergenceClaim) -> RecoveryExecution:
                return await execute_parser_control_recovery(claim, client=client)

            summary = asyncio.run(
                run_once(
                    store=store,
                    executor=execute,
                    owner=owner,
                    mode="active",
                    actions=frozenset({"retry_transport"}),
                    eligible_source_ids=eligible_transport_source_ids(),
                    lease_seconds=25 * 60,
                )
            )
    print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
