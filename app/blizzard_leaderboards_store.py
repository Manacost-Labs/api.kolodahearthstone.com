"""SQLite storage for the official Blizzard Hearthstone leaderboards.

One *board* is one leaderboard: region (EU, US, AP), mode (standard,
battlegrounds, ...) and season. A board gets at most one *snapshot* per UTC
day: a later read on the same day replaces that day's rows, so the history is
"rank and rating of a player by day". A season that is no longer the latest one
gets one last read flagged ``final`` and is never read again. Nothing deletes
an older day.

A read whose rows equal the board's previous day (Arena and Twist often do not
move for a day) stores no rows of its own: ``entries_id`` names the snapshot
whose ``lb_entries`` it shares, ``rows_hash`` detects the match. Readers always
go through ``entries_id``.

``day`` is the UTC day of the read. ``as_of_day`` is the day the standings
belong to: the read day for a live season, the season's end for a final read
(the backfill reads seasons that ended months ago). ``lb_boards.season_end`` is
the day before the next season's start where Blizzard dates seasons (Standard,
Wild), else the last day the season was read live.

The tables live in their own database file (``blizzard_leaderboards.db``)
instead of ``hs_parses.db``: a live sweep stores about 100,000 rows a day and the
history backfill several million, which the PostgreSQL analytics mirror of
``hs_parses.db`` re-hashes on every sync. REST and GraphQL read this file.

Leaderboards publish a BattleTag without its number, so a name is not unique:
two accounts called "Bob" in one region share one ``lb_players`` row, and both
of their lines stay in the snapshot (the primary key is the rank).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import data_dir

DB_FILENAME = "blizzard_leaderboards.db"
THRESHOLD_RANKS = (1, 100, 1000, 5000)
SCHEMA_VERSION = 2
_SQL_CHUNK = 500
# Above every character a name can hold: the upper bound of a prefix range.
_PREFIX_END = "\U0010ffff"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lb_boards (
    id INTEGER PRIMARY KEY,
    region TEXT NOT NULL,
    mode TEXT NOT NULL,
    season_id INTEGER NOT NULL,
    season_start TEXT,
    is_latest INTEGER NOT NULL DEFAULT 0,
    final INTEGER NOT NULL DEFAULT 0,
    total_size INTEGER,
    total_pages INTEGER,
    latest_snapshot_id INTEGER,
    first_seen_at TEXT NOT NULL,
    last_fetched_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    season_end TEXT,
    UNIQUE (region, mode, season_id)
);
CREATE TABLE IF NOT EXISTS lb_players (
    id INTEGER PRIMARY KEY,
    region TEXT NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    UNIQUE (region, name)
);
CREATE INDEX IF NOT EXISTS idx_lb_players_key ON lb_players(name_key, region);
CREATE TABLE IF NOT EXISTS lb_snapshots (
    id INTEGER PRIMARY KEY,
    board_id INTEGER NOT NULL REFERENCES lb_boards(id),
    day TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    total_size INTEGER NOT NULL,
    total_pages INTEGER NOT NULL,
    stored_rows INTEGER NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    final INTEGER NOT NULL DEFAULT 0,
    rating_at_1 REAL,
    rating_at_100 REAL,
    rating_at_1000 REAL,
    rating_at_5000 REAL,
    entries_id INTEGER,
    rows_hash TEXT,
    as_of_day TEXT,
    UNIQUE (board_id, day)
);
CREATE TABLE IF NOT EXISTS lb_entries (
    snapshot_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    rating REAL,
    PRIMARY KEY (snapshot_id, rank)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_lb_entries_player ON lb_entries(player_id, snapshot_id);
CREATE TABLE IF NOT EXISTS lb_runs (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL DEFAULT 'running',
    summary TEXT
);
"""


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older file to ``SCHEMA_VERSION``; every step only adds data."""

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if version < 1:
            _migrate_shared_entries(conn)
        if version < 2:
            _migrate_as_of_days(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _add_columns(
    conn: sqlite3.Connection, table: str, columns: Sequence[tuple[str, str]]
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, kind in columns:
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _migrate_shared_entries(conn: sqlite3.Connection) -> None:
    """Version 1: shared entries and row hashes; old rows keep their own."""

    _add_columns(
        conn, "lb_snapshots", (("entries_id", "INTEGER"), ("rows_hash", "TEXT"))
    )
    conn.execute("UPDATE lb_snapshots SET entries_id = id WHERE entries_id IS NULL")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lb_snapshots_entries ON lb_snapshots(entries_id)"
    )
    for (snapshot_id,) in conn.execute(
        "SELECT id FROM lb_snapshots WHERE rows_hash IS NULL"
    ).fetchall():
        rows = conn.execute(
            """
            SELECT e.rank, p.name, e.rating FROM lb_entries e
            JOIN lb_players p ON p.id = e.player_id
            WHERE e.snapshot_id = ? ORDER BY e.rank
            """,
            (snapshot_id,),
        ).fetchall()
        conn.execute(
            "UPDATE lb_snapshots SET rows_hash = ? WHERE id = ?",
            (rows_hash([tuple(row) for row in rows]), snapshot_id),
        )


def _migrate_as_of_days(conn: sqlite3.Connection) -> None:
    """Version 2: date final reads by the season's end; ``day`` is kept as read."""

    _add_columns(conn, "lb_snapshots", (("as_of_day", "TEXT"),))
    _add_columns(conn, "lb_boards", (("season_end", "TEXT"),))
    conn.execute(
        "UPDATE lb_snapshots SET as_of_day = day WHERE final = 0 AND as_of_day IS NULL"
    )
    for row in conn.execute(
        "SELECT id, board_id, day FROM lb_snapshots WHERE final = 1"
    ).fetchall():
        season_end, as_of = final_dates(conn, int(row["board_id"]), str(row["day"]))
        conn.execute(
            "UPDATE lb_snapshots SET as_of_day = ? WHERE id = ?", (as_of, row["id"])
        )
        conn.execute(
            "UPDATE lb_boards SET season_end = ? WHERE id = ? AND final = 1",
            (season_end, row["board_id"]),
        )


def final_dates(
    conn: sqlite3.Connection, board_id: int, read_day: str
) -> tuple[str | None, str]:
    """``(season_end, as_of_day)`` of a final read of a board made on ``read_day``.

    The standings belong to the later of the dated season end and the last day
    the season was read live, never after the read itself. A season with
    neither (an undated mode the backfill reads for the first time) keeps the
    read day.
    """

    dated = conn.execute(
        """
        SELECT n.season_start FROM lb_boards b
        JOIN lb_boards n
          ON n.region = b.region AND n.mode = b.mode AND n.season_id > b.season_id
        WHERE b.id = ? AND n.season_start IS NOT NULL
        ORDER BY n.season_id LIMIT 1
        """,
        (board_id,),
    ).fetchone()
    dated_end = (
        (date.fromisoformat(dated[0]) - timedelta(days=1)).isoformat()
        if dated
        else None
    )
    last_live = conn.execute(
        "SELECT MAX(day) FROM lb_snapshots WHERE board_id = ? AND final = 0",
        (board_id,),
    ).fetchone()[0]
    known = [value for value in (dated_end, last_live) if value]
    as_of = min(read_day, max(known)) if known else read_day
    return dated_end or last_live, as_of


def name_key(name: str) -> str:
    return name.casefold()


def rows_hash(rows: Sequence[tuple[int, str, float | None]]) -> str:
    digest = hashlib.sha256()
    for rank, name, rating in rows:
        digest.update(
            f"{rank}\t{name}\t{'' if rating is None else repr(rating)}\n".encode()
        )
    return digest.hexdigest()


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _chunks(values: Sequence[Any], size: int = _SQL_CHUNK) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


# --- boards -----------------------------------------------------------------


def sync_boards(
    conn: sqlite3.Connection,
    seasons: Mapping[tuple[str, str], Sequence[int]],
    season_starts: Mapping[tuple[str, int], str],
    now: str,
) -> None:
    """Insert every advertised board and mark the newest season of each pair.

    Also points snapshots an older writer stored after the migration at their
    own rows, so no day drops out of player history after a deploy.
    """

    with conn:
        conn.execute("UPDATE lb_snapshots SET entries_id = id WHERE entries_id IS NULL")
        conn.execute(
            "UPDATE lb_snapshots SET as_of_day = day "
            "WHERE as_of_day IS NULL AND final = 0"
        )
        for (region, mode), season_ids in seasons.items():
            if not season_ids:
                continue
            latest = max(season_ids)
            conn.executemany(
                """
                INSERT INTO lb_boards (region, mode, season_id, season_start, first_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (region, mode, season_id) DO UPDATE
                SET season_start = COALESCE(excluded.season_start, lb_boards.season_start)
                """,
                [
                    (region, mode, season_id, season_starts.get((mode, season_id)), now)
                    for season_id in season_ids
                ],
            )
            conn.execute(
                """
                UPDATE lb_boards SET is_latest = (season_id = ?)
                WHERE region = ? AND mode = ?
                """,
                (latest, region, mode),
            )


def live_boards(
    conn: sqlite3.Connection,
    regions: Sequence[str],
    modes: Sequence[str],
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Latest seasons; boards still without a snapshot on ``today`` go first.

    A run stopped by its time budget then leaves a different board without
    that day next time, never the same tail of the mode order every run.
    """

    rows = conn.execute(
        "SELECT * FROM lb_boards WHERE is_latest = 1 ORDER BY region, mode"
    ).fetchall()
    boards = _ordered(rows, regions, modes)
    if today:
        boards.sort(key=lambda board: (board["last_fetched_at"] or "")[:10] >= today)
    return boards


def history_boards(
    conn: sqlite3.Connection, regions: Sequence[str], modes: Sequence[str]
) -> list[dict[str, Any]]:
    """Past seasons without their final read: ended live seasons first, newest first."""

    rows = conn.execute(
        "SELECT * FROM lb_boards WHERE is_latest = 0 AND final = 0"
    ).fetchall()
    boards = _ordered(rows, regions, modes)
    mode_order = {mode: index for index, mode in enumerate(modes)}
    region_order = {region: index for index, region in enumerate(regions)}
    boards.sort(
        key=lambda board: (
            board["latest_snapshot_id"] is None,
            board["failures"] >= 3,
            -board["season_id"],
            mode_order[board["mode"]],
            region_order[board["region"]],
        )
    )
    return boards


def _ordered(
    rows: Iterable[sqlite3.Row], regions: Sequence[str], modes: Sequence[str]
) -> list[dict[str, Any]]:
    mode_order = {mode: index for index, mode in enumerate(modes)}
    region_order = {region: index for index, region in enumerate(regions)}
    boards = [
        dict(row)
        for row in rows
        if row["mode"] in mode_order and row["region"] in region_order
    ]
    boards.sort(
        key=lambda board: (mode_order[board["mode"]], region_order[board["region"]])
    )
    return boards


def record_board_failure(
    conn: sqlite3.Connection, board_id: int, error: str, now: str
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE lb_boards
            SET last_error = ?, last_error_at = ?, failures = failures + 1
            WHERE id = ?
            """,
            (error[:500], now, board_id),
        )


# --- snapshots --------------------------------------------------------------


def _player_ids(
    conn: sqlite3.Connection, region: str, names: Sequence[str]
) -> dict[str, int]:
    unique = list(dict.fromkeys(names))
    conn.executemany(
        "INSERT OR IGNORE INTO lb_players (region, name, name_key) VALUES (?, ?, ?)",
        [(region, name, name_key(name)) for name in unique],
    )
    ids: dict[str, int] = {}
    for chunk in _chunks(unique):
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT id, name FROM lb_players WHERE region = ? AND name IN ({placeholders})",
            (region, *chunk),
        ):
            ids[row["name"]] = int(row["id"])
    return ids


def _thresholds(
    rows: Sequence[tuple[int, str, float | None]],
) -> dict[str, float | None]:
    by_rank = {rank: rating for rank, _name, rating in rows}
    return {f"rating_at_{rank}": by_rank.get(rank) for rank in THRESHOLD_RANKS}


def write_snapshot(
    conn: sqlite3.Connection,
    board: Mapping[str, Any],
    *,
    day: str,
    captured_at: str,
    total_size: int,
    total_pages: int,
    rows: Sequence[tuple[int, str, float | None]],
    truncated: bool,
    final: bool,
) -> int:
    """Store one complete read of a board as that day's snapshot, atomically.

    Rows equal to the previous day's share its entries instead of a copy.
    """

    board_id = int(board["id"])
    thresholds = _thresholds(rows)
    digest = rows_hash(rows)
    with conn:
        season_end, as_of = final_dates(conn, board_id, day) if final else (None, day)
        existing = conn.execute(
            "SELECT id, entries_id, rows_hash FROM lb_snapshots "
            "WHERE board_id = ? AND day = ?",
            (board_id, day),
        ).fetchone()
        previous = conn.execute(
            """
            SELECT entries_id, rows_hash FROM lb_snapshots
            WHERE board_id = ? AND day < ? ORDER BY day DESC LIMIT 1
            """,
            (board_id, day),
        ).fetchone()
        values = (
            captured_at,
            total_size,
            total_pages,
            len(rows),
            int(truncated),
            int(final),
            thresholds["rating_at_1"],
            thresholds["rating_at_100"],
            thresholds["rating_at_1000"],
            thresholds["rating_at_5000"],
        )
        keep_entries = bool(existing) and existing["rows_hash"] == digest
        if existing:
            snapshot_id = int(existing["id"])
            if not keep_entries:
                _release_entries(conn, snapshot_id, existing["entries_id"])
            conn.execute(
                """
                UPDATE lb_snapshots
                SET captured_at = ?, total_size = ?, total_pages = ?, stored_rows = ?,
                    truncated = ?, final = ?, rating_at_1 = ?, rating_at_100 = ?,
                    rating_at_1000 = ?, rating_at_5000 = ?
                WHERE id = ?
                """,
                (*values, snapshot_id),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO lb_snapshots (
                    board_id, day, captured_at, total_size, total_pages, stored_rows,
                    truncated, final, rating_at_1, rating_at_100, rating_at_1000,
                    rating_at_5000
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (board_id, day, *values),
            )
            snapshot_id = int(cursor.lastrowid or 0)
        if not keep_entries:
            if previous and previous["rows_hash"] == digest:
                entries_id = int(previous["entries_id"])
            else:
                entries_id = snapshot_id
                ids = _player_ids(
                    conn, str(board["region"]), [name for _rank, name, _r in rows]
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO lb_entries "
                    "(snapshot_id, rank, player_id, rating) VALUES (?, ?, ?, ?)",
                    [
                        (snapshot_id, rank, ids[name], rating)
                        for rank, name, rating in rows
                    ],
                )
            conn.execute(
                "UPDATE lb_snapshots SET entries_id = ?, rows_hash = ? WHERE id = ?",
                (entries_id, digest, snapshot_id),
            )
        conn.execute(
            "UPDATE lb_snapshots SET as_of_day = ? WHERE id = ?", (as_of, snapshot_id)
        )
        conn.execute(
            """
            UPDATE lb_boards
            SET latest_snapshot_id = ?, total_size = ?, total_pages = ?,
                last_fetched_at = ?, final = ?, season_end = ?, last_error = NULL,
                failures = 0
            WHERE id = ?
            """,
            (
                snapshot_id,
                total_size,
                total_pages,
                captured_at,
                int(final),
                season_end,
                board_id,
            ),
        )
    return snapshot_id


def _release_entries(
    conn: sqlite3.Connection, snapshot_id: int, entries_id: int | None
) -> None:
    """Drop the rows a replaced snapshot owns, or hand them to a snapshot sharing them."""

    if entries_id is not None and int(entries_id) != snapshot_id:
        return
    heir = conn.execute(
        "SELECT MIN(id) FROM lb_snapshots WHERE entries_id = ? AND id != ?",
        (snapshot_id, snapshot_id),
    ).fetchone()[0]
    if heir is None:
        conn.execute("DELETE FROM lb_entries WHERE snapshot_id = ?", (snapshot_id,))
        return
    conn.execute(
        "UPDATE lb_entries SET snapshot_id = ? WHERE snapshot_id = ?",
        (heir, snapshot_id),
    )
    conn.execute(
        "UPDATE lb_snapshots SET entries_id = ? WHERE entries_id = ?",
        (heir, snapshot_id),
    )


def entries_of(snapshot: Mapping[str, Any] | sqlite3.Row) -> int:
    """The snapshot id whose ``lb_entries`` rows hold this snapshot's table."""

    return int(snapshot["entries_id"] or snapshot["id"])


# --- runs ---------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, scope: str, started_at: str) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO lb_runs (scope, started_at) VALUES (?, ?)", (scope, started_at)
        )
    return int(cursor.lastrowid or 0)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    state: str,
    finished_at: str,
    summary: Mapping[str, Any],
) -> None:
    with conn:
        conn.execute(
            "UPDATE lb_runs SET state = ?, finished_at = ?, summary = ? WHERE id = ?",
            (state, finished_at, json.dumps(summary, ensure_ascii=False), run_id),
        )


# --- reads ----------------------------------------------------------------------


def latest_revision(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(captured_at) AS captured_at, COUNT(*) AS snapshots FROM lb_snapshots"
    ).fetchone()
    if not row or not row["captured_at"]:
        return None
    return f"{row['captured_at']}:{row['snapshots']}"


def catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT b.region, b.mode, b.season_id, b.season_start, b.season_end,
               b.is_latest, b.final, b.total_size, b.last_fetched_at, s.day,
               s.stored_rows, s.truncated
        FROM lb_boards b
        LEFT JOIN lb_snapshots s ON s.id = b.latest_snapshot_id
        ORDER BY b.mode, b.region, b.season_id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def find_board(
    conn: sqlite3.Connection, region: str, mode: str, season_id: int | None
) -> dict[str, Any] | None:
    """The asked season, or the newest season of the pair that has data."""

    if season_id is not None:
        row = conn.execute(
            "SELECT * FROM lb_boards WHERE region = ? AND mode = ? AND season_id = ?",
            (region, mode, season_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM lb_boards
            WHERE region = ? AND mode = ? AND latest_snapshot_id IS NOT NULL
            ORDER BY season_id DESC LIMIT 1
            """,
            (region, mode),
        ).fetchone()
    return dict(row) if row else None


def find_snapshot(
    conn: sqlite3.Connection, board: Mapping[str, Any], day: str | None
) -> dict[str, Any] | None:
    if day is None:
        snapshot_id = board.get("latest_snapshot_id")
        if snapshot_id is None:
            return None
        row = conn.execute(
            "SELECT * FROM lb_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM lb_snapshots WHERE board_id = ? AND day = ?",
            (board["id"], day),
        ).fetchone()
    return dict(row) if row else None


def snapshot_days(conn: sqlite3.Connection, board_id: int) -> list[str]:
    return [
        str(row["day"])
        for row in conn.execute(
            "SELECT day FROM lb_snapshots WHERE board_id = ? ORDER BY day", (board_id,)
        )
    ]


def table_rows(
    conn: sqlite3.Connection,
    snapshot: Mapping[str, Any],
    *,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of a snapshot, optionally filtered by a name substring."""

    where = "e.snapshot_id = ?"
    params: list[Any] = [entries_of(snapshot)]
    if query:
        where += " AND p.name_key LIKE ? ESCAPE '\\'"
        params.append(_like_pattern(name_key(query)))
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM lb_entries e JOIN lb_players p ON p.id = e.player_id "
                f"WHERE {where}",
                params,
            ).fetchone()[0]
        )
    else:
        total = int(snapshot["stored_rows"])
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT e.rank, p.name, e.rating, e.player_id
            FROM lb_entries e JOIN lb_players p ON p.id = e.player_id
            WHERE {where}
            ORDER BY e.rank LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
    ]
    previous = conn.execute(
        """
        SELECT id, entries_id FROM lb_snapshots
        WHERE board_id = ? AND day < ? ORDER BY day DESC LIMIT 1
        """,
        (snapshot["board_id"], snapshot["day"]),
    ).fetchone()
    previous_ranks: dict[int, int] = {}
    if previous and rows:
        player_ids = [row["player_id"] for row in rows]
        placeholders = ",".join("?" * len(player_ids))
        for row in conn.execute(
            f"""
            SELECT player_id, MIN(rank) AS rank FROM lb_entries
            WHERE snapshot_id = ? AND player_id IN ({placeholders})
            GROUP BY player_id
            """,
            (entries_of(previous), *player_ids),
        ):
            previous_ranks[int(row["player_id"])] = int(row["rank"])
    for row in rows:
        row["previous_rank"] = previous_ranks.get(int(row.pop("player_id")))
    return rows, total


def rating_histogram(
    conn: sqlite3.Connection, snapshot_id: int, bucket: float
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT CAST(rating / ? AS INTEGER) AS bucket, COUNT(*) AS players
        FROM lb_entries WHERE snapshot_id = ? AND rating IS NOT NULL
        GROUP BY bucket ORDER BY bucket
        """,
        (bucket, snapshot_id),
    ).fetchall()
    return [
        {
            "from": round(int(row["bucket"]) * bucket, 4),
            "to": round((int(row["bucket"]) + 1) * bucket, 4),
            "players": int(row["players"]),
        }
        for row in rows
    ]


def rating_summary(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT COUNT(rating) AS rated, MIN(rating) AS min, MAX(rating) AS max,
               AVG(rating) AS mean
        FROM lb_entries WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    if not row or not row["rated"]:
        return None
    rated = int(row["rated"])
    median = conn.execute(
        """
        SELECT rating FROM lb_entries WHERE snapshot_id = ? AND rating IS NOT NULL
        ORDER BY rating LIMIT 1 OFFSET ?
        """,
        (snapshot_id, rated // 2),
    ).fetchone()
    return {
        "rated_players": rated,
        "min": row["min"],
        "max": row["max"],
        "mean": round(float(row["mean"]), 2),
        "median": median["rating"] if median else None,
    }


def snapshot_series(conn: sqlite3.Connection, board_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT day, captured_at, total_size, stored_rows, truncated, final,
                   rating_at_1, rating_at_100, rating_at_1000, rating_at_5000
            FROM lb_snapshots WHERE board_id = ? ORDER BY day
            """,
            (board_id,),
        )
    ]


def search_players(
    conn: sqlite3.Connection, query: str, *, region: str | None, limit: int
) -> list[dict[str, Any]]:
    """Players whose name starts with ``query`` (case-insensitive), exact first."""

    key = name_key(query)
    # A range, not LIKE: LIKE is case-folding and cannot use the BINARY index.
    params: list[Any] = [key, key + _PREFIX_END]
    where = "name_key >= ? AND name_key < ?"
    if region:
        where += " AND region = ?"
        params.append(region)
    players = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT id, region, name FROM lb_players WHERE {where}
            ORDER BY name_key = ? DESC, length(name), name_key, region LIMIT ?
            """,
            (*params, key, limit),
        )
    ]
    if not players:
        return []
    by_id = {player["id"]: {**player, "current": []} for player in players}
    placeholders = ",".join("?" * len(by_id))
    for row in conn.execute(
        f"""
        SELECT e.player_id, b.mode, b.season_id, MIN(e.rank) AS rank, e.rating
        FROM lb_entries e
        JOIN lb_snapshots s ON s.entries_id = e.snapshot_id
        JOIN lb_boards b ON b.latest_snapshot_id = s.id
        WHERE e.player_id IN ({placeholders}) AND b.is_latest = 1
        GROUP BY e.player_id, b.id ORDER BY b.mode
        """,
        tuple(by_id),
    ):
        by_id[row["player_id"]]["current"].append(
            {
                "mode": row["mode"],
                "season_id": row["season_id"],
                "rank": row["rank"],
                "rating": row["rating"],
            }
        )
    out = []
    for player in by_id.values():
        player.pop("id")
        out.append(player)
    return out


def find_player(
    conn: sqlite3.Connection, region: str, name: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, region, name FROM lb_players WHERE region = ? AND name = ?",
        (region, name),
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT id, region, name FROM lb_players
            WHERE region = ? AND name_key = ? ORDER BY name LIMIT 1
            """,
            (region, name_key(name)),
        ).fetchone()
    return dict(row) if row else None


def player_entries(
    conn: sqlite3.Connection, player_id: int, *, mode: str | None
) -> list[dict[str, Any]]:
    where = "e.player_id = ?"
    params: list[Any] = [player_id]
    if mode:
        where += " AND b.mode = ?"
        params.append(mode)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT b.id AS board_id, b.mode, b.season_id, b.season_start,
                   b.season_end, b.is_latest, b.final AS board_final,
                   b.latest_snapshot_id, s.id AS snapshot_id,
                   COALESCE(s.as_of_day, s.day) AS day, s.day AS read_day, s.final,
                   s.total_size, e.rank, e.rating
            FROM lb_entries e
            JOIN lb_snapshots s ON s.entries_id = e.snapshot_id
            JOIN lb_boards b ON b.id = s.board_id
            WHERE {where}
            ORDER BY b.mode, b.season_id DESC, COALESCE(s.as_of_day, s.day), s.final,
                     e.rank
            """,
            params,
        )
    ]


def coverage(conn: sqlite3.Connection, runs: int) -> dict[str, Any]:
    boards = [
        dict(row)
        for row in conn.execute(
            """
            SELECT mode, region,
                   COUNT(*) AS boards,
                   SUM(latest_snapshot_id IS NOT NULL) AS with_data,
                   SUM(final) AS final,
                   SUM(is_latest = 0 AND final = 0) AS history_pending,
                   SUM(failures > 0) AS failing,
                   MAX(CASE WHEN is_latest = 1 THEN season_id END) AS latest_season,
                   MAX(CASE WHEN is_latest = 1 THEN last_fetched_at END) AS latest_fetched_at
            FROM lb_boards GROUP BY mode, region ORDER BY mode, region
            """
        )
    ]
    totals = conn.execute(
        """
        SELECT COUNT(*) AS snapshots, COALESCE(SUM(stored_rows), 0) AS rows,
               COALESCE(SUM(entries_id != id), 0) AS shared
        FROM lb_snapshots
        """
    ).fetchone()
    last_runs = []
    for row in conn.execute("SELECT * FROM lb_runs ORDER BY id DESC LIMIT ?", (runs,)):
        item = dict(row)
        item["summary"] = json.loads(item["summary"]) if item["summary"] else None
        last_runs.append(item)
    return {
        "boards": boards,
        "snapshots": int(totals["snapshots"]),
        "stored_rows": int(totals["rows"]),
        "shared_snapshots": int(totals["shared"]),
        "live_days": live_days(conn),
        "runs": last_runs,
    }


def live_days(
    conn: sqlite3.Connection, today: date | None = None
) -> list[dict[str, Any]]:
    """Daily coverage of every latest season: UTC days without a snapshot.

    Days run from the first snapshot to yesterday (UTC), so trailing missing
    days count too; today is not missing before its runs.
    """

    yesterday = (today or datetime.now(UTC).date()) - timedelta(days=1)
    out = []
    for board in conn.execute(
        "SELECT id, region, mode, season_id FROM lb_boards WHERE is_latest = 1 "
        "ORDER BY mode, region"
    ).fetchall():
        days = [
            date.fromisoformat(day)
            for (day,) in conn.execute(
                "SELECT day FROM lb_snapshots WHERE board_id = ? ORDER BY day",
                (board["id"],),
            )
        ]
        have = set(days)
        missing = (
            [
                (days[0] + timedelta(days=offset)).isoformat()
                for offset in range((max(days[-1], yesterday) - days[0]).days + 1)
                if days[0] + timedelta(days=offset) not in have
            ]
            if days
            else []
        )
        out.append(
            {
                "region": board["region"],
                "mode": board["mode"],
                "season_id": board["season_id"],
                "first_day": days[0].isoformat() if days else None,
                "last_day": days[-1].isoformat() if days else None,
                "days": len(days),
                "missing_days": missing,
            }
        )
    return out
