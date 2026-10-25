"""SQLite storage for HSGuru streamers, their decks and who is live now.

* ``hsguru_streamers`` — one row per Twitch channel HSGuru tracks, keyed by the
  Twitch user id HSGuru links (``/streamer-decks?twitch_id=<id>``). The Twitch
  profile (avatar, description, YouTube/X/other links, followers) is filled
  from the public channel page.
* ``hsguru_streamer_decks`` — one row per streamer, HSGuru deck and format with
  the legend ranks, win-loss and the last time the deck was played. A replayed
  deck updates its row in place.
* ``hsguru_streamer_vods`` — recent Twitch past broadcasts, to link a deck to
  the moment of the stream it was played on.
* ``hsguru_streamer_live`` — the last successful ``/streaming-now`` read,
  replaced as a whole on every run.
* ``hsguru_streamer_runs`` — one row per refresh run.

Every table has an integer ``id`` primary key so the platform's analytics delta
(``platform/scripts/stage-analytics.py``) can mirror it into PostgreSQL
``analytics.*``. Column order must match ``platform/sql/018_hsguru_streamers.sql``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

STREAMERS = "hsguru_streamers"
DECKS = "hsguru_streamer_decks"
VODS = "hsguru_streamer_vods"
LIVE = "hsguru_streamer_live"
RUNS = "hsguru_streamer_runs"
TABLES = (STREAMERS, DECKS, VODS, LIVE, RUNS)

SCHEMA = (
    f"""
CREATE TABLE IF NOT EXISTS {STREAMERS} (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    login TEXT,
    twitch_url TEXT,
    youtube_url TEXT,
    x_url TEXT,
    links_json TEXT,
    avatar_url TEXT,
    description TEXT,
    followers INTEGER,
    language TEXT,
    first_seen_at TEXT NOT NULL,
    last_played_at TEXT,
    decks_total INTEGER NOT NULL DEFAULT 0,
    profile_state TEXT NOT NULL DEFAULT 'pending',
    profile_checked_at TEXT,
    profile_error TEXT,
    updated_at TEXT NOT NULL
)
""",
    f"CREATE INDEX IF NOT EXISTS idx_{STREAMERS}_login ON {STREAMERS}(login)",
    f"CREATE INDEX IF NOT EXISTS idx_{STREAMERS}_played ON {STREAMERS}(last_played_at)",
    f"""
CREATE TABLE IF NOT EXISTS {DECKS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer_id INTEGER NOT NULL,
    deck_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    deck_code TEXT NOT NULL,
    archetype TEXT NOT NULL,
    player_class TEXT,
    peak_rank INTEGER,
    latest_rank INTEGER,
    worst_rank INTEGER,
    wins INTEGER,
    losses INTEGER,
    last_played_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (streamer_id, deck_id, format)
)
""",
    f"CREATE INDEX IF NOT EXISTS idx_{DECKS}_streamer ON {DECKS}(streamer_id, last_played_at)",
    f"CREATE INDEX IF NOT EXISTS idx_{DECKS}_played ON {DECKS}(last_played_at)",
    f"""
CREATE TABLE IF NOT EXISTS {VODS} (
    id INTEGER PRIMARY KEY,
    streamer_id INTEGER NOT NULL,
    title TEXT,
    url TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_seconds INTEGER,
    views INTEGER,
    thumbnail_url TEXT,
    seen_at TEXT NOT NULL
)
""",
    f"CREATE INDEX IF NOT EXISTS idx_{VODS}_streamer ON {VODS}(streamer_id, started_at)",
    f"""
CREATE TABLE IF NOT EXISTS {LIVE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT NOT NULL,
    streamer_id INTEGER,
    platform TEXT NOT NULL,
    title TEXT,
    viewers INTEGER,
    mode TEXT,
    language TEXT,
    started_at TEXT,
    thumbnail_url TEXT,
    stream_url TEXT NOT NULL,
    observed_at TEXT NOT NULL
)
""",
    f"""
CREATE TABLE IF NOT EXISTS {RUNS} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL,
    live_streams INTEGER,
    deck_pages INTEGER NOT NULL DEFAULT 0,
    deck_rows INTEGER NOT NULL DEFAULT 0,
    decks_new INTEGER NOT NULL DEFAULT 0,
    decks_updated INTEGER NOT NULL DEFAULT 0,
    decks_cutoff TEXT,
    decks_complete INTEGER NOT NULL DEFAULT 0,
    profiles_attempted INTEGER NOT NULL DEFAULT 0,
    profiles_ok INTEGER NOT NULL DEFAULT 0,
    scrape_do_credits INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    detail_json TEXT
)
""",
)

_DECK_FIELDS = (
    "deck_code",
    "archetype",
    "player_class",
    "peak_rank",
    "latest_rank",
    "worst_rank",
    "wins",
    "losses",
    "last_played_at",
)
_PROFILE_FIELDS = (
    "login",
    "twitch_url",
    "youtube_url",
    "x_url",
    "links_json",
    "avatar_url",
    "description",
    "followers",
)


def ensure_hsguru_streamers_schema(conn: sqlite3.Connection) -> None:
    """Create the additive HSGuru streamer tables on an existing database."""

    for statement in SCHEMA:
        conn.execute(statement)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def upsert_decks(
    conn: sqlite3.Connection, rows: Iterable[Mapping[str, Any]], *, seen_at: str
) -> dict[str, int]:
    """Store parsed ``/streamer-decks`` rows; returns new and changed counts.

    A row only moves forward: an older read of the same deck (pages shift while
    a run walks them) never overwrites a newer ``last_played_at``.
    """

    new = updated = 0
    touched: dict[int, str] = {}
    with conn:
        for row in rows:
            streamer_id = int(row["streamer_id"])
            conn.execute(
                f"""
                INSERT INTO {STREAMERS} (id, display_name, login, twitch_url,
                    first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    login = COALESCE({STREAMERS}.login, excluded.login),
                    twitch_url = COALESCE({STREAMERS}.twitch_url, excluded.twitch_url)
                """,
                (
                    streamer_id,
                    row["streamer_name"],
                    row.get("login"),
                    row.get("twitch_url"),
                    seen_at,
                    seen_at,
                ),
            )
            existing = conn.execute(
                f"SELECT {', '.join(_DECK_FIELDS)} FROM {DECKS} "
                "WHERE streamer_id = ? AND deck_id = ? AND format = ?",
                (streamer_id, row["deck_id"], row["format"]),
            ).fetchone()
            values = tuple(row.get(field) for field in _DECK_FIELDS)
            if existing is None:
                conn.execute(
                    f"""
                    INSERT INTO {DECKS} (streamer_id, deck_id, format,
                        {", ".join(_DECK_FIELDS)}, first_seen_at, updated_at)
                    VALUES (?, ?, ?, {", ".join("?" for _ in _DECK_FIELDS)}, ?, ?)
                    """,
                    (
                        streamer_id,
                        row["deck_id"],
                        row["format"],
                        *values,
                        seen_at,
                        seen_at,
                    ),
                )
                new += 1
            elif (
                tuple(existing) != values
                and row["last_played_at"] >= existing["last_played_at"]
            ):
                conn.execute(
                    f"""
                    UPDATE {DECKS} SET {", ".join(f"{field} = ?" for field in _DECK_FIELDS)},
                        updated_at = ?
                    WHERE streamer_id = ? AND deck_id = ? AND format = ?
                    """,
                    (*values, seen_at, streamer_id, row["deck_id"], row["format"]),
                )
                updated += 1
            touched[streamer_id] = seen_at
        for streamer_id in touched:
            conn.execute(
                f"""
                UPDATE {STREAMERS} SET
                    decks_total = (SELECT COUNT(*) FROM {DECKS} WHERE streamer_id = :id),
                    last_played_at = (SELECT MAX(last_played_at) FROM {DECKS}
                                      WHERE streamer_id = :id),
                    updated_at = :at
                WHERE id = :id
                """,
                {"id": streamer_id, "at": seen_at},
            )
    return {"new": new, "updated": updated}


def replace_live(
    conn: sqlite3.Connection,
    run_id: int,
    streams: Iterable[Mapping[str, Any]],
    *,
    observed_at: str,
) -> int:
    """Replace the live snapshot; link streams to known streamers by name."""

    count = 0
    with conn:
        conn.execute(f"DELETE FROM {LIVE}")
        for stream in streams:
            login = str(stream["login"]).casefold()
            match = conn.execute(
                f"""
                SELECT id FROM {STREAMERS}
                WHERE login = ? OR lower(display_name) = lower(?)
                ORDER BY (login = ?) DESC, last_played_at DESC LIMIT 1
                """,
                (login, stream["display_name"], login),
            ).fetchone()
            streamer_id = int(match[0]) if match else None
            if streamer_id is not None:
                # The live card carries the real Twitch login, which a
                # non-Latin display name on /streamer-decks cannot give.
                conn.execute(
                    f"""
                    UPDATE {STREAMERS} SET login = ?,
                        twitch_url = COALESCE(twitch_url, ?),
                        language = COALESCE(?, language), updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        login,
                        stream["stream_url"],
                        stream.get("language"),
                        observed_at,
                        streamer_id,
                    ),
                )
            conn.execute(
                f"""
                INSERT INTO {LIVE} (run_id, login, display_name, streamer_id, platform,
                    title, viewers, mode, language, started_at, thumbnail_url,
                    stream_url, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    login,
                    stream["display_name"],
                    streamer_id,
                    stream.get("platform") or "twitch",
                    stream.get("title"),
                    stream.get("viewers"),
                    stream.get("mode"),
                    stream.get("language"),
                    stream.get("started_at"),
                    stream.get("thumbnail_url"),
                    stream["stream_url"],
                    observed_at,
                ),
            )
            count += 1
    return count


def profiles_due(
    conn: sqlite3.Connection, *, now: datetime, limit: int
) -> list[dict[str, Any]]:
    """Streamers whose Twitch profile is missing or old, most useful first.

    Never read profiles first; then active streamers whose recent games may be
    in a new past broadcast (6 h); then everybody else weekly. A failed or
    missing profile is read again after two hours. A streamer without a known Latin login cannot be looked up.
    """

    if limit <= 0:
        return []
    six_hours = (now - timedelta(hours=6)).isoformat()
    week = (now - timedelta(days=7)).isoformat()
    retry = (now - timedelta(hours=2)).isoformat()
    rows = conn.execute(
        f"""
        SELECT id, login, display_name, profile_state, profile_checked_at,
               last_played_at
        FROM {STREAMERS}
        WHERE login IS NOT NULL AND (
            profile_checked_at IS NULL
            OR (profile_state = 'ok' AND last_played_at > profile_checked_at
                AND profile_checked_at < :six_hours)
            OR (profile_state = 'ok' AND profile_checked_at < :week)
            OR (profile_state <> 'ok' AND profile_checked_at < :retry)
        )
        ORDER BY profile_checked_at IS NOT NULL, last_played_at DESC
        LIMIT :limit
        """,
        {"six_hours": six_hours, "week": week, "retry": retry, "limit": limit},
    ).fetchall()
    return [dict(row) for row in rows]


def record_profile(
    conn: sqlite3.Connection,
    streamer_id: int,
    profile: Mapping[str, Any] | None,
    *,
    checked_at: str,
    state: str,
    error: str | None = None,
) -> None:
    with conn:
        if profile is None:
            conn.execute(
                f"""
                UPDATE {STREAMERS} SET profile_state = ?, profile_checked_at = ?,
                    profile_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (state, checked_at, error, checked_at, streamer_id),
            )
            return
        values = dict(profile)
        values["links_json"] = _json(values.get("links") or [])
        conn.execute(
            f"""
            UPDATE {STREAMERS} SET
                {", ".join(f"{field} = COALESCE(?, {field})" for field in _PROFILE_FIELDS)},
                profile_state = ?, profile_checked_at = ?, profile_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                *(values.get(field) for field in _PROFILE_FIELDS),
                state,
                checked_at,
                checked_at,
                streamer_id,
            ),
        )
        for vod in profile.get("vods") or ():
            conn.execute(
                f"""
                INSERT INTO {VODS} (id, streamer_id, title, url, started_at,
                    duration_seconds, views, thumbnail_url, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    duration_seconds = excluded.duration_seconds,
                    views = excluded.views,
                    thumbnail_url = excluded.thumbnail_url,
                    seen_at = excluded.seen_at
                """,
                (
                    vod["id"],
                    streamer_id,
                    vod.get("title"),
                    vod["url"],
                    vod["started_at"],
                    vod.get("duration_seconds"),
                    vod.get("views"),
                    vod.get("thumbnail_url"),
                    checked_at,
                ),
            )


def deck_watermark(conn: sqlite3.Connection) -> str | None:
    """Start of the last run that walked ``/streamer-decks`` down to its cutoff."""

    row = conn.execute(
        f"SELECT MAX(started_at) FROM {RUNS} WHERE decks_complete = 1"
    ).fetchone()
    return str(row[0]) if row and row[0] else None


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
        "live_streams",
        "deck_pages",
        "deck_rows",
        "decks_new",
        "decks_updated",
        "decks_cutoff",
        "decks_complete",
        "profiles_attempted",
        "profiles_ok",
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
    conn: sqlite3.Connection, *, now: datetime, vod_days: int = 60, run_days: int = 14
) -> dict[str, int]:
    """Drop old past broadcasts and run rows; decks and streamers never age out."""

    with conn:
        vods = conn.execute(
            f"DELETE FROM {VODS} WHERE started_at < ?",
            ((now - timedelta(days=vod_days)).isoformat(),),
        ).rowcount
        runs = conn.execute(
            f"DELETE FROM {RUNS} WHERE started_at < ? AND state <> 'running'",
            ((now - timedelta(days=run_days)).isoformat(),),
        ).rowcount
    return {"vods": vods, "runs": runs}


def recent_runs(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {RUNS} ORDER BY id DESC LIMIT ?", (limit,)
        )
    ]
