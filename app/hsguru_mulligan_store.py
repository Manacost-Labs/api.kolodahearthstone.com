"""SQLite storage for HSGuru mulligan and class-matchup statistics.

One *target* is one HSGuru page for one subject (an archetype or a deck) and
one slice (format, rank, period, coin, opponent class). Targets keep the
incremental state (when the page was last read, how many games it had, when
it is due again). Every successful read that changed the page writes an
immutable *snapshot* with its per-card or per-class rows in one transaction.

Every table has a monotonic integer ``id`` primary key so the platform's
incremental analytics delta (``platform/scripts/stage-analytics.py``) can mirror
it into PostgreSQL ``analytics.*``. Column order must match
``platform/sql/017_hsguru_mulligan.sql``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

TARGETS = "hsguru_stat_targets"
SNAPSHOTS = "hsguru_stat_snapshots"
CARDS = "hsguru_mulligan_cards"
MATCHUPS = "hsguru_class_matchups"
RUNS = "hsguru_stat_runs"
TABLES = (TARGETS, SNAPSHOTS, CARDS, MATCHUPS, RUNS)

CARD_FIELDS = (
    "mulligan_impact",
    "mulligan_count",
    "drawn_impact",
    "drawn_count",
    "not_drawn_impact",
    "not_drawn_count",
    "kept_impact",
    "kept_count",
)

SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {TARGETS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('cards', 'matchups')),
    subject_type TEXT NOT NULL CHECK (subject_type IN ('archetype', 'deck')),
    format TEXT NOT NULL,
    archetype TEXT NOT NULL,
    deck_id INTEGER,
    rank TEXT NOT NULL,
    period TEXT NOT NULL,
    coin TEXT NOT NULL,
    opponent_class TEXT NOT NULL,
    tier INTEGER NOT NULL,
    expected_games INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    planned_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_changed_at TEXT,
    last_games INTEGER,
    success_expected_games INTEGER,
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
    kind TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    format TEXT NOT NULL,
    archetype TEXT NOT NULL,
    deck_id INTEGER,
    rank TEXT NOT NULL,
    period TEXT NOT NULL,
    coin TEXT NOT NULL,
    opponent_class TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    games INTEGER,
    winrate REAL,
    rows_total INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    backend TEXT NOT NULL,
    request_credits INTEGER NOT NULL DEFAULT 0
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {CARDS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES {SNAPSHOTS}(id),
    position INTEGER NOT NULL,
    card_id TEXT,
    dbf_id INTEGER,
    card_name TEXT NOT NULL,
    mulligan_impact REAL,
    mulligan_count INTEGER,
    drawn_impact REAL,
    drawn_count INTEGER,
    not_drawn_impact REAL,
    not_drawn_count INTEGER,
    kept_impact REAL,
    kept_count INTEGER,
    UNIQUE (snapshot_id, position)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {MATCHUPS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES {SNAPSHOTS}(id),
    opponent_class TEXT NOT NULL,
    winrate REAL,
    games INTEGER NOT NULL,
    share_pct REAL,
    UNIQUE (snapshot_id, opponent_class)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {RUNS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL,
    targets_planned INTEGER NOT NULL DEFAULT 0,
    targets_due INTEGER NOT NULL DEFAULT 0,
    attempted INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    no_data INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    pages_free INTEGER NOT NULL DEFAULT 0,
    pages_scrape_do INTEGER NOT NULL DEFAULT 0,
    scrape_do_credits INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    detail_json TEXT
)
""",
    (
        f"CREATE INDEX IF NOT EXISTS idx_hsguru_stat_targets_subject "
        f"ON {TARGETS}(subject_type, format, archetype, deck_id)"
    ),
    (
        f"CREATE INDEX IF NOT EXISTS idx_hsguru_stat_targets_due "
        f"ON {TARGETS}(active, tier, last_success_at)"
    ),
    (
        f"CREATE INDEX IF NOT EXISTS idx_hsguru_stat_snapshots_target "
        f"ON {SNAPSHOTS}(target_id, id)"
    ),
    f"CREATE INDEX IF NOT EXISTS idx_hsguru_mulligan_cards_snapshot ON {CARDS}(snapshot_id)",
    f"CREATE INDEX IF NOT EXISTS idx_hsguru_class_matchups_snapshot ON {MATCHUPS}(snapshot_id)",
    f"CREATE INDEX IF NOT EXISTS idx_hsguru_stat_runs_started ON {RUNS}(started_at)",
)

_PLAN_FIELDS = (
    "kind",
    "subject_type",
    "format",
    "archetype",
    "deck_id",
    "rank",
    "period",
    "coin",
    "opponent_class",
    "tier",
    "expected_games",
)


def ensure_hsguru_mulligan_schema(conn: sqlite3.Connection) -> None:
    """Create the additive HSGuru statistics tables on an existing database."""

    for statement in SCHEMA:
        conn.execute(statement)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sync_targets(
    conn: sqlite3.Connection,
    planned: Iterable[Mapping[str, Any]],
    *,
    planned_at: str,
) -> int:
    """Upsert the current plan; targets that left it stay readable but inactive."""

    keys: list[str] = []
    with conn:
        for target in planned:
            keys.append(str(target["target_key"]))
            values = [target.get(field) for field in _PLAN_FIELDS]
            conn.execute(
                f"""
                INSERT INTO {TARGETS} (target_key, {", ".join(_PLAN_FIELDS)}, active, planned_at)
                VALUES (?, {", ".join("?" for _ in _PLAN_FIELDS)}, 1, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    archetype = excluded.archetype,
                    tier = excluded.tier,
                    expected_games = excluded.expected_games,
                    active = 1,
                    planned_at = excluded.planned_at
                """,
                (target["target_key"], *values, planned_at),
            )
        conn.execute("DROP TABLE IF EXISTS temp.hsguru_planned_keys")
        conn.execute(
            "CREATE TEMP TABLE hsguru_planned_keys (target_key TEXT PRIMARY KEY)"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO temp.hsguru_planned_keys VALUES (?)",
            ((key,) for key in keys),
        )
        conn.execute(
            f"""
            UPDATE {TARGETS} SET active = 0
            WHERE active = 1
              AND target_key NOT IN (SELECT target_key FROM temp.hsguru_planned_keys)
            """
        )
        conn.execute("DROP TABLE temp.hsguru_planned_keys")
    return len(keys)


def active_targets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row) for row in conn.execute(f"SELECT * FROM {TARGETS} WHERE active = 1")
    ]


def latest_content_hash(
    conn: sqlite3.Connection, snapshot_id: int | None
) -> str | None:
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
    games: int | None,
    winrate: float | None,
    rows: list[dict[str, Any]],
    content_hash: str,
    source_url: str,
    backend: str,
    request_credits: int,
    state: str,
) -> tuple[int, bool]:
    """Store one valid page; an unchanged page only refreshes the target state."""

    target_id = int(target["id"])
    with conn:
        current = conn.execute(
            f"SELECT latest_snapshot_id FROM {TARGETS} WHERE id = ?", (target_id,)
        ).fetchone()
        latest_id = current[0] if current else None
        if (
            latest_id is not None
            and latest_content_hash(conn, latest_id) == content_hash
        ):
            conn.execute(
                f"""
                UPDATE {TARGETS} SET state = ?, last_attempt_at = ?, last_success_at = ?,
                    last_games = ?, success_expected_games = expected_games,
                    consecutive_failures = 0, next_attempt_after = NULL, last_error = NULL
                WHERE id = ?
                """,
                (state, fetched_at, fetched_at, games, target_id),
            )
            return int(latest_id), False
        cursor = conn.execute(
            f"""
            INSERT INTO {SNAPSHOTS} (
                target_id, kind, subject_type, format, archetype, deck_id, rank, period,
                coin, opponent_class, fetched_at, games, winrate, rows_total,
                content_hash, source_url, backend, request_credits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_id,
                target["kind"],
                target["subject_type"],
                target["format"],
                target["archetype"],
                target.get("deck_id"),
                target["rank"],
                target["period"],
                target["coin"],
                target["opponent_class"],
                fetched_at,
                games,
                winrate,
                len(rows),
                content_hash,
                source_url,
                backend,
                int(request_credits or 0),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        if target["kind"] == "cards":
            conn.executemany(
                f"""
                INSERT INTO {CARDS} (
                    snapshot_id, position, card_id, dbf_id, card_name, {", ".join(CARD_FIELDS)}
                ) VALUES (?, ?, ?, ?, ?, {", ".join("?" for _ in CARD_FIELDS)})
                """,
                [
                    (
                        snapshot_id,
                        position,
                        row.get("card_id"),
                        row.get("dbf_id"),
                        row["card_name"],
                        *(row.get(field) for field in CARD_FIELDS),
                    )
                    for position, row in enumerate(rows)
                ],
            )
        else:
            conn.executemany(
                f"""
                INSERT INTO {MATCHUPS} (snapshot_id, opponent_class, winrate, games, share_pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        row["class_key"],
                        row.get("winrate"),
                        int(row.get("games") or 0),
                        row.get("share_pct"),
                    )
                    for row in rows
                ],
            )
        conn.execute(
            f"""
            UPDATE {TARGETS} SET state = ?, last_attempt_at = ?, last_success_at = ?,
                last_changed_at = ?, last_games = ?, success_expected_games = expected_games,
                consecutive_failures = 0, next_attempt_after = NULL, last_error = NULL,
                latest_snapshot_id = ?
            WHERE id = ?
            """,
            (state, fetched_at, fetched_at, fetched_at, games, snapshot_id, target_id),
        )
    return snapshot_id, True


def record_failure(
    conn: sqlite3.Connection,
    target: Mapping[str, Any],
    *,
    attempted_at: str,
    state: str,
    error: str,
    retry_after: str,
) -> None:
    with conn:
        conn.execute(
            f"""
            UPDATE {TARGETS} SET state = ?, last_attempt_at = ?, last_error = ?,
                consecutive_failures = consecutive_failures + 1, next_attempt_after = ?
            WHERE id = ?
            """,
            (state, attempted_at, error[:300], retry_after, int(target["id"])),
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
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection, run_id: int, summary: Mapping[str, Any]
) -> None:
    fields = (
        "finished_at",
        "state",
        "targets_planned",
        "targets_due",
        "attempted",
        "changed",
        "unchanged",
        "no_data",
        "failed",
        "pages_free",
        "pages_scrape_do",
        "scrape_do_credits",
        "stop_reason",
    )
    with conn:
        conn.execute(
            f"UPDATE {RUNS} SET {', '.join(f'{field} = ?' for field in fields)}, "
            "detail_json = ? WHERE id = ?",
            (
                *(summary.get(field) for field in fields),
                _json(summary.get("detail") or {}),
                run_id,
            ),
        )


def prune(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    snapshot_days: int,
    run_days: int = 90,
) -> dict[str, int]:
    """Drop old non-latest snapshots and their rows; latest data never ages out."""

    cutoff = (now - timedelta(days=snapshot_days)).isoformat()
    with conn:
        conn.execute("DROP TABLE IF EXISTS temp.hsguru_expired_snapshots")
        conn.execute(
            f"""
            CREATE TEMP TABLE hsguru_expired_snapshots AS
            SELECT id FROM {SNAPSHOTS}
            WHERE fetched_at < ?
              AND id NOT IN (
                  SELECT latest_snapshot_id FROM {TARGETS}
                  WHERE latest_snapshot_id IS NOT NULL
              )
            """,
            (cutoff,),
        )
        cards = conn.execute(
            f"DELETE FROM {CARDS} WHERE snapshot_id IN "
            "(SELECT id FROM temp.hsguru_expired_snapshots)"
        ).rowcount
        matchups = conn.execute(
            f"DELETE FROM {MATCHUPS} WHERE snapshot_id IN "
            "(SELECT id FROM temp.hsguru_expired_snapshots)"
        ).rowcount
        snapshots = conn.execute(
            f"DELETE FROM {SNAPSHOTS} WHERE id IN "
            "(SELECT id FROM temp.hsguru_expired_snapshots)"
        ).rowcount
        conn.execute("DROP TABLE temp.hsguru_expired_snapshots")
        runs = conn.execute(
            f"DELETE FROM {RUNS} WHERE started_at < ?",
            ((now - timedelta(days=run_days)).isoformat(),),
        ).rowcount
    return {"snapshots": snapshots, "cards": cards, "matchups": matchups, "runs": runs}


# --- reads ---------------------------------------------------------------------


def _subject_clause(
    subject_type: str, archetype: str | None, deck_id: int | None
) -> tuple[str, list[Any]]:
    if subject_type == "deck":
        return "t.subject_type = 'deck' AND t.deck_id = ?", [deck_id]
    return "t.subject_type = 'archetype' AND lower(t.archetype) = lower(?)", [archetype]


def find_target(
    conn: sqlite3.Connection,
    *,
    kind: str,
    subject_type: str,
    format_name: str | None,
    archetype: str | None,
    deck_id: int | None,
    rank: str,
    period: str,
    coin: str,
    opponent_class: str,
) -> dict[str, Any] | None:
    clause, params = _subject_clause(subject_type, archetype, deck_id)
    if format_name:
        clause += " AND t.format = ?"
        params.append(format_name)
    row = conn.execute(
        f"""
        SELECT t.* FROM {TARGETS} AS t
        WHERE {clause} AND t.kind = ? AND t.rank = ? AND t.period = ?
          AND t.coin = ? AND t.opponent_class = ? AND t.latest_snapshot_id IS NOT NULL
        ORDER BY t.active DESC, t.id DESC LIMIT 1
        """,
        (*params, kind, rank, period, coin, opponent_class),
    ).fetchone()
    return dict(row) if row else None


def snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {SNAPSHOTS} WHERE id = ?", (snapshot_id,)
    ).fetchone()
    return dict(row) if row else None


def snapshot_rows(
    conn: sqlite3.Connection, snapshot_row: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if snapshot_row["kind"] == "cards":
        query = f"SELECT * FROM {CARDS} WHERE snapshot_id = ? ORDER BY position"
    else:
        query = (
            f"SELECT * FROM {MATCHUPS} WHERE snapshot_id = ? ORDER BY opponent_class"
        )
    rows = []
    for row in conn.execute(query, (snapshot_row["id"],)):
        item = dict(row)
        item.pop("id", None)
        item.pop("snapshot_id", None)
        rows.append(item)
    return rows


def target_snapshots(
    conn: sqlite3.Connection, target_id: int, *, limit: int
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT id, fetched_at, games, winrate, rows_total, backend FROM {SNAPSHOTS}
            WHERE target_id = ? ORDER BY id DESC LIMIT ?
            """,
            (target_id, limit),
        )
    ]


def card_history(
    conn: sqlite3.Connection, target_id: int, *, card: str, limit: int
) -> dict[int, dict[str, Any]]:
    """The named card's row in each of the target's newest snapshots."""

    digits = card.isdigit()
    rows = conn.execute(
        f"""
        SELECT c.* FROM {CARDS} AS c
        JOIN (
            SELECT id FROM {SNAPSHOTS} WHERE target_id = ? ORDER BY id DESC LIMIT ?
        ) AS s ON s.id = c.snapshot_id
        WHERE {"c.dbf_id = ?" if digits else "(c.card_id = ? OR lower(c.card_name) = lower(?))"}
        """,
        (target_id, limit, int(card)) if digits else (target_id, limit, card, card),
    )
    history: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        history.setdefault(int(item.pop("snapshot_id")), item)
        item.pop("id", None)
    return history


def subject_targets(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    format_name: str | None,
    archetype: str | None,
    deck_id: int | None,
) -> list[dict[str, Any]]:
    clause, params = _subject_clause(subject_type, archetype, deck_id)
    format_clause = " AND t.format = ?" if format_name else ""
    if format_name:
        params.append(format_name)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT t.kind, t.subject_type, t.format, t.archetype, t.deck_id, t.rank,
                   t.period, t.coin, t.opponent_class, t.active, t.state,
                   t.last_success_at, t.last_changed_at, t.last_games,
                   t.latest_snapshot_id
            FROM {TARGETS} AS t
            WHERE {clause}{format_clause} AND t.latest_snapshot_id IS NOT NULL
            ORDER BY t.kind, t.format, t.rank, t.period, t.coin, t.opponent_class
            """,
            params,
        )
    ]


def coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT subject_type, kind, tier, active, state, count(*) AS targets,
                   sum(latest_snapshot_id IS NOT NULL) AS with_data,
                   min(last_success_at) AS oldest_success_at,
                   max(last_success_at) AS newest_success_at
            FROM {TARGETS}
            GROUP BY subject_type, kind, tier, active, state
            ORDER BY subject_type, kind, tier, active DESC, state
            """
        )
    ]


def recent_runs(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = []
    for row in conn.execute(f"SELECT * FROM {RUNS} ORDER BY id DESC LIMIT ?", (limit,)):
        item = dict(row)
        item["detail"] = json.loads(item.pop("detail_json") or "{}")
        rows.append(item)
    return rows


def latest_revision(conn: sqlite3.Connection) -> str | None:
    """Newest snapshot id plus newest check, so unchanged re-reads also bust caches."""

    row = conn.execute(
        f"SELECT (SELECT max(id) FROM {SNAPSHOTS}), (SELECT max(last_success_at) FROM {TARGETS})"
    ).fetchone()
    if not row or (row[0] is None and row[1] is None):
        return None
    return f"{row[0]}:{row[1]}"
