"""SQLite storage for HSReplay card choice statistics (what players pick).

One *target* is one ``single_card_choices_by_winrate_v2`` request: one card
in one format at one rank. Targets keep the incremental state (last read,
failures, when due again). A read whose numbers changed writes an immutable
*snapshot* with its option *rows* in one transaction; an unchanged read only
moves ``last_success_at``.

Every table has a monotonic integer ``id`` primary key so the platform's
incremental analytics delta (``platform/scripts/stage-analytics.py``) can mirror
it into PostgreSQL ``analytics.*``. Column order must match
``platform/sql/020_hsreplay_card_choices.sql``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

TARGETS = "hsreplay_card_choice_targets"
SNAPSHOTS = "hsreplay_card_choice_snapshots"
ROWS = "hsreplay_card_choice_rows"
RUNS = "hsreplay_card_choice_runs"
TABLES = (TARGETS, SNAPSHOTS, ROWS, RUNS)

SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {TARGETS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key TEXT NOT NULL UNIQUE,
    card_id TEXT NOT NULL,
    dbf_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    rank TEXT NOT NULL,
    kind TEXT NOT NULL,
    tier INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    times_played INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    planned_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_changed_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_after TEXT,
    latest_snapshot_id INTEGER,
    last_error TEXT
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {SNAPSHOTS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES {TARGETS}(id),
    card_id TEXT NOT NULL,
    dbf_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    rank TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    as_of TEXT,
    rows_total INTEGER NOT NULL,
    unidentified_rows INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    backend TEXT NOT NULL,
    request_credits INTEGER NOT NULL DEFAULT 0
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {ROWS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES {SNAPSHOTS}(id),
    position INTEGER NOT NULL,
    dbf_id INTEGER NOT NULL,
    card_id TEXT,
    popularity REAL,
    win_rate REAL,
    UNIQUE (snapshot_id, position)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {RUNS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL,
    premium_state TEXT,
    targets_planned INTEGER NOT NULL DEFAULT 0,
    targets_due INTEGER NOT NULL DEFAULT 0,
    attempted INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    no_data INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    scrape_do_credits INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    detail_json TEXT
)
""",
    f"CREATE INDEX IF NOT EXISTS {TARGETS}_card_idx ON {TARGETS} (card_id, format, rank)",
    f"CREATE INDEX IF NOT EXISTS {SNAPSHOTS}_target_idx ON {SNAPSHOTS} (target_id, id)",
    f"CREATE INDEX IF NOT EXISTS {ROWS}_snapshot_idx ON {ROWS} (snapshot_id, position)",
    f"CREATE INDEX IF NOT EXISTS {RUNS}_started_idx ON {RUNS} (started_at)",
)


def ensure_hsreplay_card_choices_schema(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        conn.execute(statement)
    conn.commit()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def sync_targets(
    conn: sqlite3.Connection, planned: Iterable[Mapping[str, Any]], *, planned_at: str
) -> None:
    """Upsert the planned targets; targets no longer planned become inactive."""

    keys: list[str] = []
    with conn:
        for target in planned:
            keys.append(str(target["target_key"]))
            conn.execute(
                f"""
                INSERT INTO {TARGETS} (
                    target_key, card_id, dbf_id, format, rank, kind, tier, priority,
                    times_played, active, planned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (target_key) DO UPDATE SET
                    dbf_id = excluded.dbf_id,
                    kind = excluded.kind,
                    tier = excluded.tier,
                    priority = excluded.priority,
                    times_played = excluded.times_played,
                    active = 1,
                    planned_at = excluded.planned_at
                """,
                (
                    target["target_key"],
                    target["card_id"],
                    int(target["dbf_id"]),
                    target["format"],
                    target["rank"],
                    target["kind"],
                    int(target["tier"]),
                    int(target["priority"]),
                    target.get("times_played"),
                    planned_at,
                ),
            )
        if keys:
            placeholders = ",".join("?" for _ in keys)
            conn.execute(
                f"UPDATE {TARGETS} SET active = 0 WHERE target_key NOT IN ({placeholders})",
                keys,
            )
        else:
            conn.execute(f"UPDATE {TARGETS} SET active = 0")


def active_targets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(f"SELECT * FROM {TARGETS} WHERE active = 1 ORDER BY id")
    ]


def _latest_hash(conn: sqlite3.Connection, snapshot_id: int | None) -> str | None:
    if snapshot_id is None:
        return None
    row = conn.execute(
        f"SELECT content_hash FROM {SNAPSHOTS} WHERE id = ?", (snapshot_id,)
    ).fetchone()
    return str(row[0]) if row else None


def record_success(
    conn: sqlite3.Connection,
    target: Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | None,
    rows: list[Mapping[str, Any]],
    unidentified_rows: int,
    content_hash: str,
    source_url: str,
    backend: str,
    request_credits: int,
    state: str,
) -> tuple[int | None, bool]:
    """Store one read; a new snapshot only when the numbers changed."""

    with conn:
        current = conn.execute(
            f"SELECT id, latest_snapshot_id FROM {TARGETS} WHERE target_key = ?",
            (target["target_key"],),
        ).fetchone()
        if current is None:
            raise LookupError(f"unknown target {target['target_key']}")
        target_id, latest_id = int(current["id"]), current["latest_snapshot_id"]
        changed = _latest_hash(conn, latest_id) != content_hash
        snapshot_id = latest_id
        if changed:
            cursor = conn.execute(
                f"""
                INSERT INTO {SNAPSHOTS} (
                    target_id, card_id, dbf_id, format, rank, fetched_at, as_of,
                    rows_total, unidentified_rows, content_hash, source_url, backend,
                    request_credits
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    target["card_id"],
                    int(target["dbf_id"]),
                    target["format"],
                    target["rank"],
                    fetched_at,
                    as_of,
                    len(rows),
                    unidentified_rows,
                    content_hash,
                    source_url,
                    backend,
                    int(request_credits or 0),
                ),
            )
            snapshot_id = int(cursor.lastrowid or 0)
            conn.executemany(
                f"""
                INSERT INTO {ROWS} (
                    snapshot_id, position, dbf_id, card_id, popularity, win_rate
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        position,
                        int(row["dbf_id"]),
                        row.get("card_id"),
                        row.get("popularity"),
                        row.get("win_rate"),
                    )
                    for position, row in enumerate(rows)
                ],
            )
        conn.execute(
            f"""
            UPDATE {TARGETS} SET
                state = ?, last_attempt_at = ?, last_success_at = ?,
                last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END,
                consecutive_failures = 0, next_attempt_after = NULL,
                latest_snapshot_id = ?, last_error = NULL
            WHERE id = ?
            """,
            (
                state,
                fetched_at,
                fetched_at,
                int(changed),
                fetched_at,
                snapshot_id,
                target_id,
            ),
        )
    return snapshot_id, changed


def record_failure(
    conn: sqlite3.Connection,
    target: Mapping[str, Any],
    *,
    attempted_at: str,
    state: str,
    error: str,
    retry_after: str,
    count_failure: bool = True,
) -> None:
    with conn:
        conn.execute(
            f"""
            UPDATE {TARGETS} SET
                state = ?, last_attempt_at = ?, last_error = ?, next_attempt_after = ?,
                consecutive_failures = consecutive_failures + ?
            WHERE target_key = ?
            """,
            (
                state,
                attempted_at,
                error[:500],
                retry_after,
                int(count_failure),
                target["target_key"],
            ),
        )


def scrape_do_credits_since(conn: sqlite3.Connection, since: str) -> int:
    row = conn.execute(
        f"SELECT COALESCE(SUM(scrape_do_credits), 0) FROM {RUNS} WHERE started_at >= ?",
        (since,),
    ).fetchone()
    return int(row[0] or 0)


def start_run(conn: sqlite3.Connection, *, started_at: str) -> int:
    with conn:
        cursor = conn.execute(
            f"INSERT INTO {RUNS} (started_at, state) VALUES (?, 'running')",
            (started_at,),
        )
    return int(cursor.lastrowid or 0)


def finish_run(
    conn: sqlite3.Connection, run_id: int, summary: Mapping[str, Any]
) -> None:
    columns = (
        "finished_at",
        "state",
        "premium_state",
        "targets_planned",
        "targets_due",
        "attempted",
        "changed",
        "unchanged",
        "no_data",
        "failed",
        "requests",
        "scrape_do_credits",
        "stop_reason",
    )
    with conn:
        conn.execute(
            f"UPDATE {RUNS} SET "
            + ", ".join(f"{column} = ?" for column in columns)
            + ", detail_json = ? WHERE id = ?",
            (
                *(summary.get(column) for column in columns),
                json.dumps(
                    summary.get("detail") or {}, ensure_ascii=False, default=str
                ),
                run_id,
            ),
        )


def latest_finished_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {RUNS} WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["detail"] = json.loads(run.pop("detail_json") or "{}")
    return run


def prune(conn: sqlite3.Connection, *, now: datetime, snapshot_days: int) -> int:
    """Drop snapshots older than the window unless a target still points at them."""

    cutoff = (now - timedelta(days=snapshot_days)).isoformat()
    with conn:
        old = [
            int(row[0])
            for row in conn.execute(
                f"""
                SELECT id FROM {SNAPSHOTS}
                WHERE fetched_at < ?
                  AND id NOT IN (
                      SELECT latest_snapshot_id FROM {TARGETS}
                      WHERE latest_snapshot_id IS NOT NULL
                  )
                """,
                (cutoff,),
            )
        ]
        for snapshot_id in old:
            conn.execute(f"DELETE FROM {ROWS} WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute(f"DELETE FROM {SNAPSHOTS} WHERE id = ?", (snapshot_id,))
    return len(old)


def card_targets(conn: sqlite3.Connection, card_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {TARGETS} WHERE card_id = ? ORDER BY format, rank",
            (card_id,),
        )
    ]


def snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            f"SELECT * FROM {SNAPSHOTS} WHERE id = ?", (snapshot_id,)
        ).fetchone()
    )


def snapshot_rows(conn: sqlite3.Connection, snapshot_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT position, dbf_id, card_id, popularity, win_rate
            FROM {ROWS} WHERE snapshot_id = ? ORDER BY position
            """,
            (snapshot_id,),
        )
    ]


def coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT format, rank, active, COUNT(*) AS targets,
                   SUM(CASE WHEN latest_snapshot_id IS NOT NULL THEN 1 ELSE 0 END)
                       AS with_data,
                   SUM(CASE WHEN state = 'no_data' THEN 1 ELSE 0 END) AS no_data,
                   MAX(last_success_at) AS last_success_at
            FROM {TARGETS} GROUP BY format, rank, active ORDER BY format, rank, active
            """
        )
    ]


def collected_cards(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Per card id: every stored format/rank with its state and check time."""

    found: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
        f"""
        SELECT card_id, format, rank, state, last_success_at, active
        FROM {TARGETS} WHERE latest_snapshot_id IS NOT NULL
        ORDER BY card_id, format, rank
        """
    ):
        found.setdefault(str(row["card_id"]), []).append(dict(row))
    return found


def latest_revision(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        f"""
        SELECT (SELECT COALESCE(MAX(id), 0) FROM {SNAPSHOTS}) || ':' ||
               (SELECT COALESCE(MAX(last_success_at), '') FROM {TARGETS}) || ':' ||
               (SELECT COALESCE(MAX(id), 0) FROM {RUNS} WHERE finished_at IS NOT NULL)
        """
    ).fetchone()
    return str(row[0]) if row and row[0] else None
