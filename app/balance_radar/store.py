"""SQLite history of Balance Radar runs (``<data>/balance-radar.sqlite3``)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..storage import root_dir

DB_NAME = "balance-radar.sqlite3"
RETENTION_DAYS = 400
STATUSES = ("ok", "failed", "skipped")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    format TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'skipped')),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    generated_at TEXT,
    model TEXT,
    input_hash TEXT,
    nerfs INTEGER NOT NULL DEFAULT 0,
    buffs INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    payload TEXT,
    usage TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS runs_format_started ON runs (format, started_at DESC);
"""


def db_path() -> Path:
    return root_dir() / DB_NAME


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_run(
    *,
    run_id: str,
    format_name: str,
    status: str,
    started_at: str,
    payload: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    input_hash: str | None = None,
    error: str | None = None,
    model: str | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError("unknown run status")
    finished = datetime.now(UTC).isoformat(timespec="seconds")
    cost = None
    if usage:
        cost = usage.get("totalCostUsd")
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                id, format, status, started_at, finished_at, generated_at, model,
                input_hash, nerfs, buffs, cost_usd, payload, usage, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                format_name,
                status,
                started_at,
                finished,
                (payload or {}).get("generatedAt"),
                model or (payload or {}).get("model"),
                input_hash,
                len((payload or {}).get("nerfs") or []),
                len((payload or {}).get("buffs") or []),
                cost,
                json.dumps(payload, ensure_ascii=False)
                if payload is not None
                else None,
                json.dumps(usage, ensure_ascii=False) if usage is not None else None,
                (error or "")[:500] or None,
            ),
        )
        cutoff = (datetime.now(UTC) - timedelta(days=RETENTION_DAYS)).isoformat()
        conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))


def _row(row: sqlite3.Row, *, with_payload: bool) -> dict[str, Any]:
    item = {
        "runId": row["id"],
        "format": row["format"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "generatedAt": row["generated_at"],
        "model": row["model"],
        "nerfs": row["nerfs"],
        "buffs": row["buffs"],
        "costUsd": row["cost_usd"],
        "error": row["error"],
        "inputHash": row["input_hash"],
    }
    if with_payload:
        item["payload"] = json.loads(row["payload"]) if row["payload"] else None
        item["usage"] = json.loads(row["usage"]) if row["usage"] else None
    return item


def latest_ok(format_name: str) -> dict[str, Any] | None:
    if not db_path().exists():
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE format = ? AND status = 'ok' "
            "ORDER BY generated_at DESC, started_at DESC, rowid DESC LIMIT 1",
            (format_name,),
        ).fetchone()
    return _row(row, with_payload=True) if row else None


def latest_finished_at() -> str | None:
    """Revision for HTTP caching: changes whenever any run is stored."""
    if not db_path().exists():
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(finished_at) || ':' || COUNT(*) FROM runs"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_run(run_id: str) -> dict[str, Any] | None:
    if not db_path().exists():
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row(row, with_payload=True) if row else None


def list_runs(
    *,
    format_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if not db_path().exists():
        return [], 0
    clauses, params = [], []
    if format_name:
        clauses.append("format = ?")
        params.append(format_name)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        total = int(
            conn.execute(f"SELECT COUNT(*) FROM runs{where}", params).fetchone()[0]
        )
        rows = conn.execute(
            f"SELECT * FROM runs{where} ORDER BY started_at DESC, rowid DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [_row(row, with_payload=False) for row in rows], total


def flag_history(
    format_name: str, *, depth: int = 60
) -> dict[tuple[str, str], dict[str, Any]]:
    """For each (side, ref) flagged in the latest run: streak start and last confidence."""
    if not db_path().exists():
        return {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT generated_at, payload FROM runs WHERE format = ? AND status = 'ok' "
            "ORDER BY generated_at DESC, rowid DESC LIMIT ?",
            (format_name, depth),
        ).fetchall()
    history: dict[tuple[str, str], dict[str, Any]] = {}
    active: set[tuple[str, str]] | None = None
    for row in rows:
        payload = json.loads(row["payload"] or "{}")
        present = {
            (side, item.get("ref")): item
            for side in ("nerfs", "buffs")
            for item in payload.get(side) or []
            if isinstance(item, dict)
        }
        if active is None:
            active = set(present)
            for key, item in present.items():
                history[key] = {
                    "firstFlaggedAt": row["generated_at"],
                    "previousConfidence": item.get("confidence"),
                }
            continue
        active &= set(present)
        if not active:
            break
        for key in active:
            history[key]["firstFlaggedAt"] = row["generated_at"]
    return history


def confidence_series(
    format_name: str, *, runs: int = 30
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Confidence of every candidate across the latest successful runs, oldest first."""
    if not db_path().exists():
        return {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, generated_at, payload FROM runs WHERE format = ? AND status = 'ok' "
            "ORDER BY generated_at DESC, rowid DESC LIMIT ?",
            (format_name, runs),
        ).fetchall()
    series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in reversed(rows):
        payload = json.loads(row["payload"] or "{}")
        for side in ("nerfs", "buffs"):
            for item in payload.get(side) or []:
                if isinstance(item, dict) and item.get("ref"):
                    series.setdefault((side, item["ref"]), []).append(
                        {
                            "runId": row["id"],
                            "generatedAt": row["generated_at"],
                            "confidence": item.get("confidence"),
                        }
                    )
    return series
