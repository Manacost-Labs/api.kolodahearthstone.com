from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.convergence_policy import decide_recovery
from app.convergence_store import ConvergenceStore


def test_initialize_creates_bounded_convergence_schema(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    ConvergenceStore(path).initialize()

    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(convergence_attempts)"
        ).fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO convergence_chains (
                    chain_id, policy_version, cohort_id, origin_occurrence_id,
                    action, reason_class, state, delays_seconds_json,
                    paid_fetch_allowed, deadline_at, last_outcome,
                    last_reason_code, created_at, updated_at
                ) VALUES (
                    'invalid', 1, 'cohort', 'origin', 'retry', 'transport',
                    'not-a-state', '[]', 0, 1, 'failed', 'transport', 1, 1
                )
                """
            )

    assert {
        "convergence_chains",
        "convergence_chain_sources",
        "convergence_attempts",
    }.issubset(tables)
    assert foreign_keys


def _create_transport_chain(path: Path):
    observed = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    return ConvergenceStore(path).create_or_get_chain(
        cohort_id="hsguru-meta-slices",
        source_ids=["hsguru_meta_standard_legend"],
        origin_occurrence_id="schedule:20260820T100000Z",
        decision=decide_recovery(outcome="failed", reason_code="transport"),
        outcome="failed",
        reason_code="transport",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=12),
    )


def test_create_chain_is_deterministic_and_survives_store_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"

    first = _create_transport_chain(path)
    repeated = _create_transport_chain(path)
    restored = ConvergenceStore(path).get_chain(first.chain_id)

    assert repeated.chain_id == first.chain_id
    assert restored == first
    assert first.source_ids == ("hsguru_meta_standard_legend",)
    assert first.state == "waiting"
    assert first.delays_seconds == (300, 1200, 3600)
    assert first.next_attempt_at == datetime(2026, 8, 20, 12, 5, tzinfo=UTC)
    assert first.paid_fetch_allowed is True
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM convergence_chains"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM convergence_chain_sources"
        ).fetchone() == (1,)


def test_repeated_occurrence_merges_sources_only_within_the_same_cohort(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    first = _create_transport_chain(path)
    observed = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    merged = ConvergenceStore(path).create_or_get_chain(
        cohort_id="hsguru-meta-slices",
        source_ids=["hsguru_meta_standard_diamond_4to1"],
        origin_occurrence_id="schedule:20260820T100000Z",
        decision=decide_recovery(outcome="failed", reason_code="transport"),
        outcome="failed",
        reason_code="transport",
        observed_at=observed,
        deadline_at=observed + timedelta(hours=12),
    )

    assert merged.chain_id == first.chain_id
    assert set(merged.source_ids) == {
        "hsguru_meta_standard_legend",
        "hsguru_meta_standard_diamond_4to1",
    }
    with pytest.raises(ValueError, match="selected recovery cohort"):
        ConvergenceStore(path).create_or_get_chain(
            cohort_id="hsguru-meta-slices",
            source_ids=["metastats_decks"],
            origin_occurrence_id="schedule:20260820T100000Z",
            decision=decide_recovery(outcome="failed", reason_code="transport"),
            outcome="failed",
            reason_code="transport",
            observed_at=observed,
            deadline_at=observed + timedelta(hours=12),
        )


def test_concurrent_creation_returns_one_chain(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"

    with ThreadPoolExecutor(max_workers=6) as pool:
        chains = list(pool.map(lambda _index: _create_transport_chain(path), range(12)))

    assert len({chain.chain_id for chain in chains}) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM convergence_chains"
        ).fetchone() == (1,)


def test_fresh_outcome_never_creates_a_chain(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="Fresh outcomes"):
        ConvergenceStore(tmp_path / "parser-telemetry.sqlite3").create_or_get_chain(
            cohort_id="hsguru-meta-slices",
            source_ids=["hsguru_meta_standard_legend"],
            origin_occurrence_id="schedule:20260820T100000Z",
            decision=decide_recovery(outcome="fresh_published"),
            outcome="fresh_published",
            reason_code="none",
            observed_at=observed,
            deadline_at=observed + timedelta(hours=12),
        )


def test_only_one_worker_can_claim_a_due_chain(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    _create_transport_chain(path)
    due_at = datetime(2026, 8, 20, 12, 5, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(
            pool.map(
                lambda index: ConvergenceStore(path).claim_due(
                    owner=f"worker-{index}",
                    now=due_at,
                ),
                range(12),
            )
        )

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].attempt_number == 1
    assert claimed[0].chain.state == "running"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM convergence_attempts"
        ).fetchone() == (1,)


def test_expired_lease_is_recovered_as_the_next_attempt(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _create_transport_chain(path)
    first_due = datetime(2026, 8, 20, 12, 5, tzinfo=UTC)
    first = ConvergenceStore(path).claim_due(
        owner="worker-first",
        now=first_due,
        lease_seconds=60,
    )

    recovered = ConvergenceStore(path).claim_due(
        owner="worker-second",
        now=first_due + timedelta(minutes=2),
        lease_seconds=60,
    )

    assert first is not None
    assert recovered is not None
    assert recovered.chain.chain_id == chain.chain_id
    assert recovered.attempt_number == 2
    with sqlite3.connect(path) as connection:
        attempts = connection.execute(
            """
            SELECT attempt_number, state, reason_code
            FROM convergence_attempts
            ORDER BY attempt_number
            """
        ).fetchall()
    assert attempts == [(1, "failed", "lease_expired"), (2, "running", None)]


def test_chain_past_deadline_is_exhausted_without_a_claim(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _create_transport_chain(path)

    claim = ConvergenceStore(path).claim_due(
        owner="worker",
        now=datetime(2026, 8, 21, 1, 0, tzinfo=UTC),
    )
    exhausted = ConvergenceStore(path).get_chain(chain.chain_id)

    assert claim is None
    assert exhausted is not None
    assert exhausted.state == "exhausted"
    assert exhausted.next_attempt_at is None


def test_fresh_attempt_completes_chain_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _create_transport_chain(path)
    due_at = datetime(2026, 8, 20, 12, 5, tzinfo=UTC)
    claim = ConvergenceStore(path).claim_due(owner="worker", now=due_at)
    assert claim is not None

    finished = ConvergenceStore(path).finish_attempt(
        attempt_id=claim.attempt_id,
        owner="worker",
        outcome="fresh_published",
        reason_code="none",
        decision=decide_recovery(outcome="fresh_published"),
        finished_at=due_at + timedelta(minutes=1),
        execution_succeeded=True,
        parser_run_id="parser-control:run-1",
        paid_requests=1,
        paid_cost_microusd=1500,
    )
    repeated = ConvergenceStore(path).finish_attempt(
        attempt_id=claim.attempt_id,
        owner="worker",
        outcome="fresh_published",
        reason_code="none",
        decision=decide_recovery(outcome="fresh_published"),
        finished_at=due_at + timedelta(minutes=1),
        execution_succeeded=True,
        parser_run_id="parser-control:run-1",
    )

    assert finished.chain_id == chain.chain_id
    assert finished.state == "fresh"
    assert finished.next_attempt_at is None
    assert finished.paid_requests_total == 1
    assert finished.paid_cost_microusd_total == 1500
    assert repeated == finished
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            """
            SELECT state, parser_run_id, outcome
            FROM convergence_attempts
            """
        ).fetchone()
    assert stored == ("succeeded", "parser-control:run-1", "fresh_published")


def test_transport_failure_reschedules_then_exhausts_bounded_attempts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    chain = _create_transport_chain(path)
    store = ConvergenceStore(path)
    due_at = datetime(2026, 8, 20, 12, 5, tzinfo=UTC)
    decision = decide_recovery(outcome="failed", reason_code="transport")

    first = store.claim_due(owner="worker", now=due_at)
    assert first is not None
    after_first = store.finish_attempt(
        attempt_id=first.attempt_id,
        owner="worker",
        outcome="failed",
        reason_code="transport",
        decision=decision,
        finished_at=due_at + timedelta(minutes=1),
        execution_succeeded=False,
    )
    assert after_first.next_attempt_at == datetime(
        2026, 8, 20, 12, 26, tzinfo=UTC
    )

    second = store.claim_due(owner="worker", now=after_first.next_attempt_at)
    assert second is not None
    after_second = store.finish_attempt(
        attempt_id=second.attempt_id,
        owner="worker",
        outcome="failed",
        reason_code="transport",
        decision=decision,
        finished_at=after_first.next_attempt_at + timedelta(minutes=1),
        execution_succeeded=False,
    )
    assert after_second.next_attempt_at == datetime(
        2026, 8, 20, 13, 27, tzinfo=UTC
    )

    third = store.claim_due(owner="worker", now=after_second.next_attempt_at)
    assert third is not None
    exhausted = store.finish_attempt(
        attempt_id=third.attempt_id,
        owner="worker",
        outcome="failed",
        reason_code="transport",
        decision=decision,
        finished_at=after_second.next_attempt_at + timedelta(minutes=1),
        execution_succeeded=False,
    )

    assert exhausted.chain_id == chain.chain_id
    assert exhausted.state == "exhausted"
    assert exhausted.next_attempt_at is None


def test_unpaid_probe_rejects_provider_usage_and_wrong_owner(tmp_path: Path) -> None:
    path = tmp_path / "parser-telemetry.sqlite3"
    observed = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    store = ConvergenceStore(path)
    store.create_or_get_chain(
        cohort_id="vicious-reports",
        source_ids=["vicious_syndicate_radars"],
        origin_occurrence_id="schedule:20260820T100000Z",
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
    with pytest.raises(PermissionError, match="another worker"):
        store.finish_attempt(
            attempt_id=claim.attempt_id,
            owner="wrong-worker",
            outcome="lkg_served",
            reason_code="unavailable",
            decision=decide_recovery(
                outcome="lkg_served",
                reason_code="unavailable",
                upstream_pending=True,
            ),
            finished_at=observed + timedelta(minutes=31),
            execution_succeeded=True,
        )
    with pytest.raises(ValueError, match="cannot spend provider credits"):
        store.finish_attempt(
            attempt_id=claim.attempt_id,
            owner="probe-worker",
            outcome="lkg_served",
            reason_code="unavailable",
            decision=decide_recovery(
                outcome="lkg_served",
                reason_code="unavailable",
                upstream_pending=True,
            ),
            finished_at=observed + timedelta(minutes=31),
            execution_succeeded=True,
            paid_requests=1,
        )
