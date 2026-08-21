from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.convergence_policy import decide_recovery
from app.convergence_probes import execute_upstream_probe
from app.convergence_store import ConvergenceStore
from app.vicious_syndicate import ViciousPublicationProbe


def _probe_claim(path: Path):
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    store = ConvergenceStore(path)
    store.create_or_get_chain(
        cohort_id="vicious-reports",
        source_ids=["vicious_syndicate_radars"],
        origin_occurrence_id="schedule:20260821T080000Z",
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
    claim = store.claim_due(owner="probe-worker", now=observed + timedelta(minutes=30))
    assert claim is not None
    return claim


@pytest.mark.parametrize(
    ("probe", "outcome", "reason", "upstream_pending"),
    [
        (
            ViciousPublicationProbe("pending", "355", "354", 1),
            "skipped",
            "unavailable",
            True,
        ),
        (
            ViciousPublicationProbe("ready", "355", "354", 2),
            "failed",
            "transport",
            False,
        ),
        (
            ViciousPublicationProbe("inconclusive", "355", "354", 1),
            "skipped",
            "unavailable",
            False,
        ),
    ],
)
def test_vicious_probe_maps_to_bounded_recovery_outcome(
    tmp_path: Path,
    probe: ViciousPublicationProbe,
    outcome: str,
    reason: str,
    upstream_pending: bool,
) -> None:
    claim = _probe_claim(tmp_path / "parser-telemetry.sqlite3")
    with patch(
        "app.convergence_probes.probe_known_pending_publication",
        new=AsyncMock(return_value=probe),
    ):
        execution = asyncio.run(execute_upstream_probe(claim))

    assert execution.parser_run_id is None
    assert execution.paid_requests == 0
    assert execution.paid_cost_microusd == 0
    assert execution.results == (
        {
            "sourceId": "vicious_syndicate_radars",
            "outcome": outcome,
            "reasonCode": reason,
            "upstreamPending": upstream_pending,
        },
    )


def test_probe_executor_rejects_unsupported_source_without_network(
    tmp_path: Path,
) -> None:
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    path = tmp_path / "parser-telemetry.sqlite3"
    store = ConvergenceStore(path)
    store.create_or_get_chain(
        cohort_id="vicious-reports",
        source_ids=["vicious_syndicate_live_beta"],
        origin_occurrence_id="schedule:20260821T080000Z",
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
    claim = store.claim_due(
        owner="probe-worker",
        now=observed + timedelta(minutes=30),
    )
    assert claim is not None

    with patch(
        "app.convergence_probes.probe_known_pending_publication",
        new=AsyncMock(),
    ) as probe, pytest.raises(ValueError, match="does not support"):
        asyncio.run(execute_upstream_probe(claim))
    probe.assert_not_awaited()
