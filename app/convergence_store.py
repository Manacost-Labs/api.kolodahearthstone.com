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
    paid_requests_total: int
    paid_cost_microusd_total: int
    attempt_index: int
    next_attempt_at: datetime | None
    deadline_at: datetime
    last_outcome: str
    last_reason_code: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConvergenceClaim:
    chain: ConvergenceChain
    attempt_id: str
    attempt_number: int
    lease_owner: str
    lease_until: datetime


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

    def planner_cursor(self) -> tuple[float, str] | None:
        connection = self._connect()
        try:
            _ensure_schema(connection)
            row = connection.execute(
                """
                SELECT last_finished_at, last_attempt_id
                FROM convergence_planner_state
                WHERE singleton = 1
                """
            ).fetchone()
            if row is None:
                return None
            return float(row[0]), str(row[1])
        finally:
            connection.close()

    def advance_planner_cursor(
        self,
        *,
        finished_at: float,
        attempt_id: str,
        updated_at: datetime,
    ) -> None:
        normalized_attempt_id = _identifier(attempt_id, field="attempt_id")
        moment = _as_utc(updated_at, field="updated_at")
        connection = self._connect()
        try:
            _ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT last_finished_at, last_attempt_id
                FROM convergence_planner_state
                WHERE singleton = 1
                """
            ).fetchone()
            candidate = (float(finished_at), normalized_attempt_id)
            current = (
                (float(existing[0]), str(existing[1]))
                if existing is not None
                else None
            )
            if current is None or candidate > current:
                connection.execute(
                    """
                    INSERT INTO convergence_planner_state (
                        singleton, last_finished_at, last_attempt_id, updated_at
                    ) VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        last_finished_at = excluded.last_finished_at,
                        last_attempt_id = excluded.last_attempt_id,
                        updated_at = excluded.updated_at
                    """,
                    (candidate[0], candidate[1], moment.timestamp()),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int = 5 * 60,
    ) -> ConvergenceClaim | None:
        lease_owner = _identifier(owner, field="owner")
        moment = _as_utc(now, field="now")
        if not 30 <= lease_seconds <= 60 * 60:
            raise ValueError("lease_seconds must be between 30 and 3600")
        now_epoch = moment.timestamp()
        lease_until_epoch = now_epoch + lease_seconds

        connection = self._connect()
        connection.row_factory = sqlite3.Row
        try:
            _ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            expired_running = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT chain_id
                    FROM convergence_chains
                    WHERE state = 'running'
                      AND lease_until IS NOT NULL
                      AND lease_until <= ?
                    """,
                    (now_epoch,),
                )
            ]
            for chain_id in expired_running:
                connection.execute(
                    """
                    UPDATE convergence_attempts
                    SET state = 'failed', reason_code = 'lease_expired',
                        finished_at = ?, updated_at = ?
                    WHERE chain_id = ? AND state = 'running'
                    """,
                    (now_epoch, now_epoch, chain_id),
                )
                connection.execute(
                    """
                    UPDATE convergence_chains
                    SET state = 'waiting', next_attempt_at = ?,
                        lease_owner = NULL, lease_until = NULL, updated_at = ?
                    WHERE chain_id = ? AND state = 'running'
                    """,
                    (now_epoch, now_epoch, chain_id),
                )

            connection.execute(
                """
                UPDATE convergence_chains
                SET state = 'exhausted', next_attempt_at = NULL,
                    lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE state IN ('waiting', 'upstream_pending', 'running')
                  AND deadline_at <= ?
                """,
                (now_epoch, now_epoch),
            )
            row = connection.execute(
                """
                SELECT chain_id, attempt_index, action
                FROM convergence_chains
                WHERE state IN ('waiting', 'upstream_pending')
                  AND next_attempt_at IS NOT NULL
                  AND next_attempt_at <= ?
                  AND deadline_at > ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_attempt_at, created_at, chain_id
                LIMIT 1
                """,
                (now_epoch, now_epoch, now_epoch),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            chain_id = str(row["chain_id"])
            attempt_number = int(row["attempt_index"]) + 1
            attempt_id = hashlib.sha256(
                f"{chain_id}\0{attempt_number}".encode("utf-8")
            ).hexdigest()[:32]
            updated = connection.execute(
                """
                UPDATE convergence_chains
                SET state = 'running', attempt_index = ?,
                    next_attempt_at = NULL, lease_owner = ?, lease_until = ?,
                    updated_at = ?
                WHERE chain_id = ?
                  AND state IN ('waiting', 'upstream_pending')
                  AND (lease_until IS NULL OR lease_until <= ?)
                """,
                (
                    attempt_number,
                    lease_owner,
                    lease_until_epoch,
                    now_epoch,
                    chain_id,
                    now_epoch,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                """
                INSERT INTO convergence_attempts (
                    attempt_id, chain_id, attempt_number, action, state, worker_id,
                    started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    chain_id,
                    attempt_number,
                    str(row["action"]),
                    lease_owner,
                    now_epoch,
                    now_epoch,
                    now_epoch,
                ),
            )
            connection.commit()
            chain = self._get_chain(connection, chain_id)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        if chain is None:
            raise RuntimeError("Claimed convergence chain disappeared")
        return ConvergenceClaim(
            chain=chain,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            lease_owner=lease_owner,
            lease_until=_from_epoch(lease_until_epoch),
        )

    def finish_attempt(
        self,
        *,
        attempt_id: str,
        owner: str,
        outcome: str,
        reason_code: str,
        decision: RecoveryDecision,
        finished_at: datetime,
        execution_succeeded: bool,
        parser_run_id: str | None = None,
        paid_requests: int = 0,
        paid_cost_microusd: int = 0,
    ) -> ConvergenceChain:
        normalized_attempt_id = _identifier(attempt_id, field="attempt_id")
        lease_owner = _identifier(owner, field="owner")
        moment = _as_utc(finished_at, field="finished_at")
        normalized_outcome = _bounded_label(outcome, field="outcome")
        normalized_reason = _bounded_label(reason_code, field="reason_code")
        normalized_parser_run_id = (
            _identifier(parser_run_id, field="parser_run_id")
            if parser_run_id is not None
            else None
        )
        if paid_requests < 0 or paid_cost_microusd < 0:
            raise ValueError("Paid usage must be non-negative")
        now_epoch = moment.timestamp()

        connection = self._connect()
        connection.row_factory = sqlite3.Row
        try:
            _ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    attempts.chain_id,
                    attempts.attempt_number,
                    attempts.state AS attempt_state,
                    attempts.worker_id,
                    chains.deadline_at,
                    chains.paid_fetch_allowed,
                    chains.lease_owner,
                    chains.lease_until
                FROM convergence_attempts AS attempts
                JOIN convergence_chains AS chains USING (chain_id)
                WHERE attempts.attempt_id = ?
                """,
                (normalized_attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Convergence attempt was not found")
            chain_id = str(row["chain_id"])
            if str(row["worker_id"] or "") != lease_owner:
                raise PermissionError("Convergence attempt is owned by another worker")
            if str(row["attempt_state"]) != "running":
                connection.commit()
                chain = self._get_chain(connection, chain_id)
                if chain is None:
                    raise RuntimeError("Completed convergence chain disappeared")
                return chain
            if str(row["lease_owner"] or "") != lease_owner:
                raise PermissionError("Convergence lease is owned by another worker")
            if row["lease_until"] is None or float(row["lease_until"]) <= now_epoch:
                raise PermissionError("Convergence lease has expired")
            if paid_requests and not bool(row["paid_fetch_allowed"]):
                raise ValueError("This convergence attempt cannot spend provider credits")
            if decision.action == "complete" and not execution_succeeded:
                raise ValueError("A failed execution cannot complete a convergence chain")

            attempt_number = int(row["attempt_number"])
            delays = tuple(int(delay) for delay in decision.delays_seconds)
            if decision.action == "complete":
                state = "fresh"
                next_attempt_at = None
            elif decision.action in {
                "pause_provider",
                "quarantine",
                "diagnose",
            }:
                state = _initial_state(decision.action)
                next_attempt_at = None
            elif attempt_number >= len(delays):
                state = "exhausted"
                next_attempt_at = None
            else:
                candidate_next = now_epoch + delays[attempt_number]
                if candidate_next >= float(row["deadline_at"]):
                    state = "exhausted"
                    next_attempt_at = None
                else:
                    state = _initial_state(decision.action)
                    next_attempt_at = candidate_next

            connection.execute(
                """
                UPDATE convergence_attempts
                SET state = ?, parser_run_id = ?, outcome = ?, reason_code = ?,
                    paid_requests = ?, paid_cost_microusd = ?,
                    finished_at = ?, updated_at = ?
                WHERE attempt_id = ? AND state = 'running'
                """,
                (
                    "succeeded" if execution_succeeded else "failed",
                    normalized_parser_run_id,
                    normalized_outcome,
                    normalized_reason,
                    paid_requests,
                    paid_cost_microusd,
                    now_epoch,
                    now_epoch,
                    normalized_attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE convergence_chains
                SET action = ?, reason_class = ?, state = ?,
                    delays_seconds_json = ?, paid_fetch_allowed = ?,
                    paid_requests_total = paid_requests_total + ?,
                    paid_cost_microusd_total = paid_cost_microusd_total + ?,
                    next_attempt_at = ?, last_outcome = ?, last_reason_code = ?,
                    lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE chain_id = ? AND state = 'running' AND lease_owner = ?
                """,
                (
                    decision.action,
                    decision.reason_class,
                    state,
                    json.dumps(delays, separators=(",", ":")),
                    int(decision.paid_fetch_allowed),
                    paid_requests,
                    paid_cost_microusd,
                    next_attempt_at,
                    normalized_outcome,
                    normalized_reason,
                    now_epoch,
                    chain_id,
                    lease_owner,
                ),
            )
            connection.commit()
            chain = self._get_chain(connection, chain_id)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        if chain is None:
            raise RuntimeError("Completed convergence chain disappeared")
        return chain

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
            paid_requests_total=int(row["paid_requests_total"]),
            paid_cost_microusd_total=int(row["paid_cost_microusd_total"]),
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
                    paid_requests_total INTEGER NOT NULL DEFAULT 0 CHECK (
                        paid_requests_total >= 0
                    ),
                    paid_cost_microusd_total INTEGER NOT NULL DEFAULT 0 CHECK (
                        paid_cost_microusd_total >= 0
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
            chain_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(convergence_chains)"
                )
            }
            if "paid_requests_total" not in chain_columns:
                connection.execute(
                    "ALTER TABLE convergence_chains "
                    "ADD COLUMN paid_requests_total INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (paid_requests_total >= 0)"
                )
            if "paid_cost_microusd_total" not in chain_columns:
                connection.execute(
                    "ALTER TABLE convergence_chains "
                    "ADD COLUMN paid_cost_microusd_total INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (paid_cost_microusd_total >= 0)"
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
                    worker_id TEXT NOT NULL DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS convergence_planner_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    last_finished_at REAL NOT NULL,
                    last_attempt_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            attempt_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(convergence_attempts)"
                )
            }
            if "worker_id" not in attempt_columns:
                connection.execute(
                    "ALTER TABLE convergence_attempts "
                    "ADD COLUMN worker_id TEXT NOT NULL DEFAULT ''"
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
