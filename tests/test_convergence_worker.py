from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.convergence_policy import decide_recovery
from app.convergence_store import ConvergenceClaim, ConvergenceStore
from app.convergence_worker import (
    RecoveryExecution,
    run_once,
)


def _transport_chain(path: Path):
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    return ConvergenceStore(path).create_or_get_chain(
        cohort_id="hsguru-meta-slices",
        source_ids=["hsguru_meta_standard_legend"],
        origin_occurrence_id="refresh-all:20260821T080000Z",
        decision=decide_recovery(outcome="failed", reason_code="transport"),
        outcome="failed",
        reason_code="transport",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=4),
    )


def _candidate_chain(path: Path):
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    return ConvergenceStore(path).create_or_get_chain(
        cohort_id="hsguru-meta-slices",
        source_ids=["hsguru_meta_standard_legend"],
        origin_occurrence_id="post-patch:20260821T080000Z",
        decision=decide_recovery(outcome="provisional", reason_code="none"),
        outcome="provisional",
        reason_code="none",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=24),
    )


def test_worker_defaults_off_without_claiming_or_executing(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _transport_chain(path)
    calls: list[ConvergenceClaim] = []

    async def execute(claim: ConvergenceClaim) -> RecoveryExecution:
        calls.append(claim)
        raise AssertionError("off worker must not execute recovery")

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 5, tzinfo=UTC),
            owner="test-worker",
            mode="off",
        )
    )

    assert summary.mode == "off"
    assert summary.claimed is False
    assert calls == []
    assert ConvergenceStore(path).get_chain(chain.chain_id).state == "waiting"


def test_worker_completes_transport_chain_only_after_all_sources_are_fresh(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _transport_chain(path)

    async def execute(claim: ConvergenceClaim) -> RecoveryExecution:
        assert claim.chain.paid_fetch_allowed is True
        return RecoveryExecution(
            parser_run_id="a" * 32,
            results=(
                {
                    "sourceId": "hsguru_meta_standard_legend",
                    "outcome": "fresh_published",
                    "reasonCode": "none",
                    "upstreamPending": False,
                },
            ),
            paid_requests=1,
            paid_cost_microusd=1500,
        )

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 5, tzinfo=UTC),
            owner="test-worker",
            mode="active",
        )
    )
    restored = ConvergenceStore(path).get_chain(chain.chain_id)

    assert summary.claimed is True
    assert summary.outcome == "fresh_published"
    assert summary.final_state == "fresh"
    assert restored is not None
    assert restored.state == "fresh"
    assert restored.paid_requests_total == 1
    assert restored.paid_cost_microusd_total == 1500


def test_worker_reschedules_transport_chain_when_result_is_lkg(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _transport_chain(path)

    async def execute(_claim: ConvergenceClaim) -> RecoveryExecution:
        return RecoveryExecution(
            parser_run_id="b" * 32,
            results=(
                {
                    "sourceId": "hsguru_meta_standard_legend",
                    "outcome": "lkg_served",
                    "reasonCode": "access_blocked",
                    "upstreamPending": False,
                },
            ),
        )

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 5, tzinfo=UTC),
            owner="test-worker",
            mode="active",
        )
    )
    restored = ConvergenceStore(path).get_chain(chain.chain_id)

    assert summary.outcome == "lkg_served"
    assert summary.final_state == "waiting"
    assert restored is not None
    assert restored.action == "retry_transport"
    assert restored.next_attempt_at == datetime(2026, 8, 21, 8, 25, tzinfo=UTC)


def test_transport_worker_does_not_claim_candidate_retry(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _candidate_chain(path)
    calls: list[ConvergenceClaim] = []

    async def execute(claim: ConvergenceClaim) -> RecoveryExecution:
        calls.append(claim)
        raise AssertionError("candidate retries require a separate safe executor")

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 15, tzinfo=UTC),
            owner="test-worker",
            mode="active",
        )
    )

    assert summary.claimed is False
    assert calls == []
    assert ConvergenceStore(path).get_chain(chain.chain_id).state == "waiting"


def test_worker_fails_closed_when_executor_returns_incomplete_source_set(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _transport_chain(path)

    async def execute(_claim: ConvergenceClaim) -> RecoveryExecution:
        return RecoveryExecution(parser_run_id="c" * 32, results=())

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 5, tzinfo=UTC),
            owner="test-worker",
            mode="active",
        )
    )
    restored = ConvergenceStore(path).get_chain(chain.chain_id)

    assert summary.outcome == "failed"
    assert summary.reason_code == "dependency"
    assert restored is not None
    assert restored.state == "waiting"
    assert restored.action == "retry_local"
