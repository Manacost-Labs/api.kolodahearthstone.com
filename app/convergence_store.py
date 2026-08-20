"""Durable SQLite state for freshness convergence chains."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .reliability_telemetry import telemetry_db_path

CONVERGENCE_POLICY_VERSION = 1
CHAIN_STATES = (
    "waiting",
    "running",
    "fresh",
    "upstream_pending",
    "paused",
    "quarantined",
    "diagnosis_required",
    "exhausted",
    "cancelled",
)
ATTEMPT_STATES = ("queued", "running", "succeeded", "failed", "cancelled")

_schema_lock = threading.Lock()


class ConvergenceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else telemetry_db_path()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        connection = self._connect()
        try:
            _ensure_schema(connection)
        finally:
            connection.close()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    with _schema_lock:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).casefold():
                raise
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS convergence_chains (
                    chain_id TEXT PRIMARY KEY,
                    policy_version INTEGER NOT NULL,
                    cohort_id TEXT NOT NULL,
                    origin_occurrence_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason_class TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'waiting', 'running', 'fresh', 'upstream_pending',
                            'paused', 'quarantined', 'diagnosis_required',
                            'exhausted', 'cancelled'
                        )
                    ),
                    delays_seconds_json TEXT NOT NULL,
                    paid_fetch_allowed INTEGER NOT NULL CHECK (
                        paid_fetch_allowed IN (0, 1)
                    ),
                    attempt_index INTEGER NOT NULL DEFAULT 0 CHECK (
                        attempt_index >= 0
                    ),
                    next_attempt_at REAL,
                    deadline_at REAL NOT NULL,
                    last_outcome TEXT NOT NULL,
                    last_reason_code TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE (policy_version, cohort_id, origin_occurrence_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS convergence_chain_sources (
                    chain_id TEXT NOT NULL REFERENCES convergence_chains(chain_id)
                        ON DELETE CASCADE,
                    source_id TEXT NOT NULL,
                    PRIMARY KEY (chain_id, source_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS convergence_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL REFERENCES convergence_chains(chain_id)
                        ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
                    action TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
                    ),
                    parser_run_id TEXT,
                    outcome TEXT,
                    reason_code TEXT,
                    paid_requests INTEGER NOT NULL DEFAULT 0 CHECK (paid_requests >= 0),
                    paid_cost_microusd INTEGER NOT NULL DEFAULT 0 CHECK (
                        paid_cost_microusd >= 0
                    ),
                    started_at REAL,
                    finished_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE (chain_id, attempt_number)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS convergence_chains_due_idx
                ON convergence_chains (state, next_attempt_at, lease_until)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS convergence_attempts_chain_idx
                ON convergence_attempts (chain_id, attempt_number)
                """
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
