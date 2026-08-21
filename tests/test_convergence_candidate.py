from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.convergence_candidate import (
    DIRECT_CANDIDATE_SOURCE_IDS,
    execute_direct_candidate_confirmation,
)
from app.convergence_policy import decide_recovery
from app.convergence_store import ConvergenceClaim, ConvergenceStore
from app.post_patch_policy import (
    HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS,
    TRINKET_EARLY_SOURCE_IDS,
)
from app.refresh_context import is_direct_only_candidate_confirmation


def _claim(path: Path, source_id: str) -> ConvergenceClaim:
    observed = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    store = ConvergenceStore(path)
    cohort_id = (
        "hsreplay-card-periods"
        if source_id.startswith("hsreplay_cards_")
        else "hsguru-meta-slices"
    )
    store.create_or_get_chain(
        cohort_id=cohort_id,
        source_ids=[source_id],
        origin_occurrence_id="post-patch:20260821T080000Z",
        decision=decide_recovery(outcome="provisional", reason_code="none"),
        outcome="provisional",
        reason_code="none",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=24),
    )
    claim = store.claim_due(
        owner="candidate-test",
        now=observed + timedelta(minutes=35),
        actions=frozenset({"retry_candidate"}),
    )
    assert claim is not None
    return claim


def test_direct_candidate_allowlist_is_exactly_the_hsreplay_early_cohort() -> None:
    assert DIRECT_CANDIDATE_SOURCE_IDS == frozenset(
        HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS | TRINKET_EARLY_SOURCE_IDS
    )
    assert len(DIRECT_CANDIDATE_SOURCE_IDS) == 19


def test_direct_candidate_executor_records_recovery_and_zero_paid_usage(
    tmp_path: Path,
) -> None:
    source_id = "hsreplay_cards_legend_patch"
    claim = _claim(tmp_path / "convergence.sqlite3", source_id)
    execute = AsyncMock(
        return_value=[
            {
                "source_id": source_id,
                "state": "ok",
                "provisional": False,
            }
        ]
    )

    async def run() -> object:
        with (
            patch("app.parser_control.execute_parser_run", execute),
            patch(
                "app.parser_control.summarize_parser_result",
                return_value={
                    "sourceId": source_id,
                    "terminalOutcome": "fresh_published",
                    "reasonCode": "none",
                },
            ),
        ):
            return await execute_direct_candidate_confirmation(
                claim,
                telemetry_path=tmp_path / "telemetry.sqlite3",
            )

    result = asyncio.run(run())

    assert result.paid_requests == 0
    assert result.paid_cost_microusd == 0
    assert result.results[0]["terminalOutcome"] == "fresh_published"
    assert execute.await_count == 1
    kwargs = execute.await_args.kwargs
    assert kwargs["attempt_purpose"] == "recovery"
    assert kwargs["origin_occurrence_id"] == claim.chain.origin_occurrence_id
    assert kwargs["recovery_chain_id"] == claim.chain.chain_id
    assert kwargs["refresh_window_id"] == claim.chain.origin_occurrence_id


def test_direct_candidate_context_is_active_only_during_execution(
    tmp_path: Path,
) -> None:
    claim = _claim(
        tmp_path / "convergence.sqlite3",
        "hsreplay_cards_legend_patch",
    )

    async def execute(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        assert is_direct_only_candidate_confirmation() is True
        return [
            {
                "source_id": "hsreplay_cards_legend_patch",
                "state": "ok",
            }
        ]

    with (
        patch("app.parser_control.execute_parser_run", side_effect=execute),
        patch(
            "app.parser_control.summarize_parser_result",
            return_value={
                "sourceId": "hsreplay_cards_legend_patch",
                "terminalOutcome": "fresh_published",
                "reasonCode": "none",
            },
        ),
    ):
        asyncio.run(
            execute_direct_candidate_confirmation(
                claim,
                telemetry_path=tmp_path / "telemetry.sqlite3",
            )
        )

    assert is_direct_only_candidate_confirmation() is False


def test_direct_candidate_executor_rejects_non_allowlisted_source(
    tmp_path: Path,
) -> None:
    claim = _claim(tmp_path / "convergence.sqlite3", "hsguru_meta_standard_legend")
    with (
        patch("app.parser_control.execute_parser_run") as execute,
        pytest.raises(ValueError, match="not eligible"),
    ):
        asyncio.run(
            execute_direct_candidate_confirmation(
                claim,
                telemetry_path=tmp_path / "telemetry.sqlite3",
            )
        )
    execute.assert_not_called()
