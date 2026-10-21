"""SQLite storage for HSReplay Battlegrounds minion statistics snapshots.

Every table has a monotonic integer ``id`` primary key so the platform's
incremental analytics delta (``platform/scripts/stage-analytics.py``) can mirror
it into PostgreSQL ``analytics.*``.  Column order must match
``platform/sql/016_bg_minion_stats.sql``.  A snapshot and all of its detail
rows are written in one transaction; readers only ever see complete snapshots.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .bg_minion_stats_formulas import ALL_COMPOSITIONS, PURCHASE_COUNTERS, round_metrics, summarize_variant

SNAPSHOTS = "bg_minion_stat_snapshots"
DETAIL_TABLES = (
    "bg_minion_stat_rounds",
    "bg_minion_stat_positions",
    "bg_minion_stat_purchases",
    "bg_minion_stat_medians",
)
TABLES = (SNAPSHOTS, "bg_minion_stat_summaries", *DETAIL_TABLES)

_PURCHASE_COLUMNS = ",\n    ".join(f"{field} INTEGER NOT NULL" for field in PURCHASE_COUNTERS)

SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {SNAPSHOTS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    mmr_percentile TEXT NOT NULL,
    time_range TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    list_as_of TEXT,
    positions_as_of TEXT,
    purchases_as_of TEXT,
    medians_as_of TEXT,
    minions_total INTEGER NOT NULL,
    has_positions INTEGER NOT NULL,
    pct_players_remaining_json TEXT NOT NULL,
    composition_names_json TEXT
)
""",
    """
CREATE TABLE IF NOT EXISTS bg_minion_stat_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES bg_minion_stat_snapshots(id),
    dbf_id INTEGER NOT NULL,
    variant TEXT NOT NULL CHECK (variant IN ('normal', 'golden')),
    tavern_tier INTEGER,
    composition_ids_json TEXT,
    impact REAL,
    combat_winrate REAL,
    popularity REAL,
    games_with_minion INTEGER NOT NULL,
    games_without_minion INTEGER NOT NULL,
    avg_placement_with REAL,
    avg_placement_without REAL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    UNIQUE (snapshot_id, dbf_id, variant)
)
""",
    """
CREATE TABLE IF NOT EXISTS bg_minion_stat_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES bg_minion_stat_snapshots(id),
    dbf_id INTEGER NOT NULL,
    variant TEXT NOT NULL CHECK (variant IN ('normal', 'golden')),
    combat_round INTEGER NOT NULL,
    distinct_games INTEGER,
    games_with_minion INTEGER NOT NULL,
    games_without_minion INTEGER NOT NULL,
    sum_placement_with INTEGER NOT NULL,
    sum_placement_without INTEGER NOT NULL,
    wins INTEGER,
    losses INTEGER,
    at_least_one REAL,
    impact REAL,
    combat_winrate REAL,
    UNIQUE (snapshot_id, dbf_id, variant, combat_round)
)
""",
    """
CREATE TABLE IF NOT EXISTS bg_minion_stat_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES bg_minion_stat_snapshots(id),
    dbf_id INTEGER NOT NULL,
    composition_id INTEGER NOT NULL,
    board TEXT NOT NULL CHECK (board IN ('normal', 'taunt')),
    occurrences INTEGER NOT NULL,
    sum_placement INTEGER NOT NULL,
    position_occurrences_json TEXT NOT NULL,
    position_sum_placement_json TEXT NOT NULL,
    UNIQUE (snapshot_id, dbf_id, composition_id, board)
)
""",
    f"""
CREATE TABLE IF NOT EXISTS bg_minion_stat_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES bg_minion_stat_snapshots(id),
    dbf_id INTEGER NOT NULL,
    recruitment_round INTEGER NOT NULL,
    {_PURCHASE_COLUMNS},
    UNIQUE (snapshot_id, dbf_id, recruitment_round)
)
""",
    """
CREATE TABLE IF NOT EXISTS bg_minion_stat_medians (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES bg_minion_stat_snapshots(id),
    dbf_id INTEGER NOT NULL,
    variant TEXT NOT NULL CHECK (variant IN ('normal', 'golden')),
    combat_round INTEGER NOT NULL,
    median_attack REAL,
    median_health REAL,
    UNIQUE (snapshot_id, dbf_id, variant, combat_round)
)
""",
    f"CREATE INDEX IF NOT EXISTS idx_bg_minion_stat_snapshots_slice "
    f"ON {SNAPSHOTS}(mmr_percentile, time_range, id)",
    "CREATE INDEX IF NOT EXISTS idx_bg_minion_stat_summaries_dbf "
    "ON bg_minion_stat_summaries(dbf_id, snapshot_id)",
)


def ensure_bg_minion_stats_schema(conn: sqlite3.Connection) -> None:
    """Create the additive minion statistics tables on an existing database."""

    for statement in SCHEMA:
        conn.execute(statement)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _insert(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> int:
    columns = list(row)
    cursor = conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [row[column] for column in columns],
    )
    return int(cursor.lastrowid)


def write_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot: Mapping[str, Any],
    minions: Iterable[Mapping[str, Any]],
    pct_players_remaining: Mapping[int, float],
    summaries: Iterable[Mapping[str, Any]] | None = None,
    positions: Iterable[Mapping[str, Any]],
    purchases: Iterable[Mapping[str, Any]],
    medians: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Insert one complete slice snapshot atomically and return row counts."""

    counts = {table: 0 for table in TABLES}
    with conn:
        snapshot_id = _insert(
            conn,
            SNAPSHOTS,
            {
                **{key: value for key, value in snapshot.items() if key != "composition_names"},
                "pct_players_remaining_json": _json(
                    {str(key): value for key, value in sorted(pct_players_remaining.items())}
                ),
                "composition_names_json": _json(
                    {str(key): value for key, value in (snapshot.get("composition_names") or {}).items()}
                ),
            },
        )
        counts[SNAPSHOTS] = 1
        precomputed = {
            (row["dbf_id"], row["variant"]): {
                key: value for key, value in row.items() if key not in {"dbf_id", "variant"}
            }
            for row in summaries or ()
        }
        for minion in minions:
            for variant, aggregates in minion["rounds"].items():
                summary = precomputed.get((minion["dbf_id"], variant)) or summarize_variant(
                    aggregates, pct_players_remaining
                )
                _insert(
                    conn,
                    "bg_minion_stat_summaries",
                    {
                        "snapshot_id": snapshot_id,
                        "dbf_id": minion["dbf_id"],
                        "variant": variant,
                        "tavern_tier": minion["tavern_tier"],
                        "composition_ids_json": _json(minion["composition_ids"]),
                        **summary,
                    },
                )
                counts["bg_minion_stat_summaries"] += 1
                for aggregate in aggregates:
                    metrics = round_metrics(aggregate)
                    _insert(
                        conn,
                        "bg_minion_stat_rounds",
                        {
                            "snapshot_id": snapshot_id,
                            "dbf_id": minion["dbf_id"],
                            "variant": variant,
                            **aggregate,
                            "impact": metrics["impact"],
                            "combat_winrate": metrics["combat_winrate"],
                        },
                    )
                    counts["bg_minion_stat_rounds"] += 1
        for row in positions:
            _insert(
                conn,
                "bg_minion_stat_positions",
                {
                    "snapshot_id": snapshot_id,
                    "dbf_id": row["dbf_id"],
                    "composition_id": row["composition_id"],
                    "board": row["board"],
                    "occurrences": row["occurrences"],
                    "sum_placement": row["sum_placement"],
                    "position_occurrences_json": _json(row["position_occurrences"]),
                    "position_sum_placement_json": _json(row["position_sum_placement"]),
                },
            )
            counts["bg_minion_stat_positions"] += 1
        for table, rows in (("bg_minion_stat_purchases", purchases), ("bg_minion_stat_medians", medians)):
            for row in rows:
                _insert(conn, table, {"snapshot_id": snapshot_id, **row})
                counts[table] += 1
    counts["snapshot_id"] = snapshot_id
    return counts


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def decode_snapshot(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for field, target in (
        ("pct_players_remaining_json", "pct_players_remaining"),
        ("composition_names_json", "composition_names"),
    ):
        value = out.pop(field, None)
        out[target] = json.loads(value) if isinstance(value, str) and value else value or {}
    return out


def latest_snapshot(conn: sqlite3.Connection, mmr_percentile: str, time_range: str) -> dict[str, Any] | None:
    return decode_snapshot(
        conn.execute(
            f"SELECT * FROM {SNAPSHOTS} WHERE mmr_percentile = ? AND time_range = ? ORDER BY id DESC LIMIT 1",
            (mmr_percentile, time_range),
        ).fetchone()
    )


def snapshot_by_id(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    return decode_snapshot(conn.execute(f"SELECT * FROM {SNAPSHOTS} WHERE id = ?", (snapshot_id,)).fetchone())


def latest_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT s.* FROM {SNAPSHOTS} AS s
        JOIN (SELECT max(id) AS id FROM {SNAPSHOTS} GROUP BY mmr_percentile, time_range) AS latest
          ON latest.id = s.id
        ORDER BY s.mmr_percentile, s.time_range
        """
    ).fetchall()
    return [decode_snapshot(row) for row in rows]


def prune_details(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    retention_days: Mapping[str, int],
) -> dict[str, int]:
    """Delete expired detail rows; the latest snapshot of a slice always stays whole.

    Snapshot and summary rows are kept forever: they are the "how did it change"
    history.  Detail tables age out per ``retention_days``.
    """

    now = now or datetime.now(timezone.utc)
    deleted: dict[str, int] = {}
    with conn:
        keep = [
            row[0]
            for row in conn.execute(f"SELECT max(id) FROM {SNAPSHOTS} GROUP BY mmr_percentile, time_range")
        ]
        placeholders = ", ".join("?" for _ in keep) or "NULL"
        for table in DETAIL_TABLES:
            days = int(retention_days[table])
            cutoff = (now - timedelta(days=days)).isoformat()
            cursor = conn.execute(
                f"""
                DELETE FROM {table}
                WHERE snapshot_id IN (
                    SELECT id FROM {SNAPSHOTS} WHERE fetched_at < ? AND id NOT IN ({placeholders})
                )
                """,
                [cutoff, *keep],
            )
            deleted[table] = int(cursor.rowcount or 0)
    return deleted


# --- read helpers shared by REST ------------------------------------------------


def summaries(conn: sqlite3.Connection, snapshot_id: int, dbf_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    params: list[Any] = [snapshot_id]
    where = ""
    if dbf_ids is not None:
        ids = list(dbf_ids)
        where = f" AND dbf_id IN ({', '.join('?' for _ in ids) or 'NULL'})"
        params.extend(ids)
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM bg_minion_stat_summaries WHERE snapshot_id = ?{where} ORDER BY dbf_id, variant",
            params,
        )
    ]


def minion_detail_rows(
    conn: sqlite3.Connection, snapshot_id: int, dbf_id: int, composition_id: int = ALL_COMPOSITIONS
) -> dict[str, Any]:
    def rows(sql: str, *params: Any) -> list[dict[str, Any]]:
        return [dict(row) for row in conn.execute(sql, params)]

    positions = rows(
        "SELECT * FROM bg_minion_stat_positions WHERE snapshot_id = ? AND dbf_id = ? AND composition_id = ? "
        "ORDER BY board",
        snapshot_id,
        dbf_id,
        composition_id,
    )
    for row in positions:
        row["position_occurrences"] = json.loads(row.pop("position_occurrences_json"))
        row["position_sum_placement"] = json.loads(row.pop("position_sum_placement_json"))
    return {
        "summaries": summaries(conn, snapshot_id, [dbf_id]),
        "rounds": rows(
            "SELECT * FROM bg_minion_stat_rounds WHERE snapshot_id = ? AND dbf_id = ? ORDER BY variant, combat_round",
            snapshot_id,
            dbf_id,
        ),
        "positions": positions,
        "position_compositions": [
            row["composition_id"]
            for row in rows(
                "SELECT DISTINCT composition_id FROM bg_minion_stat_positions "
                "WHERE snapshot_id = ? AND dbf_id = ? ORDER BY composition_id",
                snapshot_id,
                dbf_id,
            )
        ],
        "purchases": rows(
            "SELECT * FROM bg_minion_stat_purchases WHERE snapshot_id = ? AND dbf_id = ? ORDER BY recruitment_round",
            snapshot_id,
            dbf_id,
        ),
        "medians": rows(
            "SELECT * FROM bg_minion_stat_medians WHERE snapshot_id = ? AND dbf_id = ? ORDER BY variant, combat_round",
            snapshot_id,
            dbf_id,
        ),
    }


def summary_history(
    conn: sqlite3.Connection, dbf_id: int, mmr_percentile: str, time_range: str, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT s.id AS snapshot_id, s.fetched_at, s.list_as_of, m.variant, m.tavern_tier,
               m.impact, m.combat_winrate, m.popularity, m.games_with_minion, m.wins, m.losses
        FROM bg_minion_stat_summaries AS m
        JOIN {SNAPSHOTS} AS s ON s.id = m.snapshot_id
        WHERE m.dbf_id = ? AND s.mmr_percentile = ? AND s.time_range = ?
          AND s.id IN (
              SELECT id FROM {SNAPSHOTS} WHERE mmr_percentile = ? AND time_range = ?
              ORDER BY id DESC LIMIT ?
          )
        ORDER BY s.id, m.variant
        """,
        (dbf_id, mmr_percentile, time_range, mmr_percentile, time_range, limit),
    ).fetchall()
    return [dict(row) for row in rows]
