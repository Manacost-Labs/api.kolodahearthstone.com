from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import data_dir
from ..storage import _assert_not_test_prod_write

SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
 page_type TEXT NOT NULL, format TEXT NOT NULL, rank TEXT NOT NULL, period TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, duration_ms REAL,
 job_class TEXT NOT NULL, reserved REAL NOT NULL, credits REAL,
 cost_basis TEXT NOT NULL, outcome TEXT NOT NULL, validation TEXT NOT NULL DEFAULT 'pending',
 http_status INTEGER
);
CREATE INDEX IF NOT EXISTS attempts_month ON attempts(started_at);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, format TEXT NOT NULL, rank TEXT NOT NULL,
 period TEXT NOT NULL, fetched_at TEXT NOT NULL, checked_at TEXT NOT NULL,
 valid INTEGER NOT NULL, reason TEXT NOT NULL,
 UNIQUE(source_id, format, rank, period, fetched_at, reason)
);
CREATE TABLE IF NOT EXISTS snapshots (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, format TEXT NOT NULL, rank TEXT NOT NULL,
 period TEXT NOT NULL, patch TEXT NOT NULL, fetched_at TEXT NOT NULL,
 parser_version TEXT NOT NULL, payload_hash TEXT NOT NULL, coverage REAL NOT NULL,
 expected_hours REAL NOT NULL, acquisition_cost REAL, duration_ms REAL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(source_id, format, rank, period, fetched_at, payload_hash)
);
CREATE INDEX IF NOT EXISTS snapshots_slice ON snapshots(source_id, format, rank, period, fetched_at DESC);
CREATE TABLE IF NOT EXISTS snapshot_attempts (
 snapshot_id TEXT NOT NULL REFERENCES snapshots(id), attempt_id TEXT NOT NULL REFERENCES attempts(id),
 PRIMARY KEY(snapshot_id, attempt_id)
);
CREATE TABLE IF NOT EXISTS signals (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, format TEXT NOT NULL,
 rank TEXT NOT NULL, period TEXT NOT NULL, patch TEXT NOT NULL, archetype TEXT NOT NULL,
 detected_at TEXT NOT NULL, updated_at TEXT NOT NULL, confidence REAL NOT NULL,
 rule_version TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_events (
 id INTEGER PRIMARY KEY, signal_id TEXT NOT NULL REFERENCES signals(id),
 at TEXT NOT NULL, state TEXT NOT NULL, evidence TEXT NOT NULL,
 UNIQUE(signal_id, at, state)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
 created_at TEXT NOT NULL, finished_at TEXT, state TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots
 BEGIN SELECT RAISE(ABORT, 'Snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots
 BEGIN SELECT RAISE(ABORT, 'Snapshots are append-only'); END;
PRAGMA user_version=1;
"""


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "meta-intelligence" / "lab.sqlite3"

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if write:
            _assert_not_test_prod_write(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=15)
            self.path.chmod(0o600)
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                conn.close()
                raise RuntimeError("Unsupported Lab schema version")
            if version == 0:
                conn.executescript(SCHEMA)
        elif self.path.exists():
            conn = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=15)
        else:
            conn = sqlite3.connect(":memory:")
            conn.executescript(SCHEMA)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def snapshots(self, *, format: str | None = None, rank: str | None = None,
                  period: str | None = None, source_id: str | None = None,
                  limit: int = 1000) -> list[dict]:
        clauses, args = [], []
        for key, value in (("format", format), ("rank", rank), ("period", period), ("source_id", source_id)):
            if value is not None:
                clauses.append(f"{key} = ?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM snapshots" + where + " ORDER BY fetched_at DESC, id DESC LIMIT ?", [*args, limit]).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def backup(self, destination: Path) -> None:
        if destination.exists():
            raise ValueError("Backup destination already exists")
        if not self.path.exists():
            raise ValueError("Lab database does not exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                destination.chmod(0o600)
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Lab backup integrity check failed")
            finally:
                target.close()
