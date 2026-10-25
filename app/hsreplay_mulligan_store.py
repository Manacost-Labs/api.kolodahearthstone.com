"""SQLite storage for the HSReplay deck mulligan guide (mulligan v2).

The *deck pool* is the top of HSReplay's deck list for one format and rank.
One *target* is one ``/api/v1/mulligan/`` request for one deck and slice
(format, rank, player initiative, time range). Targets keep the incremental
state (when the slice was last read, failures, when it is due again). Every
successful read that changed the data writes an immutable *snapshot*: one
*group* per opponent class HSReplay returned (``ALL`` plus the eleven classes
with premium) and the per-card rows of every group, in one transaction.

Every table has a monotonic integer ``id`` primary key so the platform's
incremental analytics delta (``platform/scripts/stage-analytics.py``) can mirror
it into PostgreSQL ``analytics.*``. Column order must match
``platform/sql/019_hsreplay_mulligan.sql``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

DECKS = "hsreplay_mulligan_decks"
TARGETS = "hsreplay_mulligan_targets"
SNAPSHOTS = "hsreplay_mulligan_snapshots"
GROUPS = "hsreplay_mulligan_groups"
CARDS = "hsreplay_mulligan_cards"
RUNS = "hsreplay_mulligan_runs"
TABLES = (DECKS, TARGETS, SNAPSHOTS, GROUPS, CARDS, RUNS)

ALL_OPPONENTS = "ALL"
GROUP_FIELDS = (
    "games",
    "base_winrate",
    "scope",
    "as_of",
    "deck_games",
    "deck_winrate",
    "archetype_games",
    "archetype_winrate",
    "class_games",
    "class_winrate",
)
CARD_FIELDS = (
    "keep_percentage",
    "opening_hand_winrate",
    "winrate_when_drawn",
    "winrate_when_played",
    "avg_turns_in_hand",
    "avg_turn_played_on",
)

SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {DECKS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    format TEXT NOT NULL,
    rank TEXT NOT NULL,
    deck_id TEXT NOT NULL,
    archetype_id INTEGER,
    archetype_name TEXT,
    player_class TEXT,
    total_games INTEGER,
    win_rate REAL,
    deck_list TEXT,
    pick_reason TEXT NOT NULL,
    position INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    listed_at TEXT NOT NULL,
    as_of TEXT,
    UNIQUE (format, rank, deck_id)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {TARGETS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key TEXT NOT NULL UNIQUE,
    deck_id TEXT NOT NULL,
    format TEXT NOT NULL,
    rank TEXT NOT NULL,
    initiative TEXT NOT NULL,
    period TEXT NOT NULL,
    tier INTEGER NOT NULL,
    premium INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    planned_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_changed_at TEXT,
    last_games INTEGER,
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
    deck_id TEXT NOT NULL,
    format TEXT NOT NULL,
    rank TEXT NOT NULL,
    initiative TEXT NOT NULL,
    period TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    games INTEGER,
    base_winrate REAL,
    scope TEXT,
    as_of TEXT,
    groups_total INTEGER NOT NULL,
    rows_total INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    backend TEXT NOT NULL,
    request_credits INTEGER NOT NULL DEFAULT 0
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {GROUPS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES {SNAPSHOTS}(id),
    opponent_class TEXT NOT NULL,
    games INTEGER,
    base_winrate REAL,
    scope TEXT,
    as_of TEXT,
    deck_games INTEGER,
    deck_winrate REAL,
    archetype_games INTEGER,
    archetype_winrate REAL,
    class_games INTEGER,
    class_winrate REAL,
    UNIQUE (snapshot_id, opponent_class)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {CARDS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES {SNAPSHOTS}(id),
    opponent_class TEXT NOT NULL,
    position INTEGER NOT NULL,
    dbf_id INTEGER NOT NULL,
    card_id TEXT,
    card_name TEXT,
    keep_percentage REAL,
    opening_hand_winrate REAL,
    winrate_when_drawn REAL,
    winrate_when_played REAL,
    avg_turns_in_hand REAL,
    avg_turn_played_on REAL,
    UNIQUE (snapshot_id, opponent_class, position)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {RUNS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL,
    premium_state TEXT,
    decks_listed INTEGER NOT NULL DEFAULT 0,
    targets_planned INTEGER NOT NULL DEFAULT 0,
    targets_due INTEGER NOT NULL DEFAULT 0,
    attempted INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    no_data INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    skipped_premium INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    scrape_do_credits INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    detail_json TEXT
)
""",
    f"CREATE INDEX IF NOT EXISTS idx_hsreplay_mulligan_decks_list ON {DECKS}(format, rank, active, position)",
    (
        f"CREATE INDEX IF NOT EXISTS idx_hsreplay_mulligan_targets_deck "
        f"ON {TARGETS}(deck_id, format, rank, initiative, period)"
    ),
    (
        f"CREATE INDEX IF NOT EXISTS idx_hsreplay_mulligan_snapshots_target "
        f"ON {SNAPSHOTS}(target_id, id)"
    ),
    f"CREATE INDEX IF NOT EXISTS idx_hsreplay_mulligan_groups_snapshot ON {GROUPS}(snapshot_id)",
    (
        f"CREATE INDEX IF NOT EXISTS idx_hsreplay_mulligan_cards_snapshot "
        f"ON {CARDS}(snapshot_id, opponent_class)"
    ),
    f"CREATE INDEX IF NOT EXISTS idx_hsreplay_mulligan_runs_started ON {RUNS}(started_at)",
)

_DECK_FIELDS = (
    "archetype_id",
    "archetype_name",
    "player_class",
    "total_games",
    "win_rate",
    "deck_list",
    "pick_reason",
    "position",
    "as_of",
)
_PLAN_FIELDS = ("deck_id", "format", "rank", "initiative", "period", "tier", "premium")


def ensure_hsreplay_mulligan_schema(conn: sqlite3.Connection) -> None:
    """Create the additive HSReplay mulligan tables on an existing database."""

    for statement in SCHEMA:
        conn.execute(statement)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


# --- deck pool -----------------------------------------------------------------


def replace_deck_pool(
    conn: sqlite3.Connection,
    *,
    format_name: str,
    rank: str,
    decks: Iterable[Mapping[str, Any]],
    listed_at: str,
) -> int:
    """Store a fresh deck list of one format and rank; dropped decks go inactive."""

    deck_ids: list[str] = []
    with conn:
        for deck in decks:
            deck_ids.append(str(deck["deck_id"]))
            conn.execute(
                f"""
                INSERT INTO {DECKS} (format, rank, deck_id, {", ".join(_DECK_FIELDS)},
                    active, listed_at)
                VALUES (?, ?, ?, {", ".join("?" for _ in _DECK_FIELDS)}, 1, ?)
                ON CONFLICT(format, rank, deck_id) DO UPDATE SET
                    {", ".join(f"{field} = excluded.{field}" for field in _DECK_FIELDS)},
                    active = 1, listed_at = excluded.listed_at
                """,
                (
                    format_name,
                    rank,
                    deck["deck_id"],
                    *(deck.get(field) for field in _DECK_FIELDS),
                    listed_at,
                ),
            )
        marks = ", ".join("?" for _ in deck_ids) or "''"
        conn.execute(
            f"""
            UPDATE {DECKS} SET active = 0
            WHERE format = ? AND rank = ? AND active = 1 AND deck_id NOT IN ({marks})
            """,
            (format_name, rank, *deck_ids),
        )
    return len(deck_ids)


def deck_pool_listed_at(
    conn: sqlite3.Connection, *, format_name: str, rank: str
) -> str | None:
    row = conn.execute(
        f"SELECT max(listed_at) FROM {DECKS} WHERE format = ? AND rank = ? AND active = 1",
        (format_name, rank),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def active_decks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {DECKS} WHERE active = 1 ORDER BY format, rank, position"
        )
    ]


# --- targets -------------------------------------------------------------------


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
            conn.execute(
                f"""
                INSERT INTO {TARGETS} (target_key, {", ".join(_PLAN_FIELDS)}, active, planned_at)
                VALUES (?, {", ".join("?" for _ in _PLAN_FIELDS)}, 1, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    tier = excluded.tier,
                    premium = excluded.premium,
                    active = 1,
                    planned_at = excluded.planned_at
                """,
                (
                    target["target_key"],
                    *(target.get(field) for field in _PLAN_FIELDS),
                    planned_at,
                ),
            )
        conn.execute("DROP TABLE IF EXISTS temp.hsreplay_mulligan_planned")
        conn.execute(
            "CREATE TEMP TABLE hsreplay_mulligan_planned (target_key TEXT PRIMARY KEY)"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO temp.hsreplay_mulligan_planned VALUES (?)",
            ((key,) for key in keys),
        )
        conn.execute(
            f"""
            UPDATE {TARGETS} SET active = 0
            WHERE active = 1
              AND target_key NOT IN (SELECT target_key FROM temp.hsreplay_mulligan_planned)
            """
        )
        conn.execute("DROP TABLE temp.hsreplay_mulligan_planned")
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
    groups: list[dict[str, Any]],
    content_hash: str,
    source_url: str,
    backend: str,
    request_credits: int,
    state: str,
) -> tuple[int | None, bool]:
    """Store one valid response; an unchanged response only refreshes the target.

    ``groups`` is the parsed response: one entry per opponent class with its
    summary fields and ``cards``. An empty list is HSReplay's 204 (no games in
    this slice): the target is checked, but no snapshot is written.
    """

    target_id = int(target["id"])
    overall = next(
        (group for group in groups if group["opponent_class"] == ALL_OPPONENTS),
        groups[0] if groups else {},
    )
    games = overall.get("games")
    with conn:
        current = conn.execute(
            f"SELECT latest_snapshot_id FROM {TARGETS} WHERE id = ?", (target_id,)
        ).fetchone()
        latest_id = current[0] if current else None
        if not groups or (
            latest_id is not None
            and latest_content_hash(conn, latest_id) == content_hash
        ):
            conn.execute(
                f"""
                UPDATE {TARGETS} SET state = ?, last_attempt_at = ?, last_success_at = ?,
                    last_games = ?, consecutive_failures = 0, next_attempt_after = NULL,
                    last_error = NULL
                WHERE id = ?
                """,
                (state, fetched_at, fetched_at, games, target_id),
            )
            return (int(latest_id) if latest_id is not None else None), False
        cursor = conn.execute(
            f"""
            INSERT INTO {SNAPSHOTS} (
                target_id, deck_id, format, rank, initiative, period, fetched_at, games,
                base_winrate, scope, as_of, groups_total, rows_total, content_hash,
                source_url, backend, request_credits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_id,
                target["deck_id"],
                target["format"],
                target["rank"],
                target["initiative"],
                target["period"],
                fetched_at,
                games,
                overall.get("base_winrate"),
                overall.get("scope"),
                overall.get("as_of"),
                len(groups),
                sum(len(group["cards"]) for group in groups),
                content_hash,
                source_url,
                backend,
                int(request_credits or 0),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        conn.executemany(
            f"""
            INSERT INTO {GROUPS} (snapshot_id, opponent_class, {", ".join(GROUP_FIELDS)})
            VALUES (?, ?, {", ".join("?" for _ in GROUP_FIELDS)})
            """,
            [
                (
                    snapshot_id,
                    group["opponent_class"],
                    *(group.get(field) for field in GROUP_FIELDS),
                )
                for group in groups
            ],
        )
        conn.executemany(
            f"""
            INSERT INTO {CARDS} (
                snapshot_id, opponent_class, position, dbf_id, card_id, card_name,
                {", ".join(CARD_FIELDS)}
            ) VALUES (?, ?, ?, ?, ?, ?, {", ".join("?" for _ in CARD_FIELDS)})
            """,
            [
                (
                    snapshot_id,
                    group["opponent_class"],
                    position,
                    card["dbf_id"],
                    card.get("card_id"),
                    card.get("card_name"),
                    *(card.get(field) for field in CARD_FIELDS),
                )
                for group in groups
                for position, card in enumerate(group["cards"])
            ],
        )
        conn.execute(
            f"""
            UPDATE {TARGETS} SET state = ?, last_attempt_at = ?, last_success_at = ?,
                last_changed_at = ?, last_games = ?, consecutive_failures = 0,
                next_attempt_after = NULL, last_error = NULL, latest_snapshot_id = ?
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
    count_failure: bool = True,
) -> None:
    with conn:
        conn.execute(
            f"""
            UPDATE {TARGETS} SET state = ?, last_attempt_at = ?, last_error = ?,
                consecutive_failures = consecutive_failures + ?, next_attempt_after = ?
            WHERE id = ?
            """,
            (
                state,
                attempted_at,
                error[:300],
                1 if count_failure else 0,
                retry_after,
                int(target["id"]),
            ),
        )


# --- runs ----------------------------------------------------------------------


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


_RUN_FIELDS = (
    "finished_at",
    "state",
    "premium_state",
    "decks_listed",
    "targets_planned",
    "targets_due",
    "attempted",
    "changed",
    "unchanged",
    "no_data",
    "failed",
    "skipped_premium",
    "requests",
    "scrape_do_credits",
    "stop_reason",
)


def finish_run(
    conn: sqlite3.Connection, run_id: int, summary: Mapping[str, Any]
) -> None:
    with conn:
        conn.execute(
            f"UPDATE {RUNS} SET {', '.join(f'{field} = ?' for field in _RUN_FIELDS)}, "
            "detail_json = ? WHERE id = ?",
            (
                *(summary.get(field) for field in _RUN_FIELDS),
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
        conn.execute("DROP TABLE IF EXISTS temp.hsreplay_mulligan_expired")
        conn.execute(
            f"""
            CREATE TEMP TABLE hsreplay_mulligan_expired AS
            SELECT id FROM {SNAPSHOTS}
            WHERE fetched_at < ?
              AND id NOT IN (
                  SELECT latest_snapshot_id FROM {TARGETS}
                  WHERE latest_snapshot_id IS NOT NULL
              )
            """,
            (cutoff,),
        )
        expired = "(SELECT id FROM temp.hsreplay_mulligan_expired)"
        cards = conn.execute(
            f"DELETE FROM {CARDS} WHERE snapshot_id IN {expired}"
        ).rowcount
        groups = conn.execute(
            f"DELETE FROM {GROUPS} WHERE snapshot_id IN {expired}"
        ).rowcount
        snapshots = conn.execute(
            f"DELETE FROM {SNAPSHOTS} WHERE id IN {expired}"
        ).rowcount
        conn.execute("DROP TABLE temp.hsreplay_mulligan_expired")
        runs = conn.execute(
            f"DELETE FROM {RUNS} WHERE started_at < ?",
            ((now - timedelta(days=run_days)).isoformat(),),
        ).rowcount
    return {"snapshots": snapshots, "groups": groups, "cards": cards, "runs": runs}


# --- reads ---------------------------------------------------------------------


def find_target(
    conn: sqlite3.Connection,
    *,
    deck_id: str,
    format_name: str | None,
    rank: str,
    initiative: str,
    period: str,
) -> dict[str, Any] | None:
    """The stored slice; without a format the deck's most recent read wins."""

    format_clause = " AND format = ?" if format_name else ""
    row = conn.execute(
        f"""
        SELECT * FROM {TARGETS}
        WHERE deck_id = ?{format_clause} AND rank = ? AND initiative = ? AND period = ?
        ORDER BY latest_snapshot_id IS NOT NULL DESC, active DESC,
                 last_success_at DESC, id DESC
        LIMIT 1
        """,
        (deck_id, *([format_name] if format_name else []), rank, initiative, period),
    ).fetchone()
    return dict(row) if row else None


def snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {SNAPSHOTS} WHERE id = ?", (snapshot_id,)
    ).fetchone()
    return dict(row) if row else None


def _strip(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item.pop("id", None)
    item.pop("snapshot_id", None)
    return item


def snapshot_groups(conn: sqlite3.Connection, snapshot_id: int) -> list[dict[str, Any]]:
    return [
        _strip(row)
        for row in conn.execute(
            f"SELECT * FROM {GROUPS} WHERE snapshot_id = ? ORDER BY opponent_class",
            (snapshot_id,),
        )
    ]


def snapshot_cards(
    conn: sqlite3.Connection, snapshot_id: int, *, opponent_class: str
) -> list[dict[str, Any]]:
    return [
        _strip(row)
        for row in conn.execute(
            f"""
            SELECT * FROM {CARDS} WHERE snapshot_id = ? AND opponent_class = ?
            ORDER BY position
            """,
            (snapshot_id, opponent_class),
        )
    ]


def deck_info(
    conn: sqlite3.Connection, deck_id: str, format_name: str | None = None
) -> dict[str, Any] | None:
    """The deck's newest pool row (archetype, class, list) in any rank."""

    format_clause = " AND format = ?" if format_name else ""
    row = conn.execute(
        f"""
        SELECT * FROM {DECKS} WHERE deck_id = ?{format_clause}
        ORDER BY active DESC, listed_at DESC, id DESC LIMIT 1
        """,
        (deck_id, *([format_name] if format_name else [])),
    ).fetchone()
    return dict(row) if row else None


def target_snapshots(
    conn: sqlite3.Connection, target_id: int, *, limit: int
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT id, fetched_at, games, base_winrate, scope, as_of, groups_total,
                   rows_total, backend FROM {SNAPSHOTS}
            WHERE target_id = ? ORDER BY id DESC LIMIT ?
            """,
            (target_id, limit),
        )
    ]


def card_history(
    conn: sqlite3.Connection,
    target_id: int,
    *,
    card: str,
    opponent_class: str,
    limit: int,
) -> dict[int, dict[str, Any]]:
    """The named card's row in each of the target's newest snapshots."""

    digits = card.isdigit()
    rows = conn.execute(
        f"""
        SELECT c.* FROM {CARDS} AS c
        JOIN (
            SELECT id FROM {SNAPSHOTS} WHERE target_id = ? ORDER BY id DESC LIMIT ?
        ) AS s ON s.id = c.snapshot_id
        WHERE c.opponent_class = ? AND {
            "c.dbf_id = ?"
            if digits
            else "(c.card_id = ? OR lower(c.card_name) = lower(?))"
        }
        """,
        (target_id, limit, opponent_class, int(card))
        if digits
        else (target_id, limit, opponent_class, card, card),
    )
    history: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        history.setdefault(int(item.pop("snapshot_id")), item)
        item.pop("id", None)
    return history


def deck_targets(
    conn: sqlite3.Connection, *, deck_id: str, format_name: str | None
) -> list[dict[str, Any]]:
    format_clause = " AND t.format = ?" if format_name else ""
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT t.deck_id, t.format, t.rank, t.initiative, t.period, t.premium,
                   t.active, t.state, t.last_success_at, t.last_changed_at, t.last_games,
                   t.latest_snapshot_id, s.scope, s.groups_total
            FROM {TARGETS} AS t
            LEFT JOIN {SNAPSHOTS} AS s ON s.id = t.latest_snapshot_id
            WHERE t.deck_id = ?{format_clause}
            ORDER BY t.format, t.rank, t.initiative, t.period
            """,
            (deck_id, *([format_name] if format_name else [])),
        )
    ]


def deck_catalog(
    conn: sqlite3.Connection,
    *,
    format_name: str | None,
    rank: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Pool decks with how many of their slices hold stored data."""

    clauses = ["d.active = 1"]
    params: list[Any] = []
    if format_name:
        clauses.append("d.format = ?")
        params.append(format_name)
    if rank:
        clauses.append("d.rank = ?")
        params.append(rank)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT d.format, d.rank, d.deck_id, d.archetype_id, d.archetype_name,
                   d.player_class, d.total_games, d.win_rate, d.pick_reason, d.position,
                   d.listed_at,
                   (SELECT count(*) FROM {TARGETS} AS t
                    WHERE t.deck_id = d.deck_id AND t.format = d.format
                      AND t.rank = d.rank AND t.active = 1) AS slices_planned,
                   (SELECT count(*) FROM {TARGETS} AS t
                    WHERE t.deck_id = d.deck_id AND t.format = d.format
                      AND t.rank = d.rank AND t.latest_snapshot_id IS NOT NULL)
                       AS slices_with_data,
                   (SELECT max(t.last_success_at) FROM {TARGETS} AS t
                    WHERE t.deck_id = d.deck_id AND t.format = d.format
                      AND t.rank = d.rank) AS checked_at
            FROM {DECKS} AS d
            WHERE {" AND ".join(clauses)}
            ORDER BY d.format, d.rank, d.position
            LIMIT ?
            """,
            (*params, limit),
        )
    ]


def coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT format, rank, initiative, period, premium, active, state,
                   count(*) AS targets,
                   sum(latest_snapshot_id IS NOT NULL) AS with_data,
                   min(last_success_at) AS oldest_success_at,
                   max(last_success_at) AS newest_success_at
            FROM {TARGETS}
            GROUP BY format, rank, initiative, period, premium, active, state
            ORDER BY format, rank, initiative, period, active DESC, state
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


def latest_finished_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {RUNS} WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["detail"] = json.loads(item.pop("detail_json") or "{}")
    return item


def latest_revision(conn: sqlite3.Connection) -> str | None:
    """Newest snapshot, check and run, so re-reads and auth changes bust caches."""

    row = conn.execute(
        f"""
        SELECT (SELECT max(id) FROM {SNAPSHOTS}), (SELECT max(last_success_at) FROM {TARGETS}),
               (SELECT max(id) FROM {RUNS} WHERE finished_at IS NOT NULL)
        """
    ).fetchone()
    if not row or all(value is None for value in row):
        return None
    return f"{row[0]}:{row[1]}:{row[2]}"
