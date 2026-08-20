from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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
