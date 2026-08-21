from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import httpx

from app.convergence_executor import (
    HttpParserControlClient,
    PaidUsageUnavailable,
    ParserControlContractError,
    execute_parser_control_recovery,
)
from app.convergence_policy import decide_recovery
from app.convergence_store import ConvergenceClaim, ConvergenceStore


def _claim(path: Path) -> ConvergenceClaim:
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    store = ConvergenceStore(path)
    store.create_or_get_chain(
        cohort_id="hsguru-meta-slices",
        source_ids=["hsguru_meta_standard_legend"],
        origin_occurrence_id="schedule:20260821T080000Z",
        decision=decide_recovery(outcome="failed", reason_code="transport"),
        outcome="failed",
        reason_code="transport",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=4),
    )
    claim = store.claim_due(owner="worker", now=observed + timedelta(minutes=5))
    assert claim is not None
    return claim


def _run(claim: ConvergenceClaim, *, status: str) -> dict[str, Any]:
    terminal = status in {"succeeded", "partial", "failed"}
    return {
        "id": "a" * 32,
        "status": status,
        "attemptPurpose": "recovery",
        "originOccurrenceId": claim.chain.origin_occurrence_id,
        "recoveryChainId": claim.chain.chain_id,
        "sourceIds": list(claim.chain.source_ids),
        "totalSources": 1,
        "completedSources": 1 if terminal else 0,
        "failedSources": 0,
        "results": (
            [
                {
                    "sourceId": "hsguru_meta_standard_legend",
                    "state": "ok",
                    "servingCachedDataset": False,
                    "outcome": "fresh_published",
                    "reasonCode": "none",
                    "upstreamPending": False,
                    "paidRequests": 1,
                    "paidCostMicrousd": 290,
                    "paidUsageExact": True,
                }
            ]
            if terminal
            else []
        ),
    }


class _Client:
    def __init__(self, claim: ConvergenceClaim, runs: list[dict[str, Any]]) -> None:
        self.claim = claim
        self.runs = list(runs)
        self.enqueued: list[tuple[str, int]] = []

    async def enqueue_recovery(
        self,
        claim: ConvergenceClaim,
    ) -> dict[str, Any]:
        self.enqueued.append((claim.chain.chain_id, claim.attempt_number))
        return self.runs.pop(0)

    async def get_run(self, _run_id: str) -> dict[str, Any]:
        return self.runs.pop(0)


def test_executor_polls_correlated_run_and_aggregates_exact_usage(
    tmp_path: Path,
) -> None:
    claim = _claim(tmp_path / "parser-telemetry.sqlite3")
    client = _Client(
        claim,
        [
            _run(claim, status="queued"),
            _run(claim, status="running"),
            _run(claim, status="succeeded"),
        ],
    )

    execution = asyncio.run(
        execute_parser_control_recovery(
            claim,
            client=client,
            poll_interval_seconds=0,
            timeout_seconds=10,
        )
    )

    assert client.enqueued == [(claim.chain.chain_id, 1)]
    assert execution.parser_run_id == "a" * 32
    assert execution.paid_requests == 1
    assert execution.paid_cost_microusd == 290
    assert execution.results[0]["outcome"] == "fresh_published"


def test_executor_rejects_unknown_paid_cost_instead_of_reporting_zero(
    tmp_path: Path,
) -> None:
    claim = _claim(tmp_path / "parser-telemetry.sqlite3")
    terminal = _run(claim, status="succeeded")
    terminal["results"][0].pop("paidCostMicrousd")
    terminal["results"][0]["paidUsageExact"] = False

    with pytest.raises(PaidUsageUnavailable, match="exact paid usage"):
        asyncio.run(
            execute_parser_control_recovery(
                claim,
                client=_Client(claim, [terminal]),
                poll_interval_seconds=0,
                timeout_seconds=10,
            )
        )


def test_executor_fails_closed_on_correlation_mismatch(tmp_path: Path) -> None:
    claim = _claim(tmp_path / "parser-telemetry.sqlite3")
    terminal = _run(claim, status="succeeded")
    terminal["recoveryChainId"] = "other-chain"

    with pytest.raises(ParserControlContractError, match="correlation"):
        asyncio.run(
            execute_parser_control_recovery(
                claim,
                client=_Client(claim, [terminal]),
                poll_interval_seconds=0,
                timeout_seconds=10,
            )
        )


def test_http_client_sends_scoped_idempotent_recovery_contract(tmp_path: Path) -> None:
    claim = _claim(tmp_path / "parser-telemetry.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://api:8000/admin/orchestrator/parser-runs"
        assert request.headers["X-Orchestrator-Key"] == "x" * 32
        payload = __import__("json").loads(request.content)
        assert payload == {
            "requestId": f"convergence:{claim.chain.chain_id}:attempt:1",
            "sourceIds": ["hsguru_meta_standard_legend"],
            "attemptPurpose": "recovery",
            "originOccurrenceId": claim.chain.origin_occurrence_id,
            "recoveryChainId": claim.chain.chain_id,
            "reason": "bounded automatic freshness recovery",
        }
        return httpx.Response(
            202,
            json={"run": _run(claim, status="queued"), "deduplicated": False},
        )

    client = HttpParserControlClient(
        base_url="http://api:8000",
        token="x" * 32,
        transport=httpx.MockTransport(handler),
    )

    run = asyncio.run(client.enqueue_recovery(claim))

    assert run["status"] == "queued"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://public.example.invalid",
        "https://user@example.invalid",
        "ftp://api:8000",
        "http://api:8000?token=secret",
    ],
)
def test_http_client_rejects_unsafe_control_plane_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        HttpParserControlClient(base_url=base_url, token="x" * 32)
