"""Durable SQLite state for freshness convergence chains."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .convergence_policy import (
    RECOVERY_COHORT_BY_ID,
    RecoveryDecision,
)
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
_IDENTIFIER_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


@dataclass(frozen=True)
class ConvergenceChain:
    chain_id: str
    policy_version: int
    cohort_id: str
    source_ids: tuple[str, ...]
    origin_occurrence_id: str
    action: str
    reason_class: str
    state: str
    delays_seconds: tuple[int, ...]
    paid_fetch_allowed: bool
    attempt_index: int
    next_attempt_at: datetime | None
    deadline_at: datetime
    last_outcome: str
    last_reason_code: str
    created_at: datetime
    updated_at: datetime


def _as_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _identifier(value: object, *, field: str) -> str:
    identifier = str(value or "").strip()
    if not identifier or len(identifier) > 160 or any(
        character not in _IDENTIFIER_CHARACTERS for character in identifier
    ):
        raise ValueError(f"{field} is invalid")
    return identifier


def _bounded_label(value: object, *, field: str) -> str:
    label = str(value or "").strip().lower()
    if not label or len(label) > 80 or any(
        character not in _IDENTIFIER_CHARACTERS for character in label
    ):
        raise ValueError(f"{field} is invalid")
    return label


def _from_epoch(value: object) -> datetime:
    return datetime.fromtimestamp(float(value), tz=UTC)


def _initial_state(action: str) -> str:
    return {
        "probe_upstream": "upstream_pending",
        "pause_provider": "paused",
        "quarantine": "quarantined",
        "diagnose": "diagnosis_required",
    }.get(action, "waiting")


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

    def create_or_get_chain(
        self,
        *,
        cohort_id: str,
        source_ids: list[str] | tuple[str, ...],
        origin_occurrence_id: str,
        decision: RecoveryDecision,
        outcome: str,
        reason_code: str,
        observed_at: datetime,
        deadline_at: datetime,
    ) -> ConvergenceChain:
        cohort = RECOVERY_COHORT_BY_ID.get(cohort_id)
        if cohort is None:
            raise ValueError("cohort_id is invalid")
        selected = tuple(sorted(set(source_ids)))
        if not selected:
            raise ValueError("At least one source is required")
        if not set(selected).issubset(cohort.source_ids):
            raise ValueError("Sources must belong to the selected recovery cohort")
        if decision.action == "complete":
            raise ValueError("Fresh outcomes do not create convergence chains")

        origin = _identifier(origin_occurrence_id, field="origin_occurrence_id")
        normalized_outcome = _bounded_label(outcome, field="outcome")
        normalized_reason = _bounded_label(reason_code, field="reason_code")
        observed = _as_utc(observed_at, field="observed_at")
        deadline = _as_utc(deadline_at, field="deadline_at")
        if deadline <= observed:
            raise ValueError("deadline_at must be after observed_at")
        delays = tuple(int(delay) for delay in decision.delays_seconds)
        if any(delay < 0 for delay in delays) or tuple(sorted(delays)) != delays:
            raise ValueError("Recovery delays must be non-negative and ordered")
        next_attempt_at = observed.timestamp() + delays[0] if delays else None
        chain_id = hashlib.sha256(
            (
                f"{CONVERGENCE_POLICY_VERSION}\0{cohort_id}\0{origin}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        now_epoch = observed.timestamp()

        connection = self._connect()
        connection.row_factory = sqlite3.Row
        try:
            _ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO convergence_chains (
                    chain_id, policy_version, cohort_id, origin_occurrence_id,
                    action, reason_class, state, delays_seconds_json,
                    paid_fetch_allowed, attempt_index, next_attempt_at,
                    deadline_at, last_outcome, last_reason_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chain_id,
                    CONVERGENCE_POLICY_VERSION,
                    cohort_id,
                    origin,
                    decision.action,
                    decision.reason_class,
                    _initial_state(decision.action),
                    json.dumps(delays, separators=(",", ":")),
                    int(decision.paid_fetch_allowed),
                    next_attempt_at,
                    deadline.timestamp(),
                    normalized_outcome,
                    normalized_reason,
                    now_epoch,
                    now_epoch,
                ),
            )
            for source_id in selected:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO convergence_chain_sources (
                        chain_id, source_id
                    ) VALUES (?, ?)
                    """,
                    (chain_id, source_id),
                )
            connection.commit()
            chain = self._get_chain(connection, chain_id)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        if chain is None:
            raise RuntimeError("Convergence chain was not persisted")
        return chain

    def get_chain(self, chain_id: str) -> ConvergenceChain | None:
        normalized_chain_id = _identifier(chain_id, field="chain_id")
        connection = self._connect()
        connection.row_factory = sqlite3.Row
        try:
            _ensure_schema(connection)
            return self._get_chain(connection, normalized_chain_id)
        finally:
            connection.close()

    @staticmethod
    def _get_chain(
        connection: sqlite3.Connection,
        chain_id: str,
    ) -> ConvergenceChain | None:
        row = connection.execute(
            "SELECT * FROM convergence_chains WHERE chain_id = ?",
            (chain_id,),
        ).fetchone()
        if row is None:
            return None
        source_ids = tuple(
            str(source_row[0])
            for source_row in connection.execute(
                """
                SELECT source_id
                FROM convergence_chain_sources
                WHERE chain_id = ?
                ORDER BY source_id
                """,
                (chain_id,),
            )
        )
        raw_delays = json.loads(str(row["delays_seconds_json"]))
        delays = tuple(int(value) for value in raw_delays)
        next_attempt_at = (
            _from_epoch(row["next_attempt_at"])
            if row["next_attempt_at"] is not None
            else None
        )
        return ConvergenceChain(
            chain_id=str(row["chain_id"]),
            policy_version=int(row["policy_version"]),
            cohort_id=str(row["cohort_id"]),
            source_ids=source_ids,
            origin_occurrence_id=str(row["origin_occurrence_id"]),
            action=str(row["action"]),
            reason_class=str(row["reason_class"]),
            state=str(row["state"]),
            delays_seconds=delays,
            paid_fetch_allowed=bool(row["paid_fetch_allowed"]),
            attempt_index=int(row["attempt_index"]),
            next_attempt_at=next_attempt_at,
            deadline_at=_from_epoch(row["deadline_at"]),
            last_outcome=str(row["last_outcome"]),
            last_reason_code=str(row["last_reason_code"]),
            created_at=_from_epoch(row["created_at"]),
            updated_at=_from_epoch(row["updated_at"]),
        )


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
