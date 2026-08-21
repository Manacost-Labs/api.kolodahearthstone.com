from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.convergence_policy import decide_recovery
from app.convergence_store import ConvergenceClaim, ConvergenceStore
from app.convergence_worker import (
    RecoveryExecution,
    eligible_direct_candidate_source_ids,
    eligible_transport_source_ids,
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


def _probe_chain(path: Path):
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    return ConvergenceStore(path).create_or_get_chain(
        cohort_id="vicious-reports",
        source_ids=["vicious_syndicate_radars"],
        origin_occurrence_id="refresh-all:20260821T080000Z",
        decision=decide_recovery(
            outcome="lkg_served",
            reason_code="unavailable",
            upstream_pending=True,
        ),
        outcome="lkg_served",
        reason_code="unavailable",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=12),
    )


def test_eligible_transport_sources_require_explicit_active_parsesunix_rollout(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HS_PARSESUNIX_ENABLED", "true")
    monkeypatch.setenv(
        "HS_PARSESUNIX_ACTIVE_SOURCE_IDS",
        "hsguru_meta_standard_legend",
    )
    monkeypatch.setenv(
        "HS_PARSESUNIX_SHADOW_SOURCE_IDS",
        "hsguru_meta_standard_diamond_4to1",
    )

    assert eligible_transport_source_ids() == frozenset(
        {"hsguru_meta_standard_legend"}
    )


def test_direct_candidate_worker_uses_only_bounded_hsreplay_allowlist() -> None:
    source_ids = eligible_direct_candidate_source_ids()

    assert len(source_ids) == 19
    assert "hsreplay_cards_legend_patch" in source_ids
    assert "hsreplay_battlegrounds_trinkets_lesser" in source_ids
    assert "hsreplay_cards_legend_1d" not in source_ids
    assert all(source_id.startswith("hsreplay_") for source_id in source_ids)


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


def test_worker_does_not_claim_transport_outside_active_rollout(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _transport_chain(path)
    calls: list[ConvergenceClaim] = []

    async def execute(claim: ConvergenceClaim) -> RecoveryExecution:
        calls.append(claim)
        raise AssertionError("ineligible transport must not be executed")

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 5, tzinfo=UTC),
            owner="test-worker",
            mode="active",
            eligible_source_ids=frozenset(),
        )
    )

    assert summary.claimed is False
    assert calls == []
    assert ConvergenceStore(path).get_chain(chain.chain_id).state == "waiting"


def test_worker_can_run_only_the_explicit_free_probe_action(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _probe_chain(path)

    async def execute(_claim: ConvergenceClaim) -> RecoveryExecution:
        return RecoveryExecution(
            parser_run_id=None,
            results=(
                {
                    "sourceId": "vicious_syndicate_radars",
                    "outcome": "skipped",
                    "reasonCode": "unavailable",
                    "upstreamPending": True,
                },
            ),
        )

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 30, tzinfo=UTC),
            owner="probe-worker",
            mode="active",
            actions=frozenset({"probe_upstream"}),
            eligible_source_ids=frozenset({"vicious_syndicate_radars"}),
        )
    )

    restored = ConvergenceStore(path).get_chain(chain.chain_id)
    assert summary.claimed is True
    assert summary.outcome == "skipped"
    assert restored is not None
    assert restored.action == "probe_upstream"
    assert restored.state == "upstream_pending"


def test_ready_probe_transitions_to_first_transport_delay(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _probe_chain(path)

    async def execute(_claim: ConvergenceClaim) -> RecoveryExecution:
        return RecoveryExecution(
            parser_run_id=None,
            results=(
                {
                    "sourceId": "vicious_syndicate_radars",
                    "outcome": "failed",
                    "reasonCode": "transport",
                    "upstreamPending": False,
                },
            ),
        )

    summary = asyncio.run(
        run_once(
            store=ConvergenceStore(path),
            executor=execute,
            now=datetime(2026, 8, 21, 8, 30, tzinfo=UTC),
            owner="probe-worker",
            mode="active",
            actions=frozenset({"probe_upstream"}),
            eligible_source_ids=frozenset({"vicious_syndicate_radars"}),
        )
    )

    restored = ConvergenceStore(path).get_chain(chain.chain_id)
    assert summary.outcome == "failed"
    assert restored is not None
    assert restored.action == "retry_transport"
    assert restored.next_attempt_at == datetime(2026, 8, 21, 8, 35, tzinfo=UTC)


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
