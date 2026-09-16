from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import data_dir
from .source_state import SourceState

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    db_dir = data_dir()
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "hs_parses.db"


def get_db_connection() -> sqlite3.Connection:
    path = get_db_path()
    conn = sqlite3.connect(str(path), timeout=15.0)
    # Enable WAL mode for high concurrency and fast writes
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_decks_column(conn: sqlite3.Connection, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(decks)")}
    if column not in columns:
        conn.execute(f"ALTER TABLE decks ADD COLUMN {column} {definition}")


def _normalize_class_name(cls: str) -> str:
    return (
        (cls or "Unknown")
        .replace("Demonhunter", "DemonHunter")
        .replace("Deathknight", "DeathKnight")
    )


def _win_rate_from_record(record: str | None) -> float | None:
    if not record:
        return None
    match = re.match(r"(\d+)\s*-\s*(\d+)", str(record).strip())
    if not match:
        return None
    wins, losses = int(match.group(1)), int(match.group(2))
    total = wins + losses
    if total <= 0:
        return None
    return round(wins / total * 100, 2)


def init_db() -> None:
    """Initialize SQLite database tables and indices if they do not exist."""
    conn = get_db_connection()
    try:
        with conn:
            # 1. Fetch Log table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    content_length INTEGER
                );
            """)

            # 2. Decks table (Unique constraint on deck_code only, to guarantee absolutely NO duplicate decks across different sources or updates)
            # URL is optional, so we place the UNIQUE on deck_code (if populated).
            # To handle records with null deck_code but unique URLs, we use a unique index where appropriate,
            # or handle duplicates explicitly during ingestion (checking for existing codes/URLs and doing an upsert).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS decks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    class TEXT NOT NULL,
                    archetype TEXT NOT NULL,
                    deck_code TEXT UNIQUE,
                    win_rate REAL,
                    score TEXT,
                    title TEXT,
                    url TEXT,
                    format TEXT,
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # 3. Card Popularity History table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS card_popularity_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    class TEXT,
                    archetype TEXT,
                    card_name TEXT NOT NULL,
                    popularity REAL NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(source_id, class, archetype, card_name, recorded_at)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_refresh_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    game_type TEXT NOT NULL,
                    rank_range TEXT NOT NULL,
                    region TEXT NOT NULL,
                    summary_time_range TEXT NOT NULL,
                    deck_time_range TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    state TEXT NOT NULL,
                    archetypes_total INTEGER DEFAULT 0,
                    archetypes_ok INTEGER DEFAULT 0,
                    error TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS hsreplay_archetypes (
                    archetype_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT,
                    player_class TEXT,
                    class_name TEXT,
                    url TEXT,
                    format TEXT DEFAULT 'standard',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    archetype_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    game_type TEXT NOT NULL,
                    rank_range TEXT NOT NULL,
                    region TEXT NOT NULL,
                    summary_time_range TEXT NOT NULL,
                    deck_time_range TEXT NOT NULL,
                    mulligan_time_range TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    as_of_popularity TEXT,
                    as_of_matchups TEXT,
                    as_of_decks TEXT,
                    as_of_mulligan TEXT,
                    total_games INTEGER,
                    win_rate REAL,
                    pct_of_class REAL,
                    pct_of_total REAL,
                    raw_json TEXT,
                    UNIQUE(run_id, archetype_id),
                    FOREIGN KEY(run_id) REFERENCES archetype_refresh_runs(id),
                    FOREIGN KEY(archetype_id) REFERENCES hsreplay_archetypes(archetype_id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_matchups (
                    snapshot_id INTEGER NOT NULL,
                    opponent_archetype_id INTEGER NOT NULL,
                    opponent_name TEXT,
                    opponent_class TEXT,
                    total_games INTEGER,
                    win_rate REAL,
                    PRIMARY KEY(snapshot_id, opponent_archetype_id),
                    FOREIGN KEY(snapshot_id) REFERENCES archetype_snapshots(id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_mulligan (
                    snapshot_id INTEGER NOT NULL,
                    dbf_id INTEGER NOT NULL,
                    card_id TEXT,
                    card_name TEXT NOT NULL,
                    card_name_en TEXT,
                    cost INTEGER,
                    card_type TEXT,
                    rarity TEXT,
                    card_class TEXT,
                    hsreplay_rank INTEGER,
                    display_row INTEGER NOT NULL DEFAULT 0,
                    top_30_row INTEGER NOT NULL DEFAULT 0,
                    times_presented_in_initial_cards INTEGER,
                    times_kept INTEGER,
                    keep_percentage REAL,
                    times_in_opening_hand INTEGER,
                    opening_hand_winrate REAL,
                    times_card_drawn INTEGER,
                    winrate_when_drawn REAL,
                    times_card_played INTEGER,
                    avg_turn_played_on REAL,
                    avg_turns_in_hand REAL,
                    winrate_when_played REAL,
                    PRIMARY KEY(snapshot_id, dbf_id),
                    FOREIGN KEY(snapshot_id) REFERENCES archetype_snapshots(id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_decks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    deck_id TEXT NOT NULL,
                    url TEXT,
                    digest TEXT,
                    total_games INTEGER,
                    win_rate REAL,
                    avg_game_length_seconds REAL,
                    avg_num_player_turns REAL,
                    card_count INTEGER,
                    raw_deck_list TEXT,
                    raw_deck_sideboard TEXT,
                    UNIQUE(snapshot_id, deck_id),
                    FOREIGN KEY(snapshot_id) REFERENCES archetype_snapshots(id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_deck_cards (
                    archetype_deck_id INTEGER NOT NULL,
                    dbf_id INTEGER NOT NULL,
                    card_id TEXT,
                    card_name TEXT NOT NULL,
                    card_name_en TEXT,
                    cost INTEGER,
                    card_type TEXT,
                    rarity TEXT,
                    card_class TEXT,
                    count INTEGER NOT NULL,
                    sideboard INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(archetype_deck_id, dbf_id, sideboard),
                    FOREIGN KEY(archetype_deck_id) REFERENCES archetype_decks(id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS archetype_time_series (
                    snapshot_id INTEGER NOT NULL,
                    series_name TEXT NOT NULL,
                    point_date TEXT NOT NULL,
                    value REAL,
                    PRIMARY KEY(snapshot_id, series_name, point_date),
                    FOREIGN KEY(snapshot_id) REFERENCES archetype_snapshots(id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bg_minion_refresh_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    mmr_percentile TEXT NOT NULL,
                    time_range TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    state TEXT NOT NULL,
                    minions_total INTEGER DEFAULT 0,
                    minions_ok INTEGER DEFAULT 0,
                    error TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bg_minions (
                    dbf_id INTEGER PRIMARY KEY,
                    card_id TEXT,
                    name TEXT NOT NULL,
                    name_ru TEXT,
                    tavern_tier INTEGER,
                    card_type TEXT,
                    rarity TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_card_json TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bg_minion_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    dbf_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    mmr_percentile TEXT NOT NULL,
                    time_range TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    tavern_tier INTEGER,
                    impact REAL,
                    combat_winrate REAL,
                    popularity REAL,
                    games_with_minion INTEGER,
                    games_without_minion INTEGER,
                    avg_placement_with REAL,
                    avg_placement_without REAL,
                    raw_json TEXT,
                    UNIQUE(run_id, dbf_id),
                    FOREIGN KEY(run_id) REFERENCES bg_minion_refresh_runs(id),
                    FOREIGN KEY(dbf_id) REFERENCES bg_minions(dbf_id)
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bg_minion_round_stats (
                    snapshot_id INTEGER NOT NULL,
                    combat_round INTEGER NOT NULL,
                    games_with_minion INTEGER,
                    games_without_minion INTEGER,
                    avg_placement_with REAL,
                    avg_placement_without REAL,
                    impact REAL,
                    combat_winrate REAL,
                    wins INTEGER,
                    losses INTEGER,
                    PRIMARY KEY(snapshot_id, combat_round),
                    FOREIGN KEY(snapshot_id) REFERENCES bg_minion_snapshots(id)
                );
            """)

            # Create indexing for super fast search and queries
            _ensure_decks_column(conn, "draft_id", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decks_source_class ON decks(source_id, class);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decks_code ON decks(deck_code);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decks_url ON decks(url);")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_decks_draft_id "
                "ON decks(draft_id) WHERE draft_id IS NOT NULL"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decks_format_updated ON decks(format, updated_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_card_pop_history ON card_popularity_history(source_id, card_name, recorded_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetype_runs_latest ON archetype_refresh_runs(source, state, completed_at);")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archetype_runs_current "
                "ON archetype_refresh_runs(source, state, game_type, rank_range, completed_at, id);"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetypes_class ON hsreplay_archetypes(player_class, updated_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetype_snapshots_latest ON archetype_snapshots(archetype_id, rank_range, game_type, fetched_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetype_snapshots_run ON archetype_snapshots(run_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetype_matchups_wr ON archetype_matchups(snapshot_id, win_rate);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetype_decks_games ON archetype_decks(snapshot_id, total_games);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archetype_mulligan_display ON archetype_mulligan(snapshot_id, display_row, hsreplay_rank);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_minion_runs_latest ON bg_minion_refresh_runs(source, state, completed_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_minion_snapshots_latest ON bg_minion_snapshots(dbf_id, fetched_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_minion_snapshots_tier ON bg_minion_snapshots(tavern_tier, popularity);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_minion_round_stats_snapshot ON bg_minion_round_stats(snapshot_id, combat_round);")
            from .hsguru_deck_radar import ensure_deck_radar_schema

            ensure_deck_radar_schema(conn)
            
            logger.info("SQLite database tables initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite database: {e}")
        raise
    finally:
        conn.close()


def _parse_percent(val: Any) -> float | None:
    if val is None:
        return None
    val_str = str(val).strip()
    match = re.search(r"([\d.]+)", val_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def store_dataset_to_db(source_id: str, payload: dict[str, Any]) -> None:
    """Parse structured data from a dataset payload and store it in SQLite."""
    init_db() # Ensure tables exist

    fetched_at = payload.get("fetched_at") or datetime.now(timezone.utc).isoformat()
    state = payload.get("state") or SourceState.OK
    
    # Safely get structured dict
    data_dict = payload.get("data") or {}
    structured = data_dict.get("structured") or payload.get("structured") or {}
    
    # Calculate content length
    content_length = data_dict.get("content_length") or len(str(payload))

    conn = get_db_connection()
    try:
        with conn:
            # 1. Log the fetch run
            conn.execute("""
                INSERT INTO fetch_log (source_id, fetched_at, state, content_length)
                VALUES (?, ?, ?, ?)
            """, (source_id, fetched_at, state, content_length))

            # 2. Extract and store decks
            # Source: hearthstone_decks (Hearthstone-Decks.net)
            if source_id == "hearthstone_decks":
                decks_list = structured.get("decks") or []
                for d in decks_list:
                    fmt = d.get("format") or "Standard"
                    cls = d.get("class") or ""
                    if not cls:
                        url = d.get("url") or ""
                        for c in ("death-knight", "demon-hunter", "druid", "hunter", "mage", "paladin", "priest", "rogue", "shaman", "warlock", "warrior"):
                            if c in url.lower():
                                cls = c.replace("-", "").title()
                                break
                    if not cls:
                        cls = "Neutral"

                    # Normalize Class capitalization
                    cls = cls.replace("Demonhunter", "DemonHunter").replace("Deathknight", "DeathKnight")

                    arch = d.get("archetype") or "Unknown"
                    deck_code = d.get("deck_code")
                    score = d.get("score")
                    title = d.get("title")
                    url = d.get("url")

                    if not deck_code:
                        continue

                    # Search if deck code is already saved from ANY source
                    row = conn.execute("""
                        SELECT id, source_id, url FROM decks WHERE deck_code = ?
                    """, (deck_code,)).fetchone()

                    if row:
                        # Deck code exists. Update it (keep original source_id, update stats/score/title/format if newer)
                        conn.execute("""
                            UPDATE decks
                            SET class = ?, archetype = ?, score = ?, title = ?, format = ?, url = ?, updated_at = ?
                            WHERE id = ?
                        """, (cls, arch, score, title, fmt, url or row["url"], fetched_at, row["id"]))
                    else:
                        # No duplicate code. Check if URL exists for deck without code (or update it)
                        if url:
                            row_url = conn.execute("SELECT id FROM decks WHERE url = ?", (url,)).fetchone()
                            if row_url:
                                conn.execute("""
                                    UPDATE decks
                                    SET class = ?, archetype = ?, deck_code = ?, score = ?, title = ?, format = ?, updated_at = ?
                                    WHERE id = ?
                                """, (cls, arch, deck_code, score, title, fmt, fetched_at, row_url["id"]))
                                continue
                        
                        conn.execute("""
                            INSERT INTO decks (source_id, class, archetype, deck_code, score, title, url, format, added_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (source_id, cls, arch, deck_code, score, title, url, fmt, fetched_at, fetched_at))

            # Source: metastats_decks (MetaStats.net)
            elif source_id == "metastats_decks":
                decks_list = structured.get("decks") or []
                for d in decks_list:
                    cls = d.get("class") or "Neutral"
                    cls = cls.replace("Demonhunter", "DemonHunter").replace("Deathknight", "DeathKnight")
                    arch = d.get("archetype_name") or "Unknown"
                    deck_code = d.get("deck_code")
                    win_rate = _parse_percent(d.get("win_rate"))
                    title = d.get("title")
                    games = d.get("games")
                    score = f"{games} games" if games else None

                    if not deck_code:
                        continue

                    # Search by unique deck_code
                    row = conn.execute("""
                        SELECT id FROM decks WHERE deck_code = ?
                    """, (deck_code,)).fetchone()

                    if row:
                        conn.execute("""
                            UPDATE decks
                            SET class = ?, archetype = ?, win_rate = ?, score = ?, title = ?, updated_at = ?
                            WHERE id = ?
                        """, (cls, arch, win_rate, score, title, fetched_at, row["id"]))
                    else:
                        conn.execute("""
                            INSERT INTO decks (source_id, class, archetype, deck_code, win_rate, score, title, added_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (source_id, cls, arch, deck_code, win_rate, score, title, fetched_at, fetched_at))

            # Source: hsreplay_arena_winning_decks (accumulating Arena feed, deduped)
            elif source_id == "hsreplay_arena_winning_decks":
                decks_list = structured.get("decks") or []
                for d in decks_list:
                    deck_code = (d.get("final_deckstring") or "").strip()
                    if not deck_code:
                        continue

                    draft_id = d.get("draft_id")
                    draft_id_s = str(draft_id).strip() if draft_id is not None else None
                    cls = _normalize_class_name(d.get("main_class") or d.get("class") or "Unknown")
                    arch = (d.get("legendary_group") or "Arena").strip() or "Arena"
                    player = (d.get("player") or "Unknown").strip() or "Unknown"
                    record = d.get("record")
                    title = f"{player} · {record}" if record else player
                    url = d.get("url")
                    win_rate = _win_rate_from_record(record)
                    score = record

                    row = None
                    if draft_id_s:
                        row = conn.execute(
                            "SELECT id FROM decks WHERE draft_id = ?",
                            (draft_id_s,),
                        ).fetchone()
                    if not row:
                        row = conn.execute(
                            "SELECT id FROM decks WHERE deck_code = ?",
                            (deck_code,),
                        ).fetchone()

                    if row:
                        conn.execute(
                            """
                            UPDATE decks
                            SET source_id = ?, class = ?, archetype = ?,
                                draft_id = COALESCE(?, draft_id),
                                win_rate = ?, score = ?, title = ?,
                                url = COALESCE(?, url), format = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                source_id,
                                cls,
                                arch,
                                draft_id_s,
                                win_rate,
                                score,
                                title,
                                url,
                                "Arena",
                                fetched_at,
                                row["id"],
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO decks (
                                source_id, class, archetype, deck_code, draft_id,
                                win_rate, score, title, url, format, added_at, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                source_id,
                                cls,
                                arch,
                                deck_code,
                                draft_id_s,
                                win_rate,
                                score,
                                title,
                                url,
                                "Arena",
                                fetched_at,
                                fetched_at,
                            ),
                        )

            # Source: vicious_syndicate_radars (Vicious Syndicate Radars)
            elif source_id == "vicious_syndicate_radars":
                radars_list = structured.get("radars") or []
                recorded_date = fetched_at.split("T")[0]

                for r in radars_list:
                    cls = r.get("class") or "Neutral"
                    cls = cls.replace("Demonhunter", "DemonHunter").replace("Deathknight", "DeathKnight")
                    arch = r.get("archetype") or "Overall"
                    deck_code = r.get("deck_code")
                    url = r.get("url")

                    if deck_code:
                        row = conn.execute("""
                            SELECT id FROM decks WHERE deck_code = ?
                        """, (deck_code,)).fetchone()

                        if row:
                            conn.execute("""
                                UPDATE decks
                                SET class = ?, archetype = ?, url = ?, updated_at = ?
                                WHERE id = ?
                            """, (cls, arch, url, fetched_at, row["id"]))
                        else:
                            conn.execute("""
                                INSERT INTO decks (source_id, class, archetype, deck_code, url, added_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (source_id, cls, arch, deck_code, url, fetched_at, fetched_at))

                    # Save card popularity nodes to trend history
                    nodes_list = r.get("nodes") or []
                    for n in nodes_list:
                        card_name = n.get("name")
                        popularity = n.get("radius") or 0.0

                        if card_name:
                            conn.execute("""
                                INSERT INTO card_popularity_history (source_id, class, archetype, card_name, popularity, recorded_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ON CONFLICT(source_id, class, archetype, card_name, recorded_at) DO UPDATE SET
                                    popularity = excluded.popularity
                            """, (source_id, cls, arch, card_name, popularity, recorded_date))

            # Source: any card stats (HSReplay, Arena, etc.)
            elif source_id.startswith("hsreplay_cards_") or source_id == "hsreplay_arena_cards_advanced":
                cards_list = structured.get("cards") or []
                recorded_date = fetched_at.split("T")[0]

                for c in cards_list:
                    card_name = c.get("name")
                    pop = _parse_percent(c.get("deck_popularity") or c.get("avg_copies") or c.get("popularity"))
                    if pop is None:
                        pop = 0.0

                    cls = c.get("cardClass") or "Neutral"
                    cls = cls.replace("Demonhunter", "DemonHunter").replace("Deathknight", "DeathKnight")

                    if card_name:
                        conn.execute("""
                            INSERT INTO card_popularity_history (source_id, class, archetype, card_name, popularity, recorded_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(source_id, class, archetype, card_name, recorded_at) DO UPDATE SET
                                popularity = excluded.popularity
                            """, (source_id, cls, "Overall", card_name, pop, recorded_date))

        logger.info(f"Successfully saved structured dataset for {source_id} to SQLite DB.")
    except Exception as e:
        logger.error(f"Error storing dataset {source_id} in SQLite database: {e}", exc_info=True)
    finally:
        conn.close()
