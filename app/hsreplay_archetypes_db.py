from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .cards_index import _load_raw_cards, card_label, cards_by_dbfid
from .config import data_dir
from .db import get_db_connection, init_db
from .firecrawl_map import load_hsreplay_index
from .hsreplay_client import fetch_hsreplay_json
from .hsreplay_meta_api import CLASS_RU_NAMES, _archetype_name_map
from .refresh_log import log_action
from .resource_locks import ResourceLocked, ResourceLockSet

HSREPLAY_ANALYTICS_BASE = "https://hsreplay.net/analytics/query"
SOURCE = "hsreplay_archetypes"
OTHER_ARCHETYPE_CLASS = {
    -14: "DEMONHUNTER",
    -10: "WARRIOR",
    -9: "WARLOCK",
    -8: "SHAMAN",
    -7: "ROGUE",
    -6: "PRIEST",
    -5: "PALADIN",
    -4: "MAGE",
    -3: "HUNTER",
    -2: "DRUID",
    -1: "DEATHKNIGHT",
}
MULLIGAN_EXCLUDED_DBF_IDS = {
    110440,
    104944,
    104945,
    104946,
    104947,
    104948,
    104949,
    104950,
    104951,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/archetypes/\d+/([^/?#]+)", url)
    return match.group(1) if match else None


def _analytics_url(endpoint: str, params: dict[str, Any]) -> str:
    query = "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in params.items())
    return f"{HSREPLAY_ANALYTICS_BASE}/{endpoint}/?{query}"


def _card_indexes() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    en_by_dbf = cards_by_dbfid()
    ru_by_dbf = {
        int(card["dbfId"]): card
        for card in _load_raw_cards("ruRU")
        if card.get("dbfId") is not None
    }
    return en_by_dbf, ru_by_dbf


def _card_meta(
    dbf_id: int,
    en_by_dbf: dict[int, dict[str, Any]],
    ru_by_dbf: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    en = card_label(en_by_dbf.get(int(dbf_id)))
    ru = ru_by_dbf.get(int(dbf_id)) or {}
    return {
        "dbf_id": int(dbf_id),
        "card_id": en.get("id"),
        "card_name": ru.get("name") or en.get("name") or "Unknown",
        "card_name_en": en.get("name"),
        "cost": en.get("cost"),
        "card_type": en.get("type"),
        "rarity": en.get("rarity"),
        "card_class": en.get("cardClass"),
    }


def _parse_deck_cards(
    raw: str | None,
    en_by_dbf: dict[int, dict[str, Any]],
    ru_by_dbf: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    cards: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            dbf_id = int(item[0])
            count = int(item[1])
        except (TypeError, ValueError):
            continue
        cards.append({**_card_meta(dbf_id, en_by_dbf, ru_by_dbf), "count": count})
    cards.sort(key=lambda c: ((c.get("cost") is None, c.get("cost") or 99), c.get("card_name") or "", c["dbf_id"]))
    return cards


def _archetypes_from_index() -> list[dict[str, Any]]:
    payload = load_hsreplay_index()
    if not payload:
        raise RuntimeError("HSReplay derived index is missing; run scrape-do-map-hsreplay first")
    rows = payload.get("standard_unique_archetypes") or []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("archetype_id") is None:
            continue
        out.append(row)
    out.sort(key=lambda item: int(item["archetype_id"]))
    return out


def _archetype_label(
    archetype_id: int,
    archetype_names: dict[int, dict[str, Any]],
    fallback_class: str | None = None,
) -> dict[str, Any]:
    meta = archetype_names.get(int(archetype_id)) or {}
    class_key = fallback_class
    if archetype_id < 0:
        class_key = OTHER_ARCHETYPE_CLASS.get(archetype_id, fallback_class)
    name = meta.get("name")
    if not name:
        name = (
            f"Другое ({CLASS_RU_NAMES.get(class_key or '', class_key or 'Unknown')})"
            if archetype_id < 0
            else f"Архетип #{archetype_id}"
        )
    url = meta.get("url")
    if url and url.startswith("/"):
        url = "https://hsreplay.net" + url
    return {
        "archetype_id": int(archetype_id),
        "name": name,
        "player_class": class_key or meta.get("player_class_name") or meta.get("player_class"),
        "class_name": CLASS_RU_NAMES.get(class_key or "", class_key),
        "url": url,
        "slug": _slug_from_url(url),
    }


def _begin_run(
    *,
    game_type: str,
    rank_range: str,
    region: str,
    summary_time_range: str,
    deck_time_range: str,
    archetypes_total: int,
) -> int:
    init_db()
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO archetype_refresh_runs (
                    source, game_type, rank_range, region, summary_time_range,
                    deck_time_range, started_at, state, archetypes_total
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SOURCE,
                    game_type,
                    rank_range,
                    region,
                    summary_time_range,
                    deck_time_range,
                    _now(),
                    "running",
                    archetypes_total,
                ),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def _finish_run(
    run_id: int,
    *,
    state: str,
    archetypes_ok: int,
    error: str | None = None,
    write_source_status: bool = True,
) -> None:
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                UPDATE archetype_refresh_runs
                SET completed_at = ?, state = ?, archetypes_ok = ?, error = ?
                WHERE id = ?
                """,
                (_now(), state, archetypes_ok, error, run_id),
            )
    finally:
        conn.close()
    if write_source_status:
        _save_source_status(
            run_id,
            run_state=state,
            archetypes_ok=archetypes_ok,
            error=error,
        )


def _cached_source_dataset() -> dict[str, Any] | None:
    from .storage import load_dataset

    try:
        dataset = load_dataset(SOURCE)
    except Exception as exc:  # cached-read telemetry must not invert a refresh result
        log_action(
            "hsreplay.archetype_db.cached_read.fail",
            source_id=SOURCE,
            level="warn",
            detail=type(exc).__name__,
        )
        return None
    return dataset if isinstance(dataset, dict) and dataset else None


def _save_source_status(run_id: int, *, run_state: str, archetypes_ok: int, error: str | None) -> None:
    """Write a status file for the registered pipeline source (Phase 5).

    Maps the SQLite run-state domain ("ok"/"partial"/"failed") onto
    ``SourceState`` so stale_monitor/refresh_log see this pipeline like any
    other registered source. Modeled on hsreplay_bg_hero_details save_status.
    """
    from .source_state import SourceState
    from .storage import save_status

    state = {
        "ok": SourceState.OK,
        "partial": SourceState.PARTIAL,
    }.get(run_state, SourceState.FETCH_ERROR)
    detail = f"Archetype DB run {run_id}: state={run_state}, archetypes_ok={archetypes_ok}."
    if error:
        detail += f" error={error[:800]}"
    refreshed_at = datetime.now(UTC).isoformat()
    cached_dataset = _cached_source_dataset() if run_state != "ok" else None
    serving_cached = cached_dataset is not None
    published_at = (
        str(cached_dataset.get("fetched_at") or refreshed_at)
        if serving_cached
        else refreshed_at
    )
    status_state = SourceState.OK if serving_cached else state
    status: dict[str, Any] = {
        "source_id": SOURCE,
        "site": "hsreplay",
        "category": "meta",
        "url": "https://hsreplay.net/meta/",
        "state": status_state,
        "fetched_at": published_at,
        "backend": (
            str(cached_dataset.get("backend") or "hsreplay_api")
            if serving_cached
            else "hsreplay_api"
        ),
        "detail": detail,
        "serving_cached_dataset": serving_cached,
        "last_refresh_state": state,
        "last_refresh_at": refreshed_at,
    }
    if error:
        status["last_refresh_error"] = error[:800]
    try:
        save_status(
            SOURCE,
            status,
        )
    except Exception as exc:  # status write must not fail the SQLite run
        log_action(
            "hsreplay.archetype_db.status_write.fail",
            source_id=SOURCE,
            level="warn",
            detail=str(exc)[:500],
        )


def store_archetype_snapshot(
    *,
    run_id: int,
    archetype: dict[str, Any],
    snapshot: dict[str, Any],
) -> int:
    init_db()
    now = _now()
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO hsreplay_archetypes (
                    archetype_id, name, slug, player_class, class_name, url, format,
                    first_seen_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(archetype_id) DO UPDATE SET
                    name = excluded.name,
                    slug = excluded.slug,
                    player_class = excluded.player_class,
                    class_name = excluded.class_name,
                    url = excluded.url,
                    format = excluded.format,
                    updated_at = excluded.updated_at
                """,
                (
                    int(archetype["archetype_id"]),
                    archetype.get("name") or archetype.get("archetype") or f"Архетип #{archetype['archetype_id']}",
                    archetype.get("slug") or _slug_from_url(archetype.get("url")),
                    archetype.get("class") or archetype.get("player_class"),
                    archetype.get("class_name"),
                    archetype.get("url"),
                    "standard",
                    now,
                    now,
                ),
            )
            cur = conn.execute(
                """
                INSERT INTO archetype_snapshots (
                    run_id, archetype_id, source, game_type, rank_range, region,
                    summary_time_range, deck_time_range, mulligan_time_range,
                    fetched_at, as_of_popularity, as_of_matchups, as_of_decks,
                    as_of_mulligan, total_games, win_rate, pct_of_class,
                    pct_of_total, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, archetype_id) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    as_of_popularity = excluded.as_of_popularity,
                    as_of_matchups = excluded.as_of_matchups,
                    as_of_decks = excluded.as_of_decks,
                    as_of_mulligan = excluded.as_of_mulligan,
                    total_games = excluded.total_games,
                    win_rate = excluded.win_rate,
                    pct_of_class = excluded.pct_of_class,
                    pct_of_total = excluded.pct_of_total,
                    raw_json = excluded.raw_json
                """,
                (
                    run_id,
                    int(archetype["archetype_id"]),
                    SOURCE,
                    snapshot["filters"]["game_type"],
                    snapshot["filters"]["rank_range"],
                    snapshot["filters"]["region"],
                    snapshot["filters"]["summary_time_range"],
                    snapshot["filters"]["deck_time_range"],
                    snapshot["filters"]["mulligan_time_range"],
                    snapshot["fetched_at"],
                    snapshot["as_of"].get("popularity"),
                    snapshot["as_of"].get("matchups"),
                    snapshot["as_of"].get("decks"),
                    snapshot["as_of"].get("mulligan"),
                    snapshot["summary"].get("total_games"),
                    snapshot["summary"].get("win_rate"),
                    snapshot["summary"].get("pct_of_class"),
                    snapshot["summary"].get("pct_of_total"),
                    json.dumps(snapshot.get("raw_summary") or {}, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT id FROM archetype_snapshots WHERE run_id = ? AND archetype_id = ?",
                (run_id, int(archetype["archetype_id"])),
            ).fetchone()
            snapshot_id = int(row["id"] if row else cur.lastrowid)

            conn.execute("DELETE FROM archetype_matchups WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute("DELETE FROM archetype_mulligan WHERE snapshot_id = ?", (snapshot_id,))
            deck_ids = [row["id"] for row in conn.execute("SELECT id FROM archetype_decks WHERE snapshot_id = ?", (snapshot_id,))]
            if deck_ids:
                conn.executemany("DELETE FROM archetype_deck_cards WHERE archetype_deck_id = ?", [(deck_id,) for deck_id in deck_ids])
            conn.execute("DELETE FROM archetype_decks WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute("DELETE FROM archetype_time_series WHERE snapshot_id = ?", (snapshot_id,))

            conn.executemany(
                """
                INSERT INTO archetype_matchups (
                    snapshot_id, opponent_archetype_id, opponent_name, opponent_class,
                    total_games, win_rate
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        row["archetype_id"],
                        row.get("name"),
                        row.get("player_class"),
                        row.get("total_games"),
                        row.get("win_rate"),
                    )
                    for row in snapshot.get("matchups", [])
                ],
            )

            conn.executemany(
                """
                INSERT INTO archetype_mulligan (
                    snapshot_id, dbf_id, card_id, card_name, card_name_en, cost,
                    card_type, rarity, card_class, hsreplay_rank, display_row,
                    top_30_row, times_presented_in_initial_cards, times_kept,
                    keep_percentage, times_in_opening_hand, opening_hand_winrate,
                    times_card_drawn, winrate_when_drawn, times_card_played,
                    avg_turn_played_on, avg_turns_in_hand, winrate_when_played
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        row["dbf_id"],
                        row.get("card_id"),
                        row.get("card_name"),
                        row.get("card_name_en"),
                        row.get("cost"),
                        row.get("card_type"),
                        row.get("rarity"),
                        row.get("card_class"),
                        row.get("rank"),
                        1 if row.get("display_row") else 0,
                        1 if row.get("top_30_row") else 0,
                        row.get("times_presented_in_initial_cards"),
                        row.get("times_kept"),
                        row.get("keep_percentage"),
                        row.get("times_in_opening_hand"),
                        row.get("opening_hand_winrate"),
                        row.get("times_card_drawn"),
                        row.get("winrate_when_drawn"),
                        row.get("times_card_played"),
                        row.get("avg_turn_played_on"),
                        row.get("avg_turns_in_hand"),
                        row.get("winrate_when_played"),
                    )
                    for row in snapshot.get("mulligan", [])
                ],
            )

            for deck in snapshot.get("decks", []):
                deck_cur = conn.execute(
                    """
                    INSERT INTO archetype_decks (
                        snapshot_id, deck_id, url, digest, total_games, win_rate,
                        avg_game_length_seconds, avg_num_player_turns, card_count,
                        raw_deck_list, raw_deck_sideboard
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        deck.get("deck_id"),
                        deck.get("url"),
                        deck.get("digest"),
                        deck.get("total_games"),
                        deck.get("win_rate"),
                        deck.get("avg_game_length_seconds"),
                        deck.get("avg_num_player_turns"),
                        deck.get("card_count"),
                        deck.get("raw_deck_list"),
                        deck.get("raw_deck_sideboard"),
                    ),
                )
                archetype_deck_id = int(deck_cur.lastrowid)
                card_rows = []
                for sideboard, cards in ((0, deck.get("cards") or []), (1, deck.get("sideboard") or [])):
                    for card in cards:
                        card_rows.append(
                            (
                                archetype_deck_id,
                                card["dbf_id"],
                                card.get("card_id"),
                                card.get("card_name"),
                                card.get("card_name_en"),
                                card.get("cost"),
                                card.get("card_type"),
                                card.get("rarity"),
                                card.get("card_class"),
                                card.get("count") or 1,
                                sideboard,
                            )
                        )
                conn.executemany(
                    """
                    INSERT INTO archetype_deck_cards (
                        archetype_deck_id, dbf_id, card_id, card_name, card_name_en,
                        cost, card_type, rarity, card_class, count, sideboard
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    card_rows,
                )

            series_rows = []
            for series in snapshot.get("stats_over_time", []):
                name = series.get("name")
                for point in series.get("points") or []:
                    if name and point.get("x") is not None:
                        series_rows.append((snapshot_id, name, str(point.get("x")), _num(point.get("y"))))
            conn.executemany(
                """
                INSERT INTO archetype_time_series (snapshot_id, series_name, point_date, value)
                VALUES (?, ?, ?, ?)
                """,
                series_rows,
            )
            return snapshot_id
    finally:
        conn.close()


async def _fetch_common_payloads(
    *,
    game_type: str,
    rank_range: str,
    region: str,
    summary_time_range: str,
    deck_time_range: str,
) -> dict[str, dict[str, Any]]:
    urls = {
        "popularity": _analytics_url(
            "archetype_popularity_distribution_stats_v2",
            {
                "GameType": game_type,
                "LeagueRankRange": rank_range,
                "Region": region,
                "TimeRange": summary_time_range,
            },
        ),
        "matchups": _analytics_url(
            "head_to_head_archetype_matchups_v2",
            {
                "GameType": game_type,
                "LeagueRankRange": rank_range,
                "TimeRange": summary_time_range,
            },
        ),
        "decks": _analytics_url(
            "list_decks_by_win_rate_v2",
            {
                "GameType": game_type,
                "LeagueRankRange": rank_range,
                "TimeRange": deck_time_range,
            },
        ),
    }
    fetched = await asyncio.gather(
        *[
            fetch_hsreplay_json(url, source_id=f"{SOURCE}_{key}", cache_key=None)
            for key, url in urls.items()
        ]
    )
    return dict(zip(urls.keys(), fetched, strict=True))


async def _fetch_archetype_payloads(
    archetype_id: int,
    *,
    game_type: str,
    rank_range: str,
    mulligan_time_range: str,
) -> dict[str, dict[str, Any]]:
    urls = {
        "mulligan": _analytics_url(
            "single_archetype_mulligan_guide_v2",
            {
                "GameType": game_type,
                "LeagueRankRange": rank_range,
                "TimeRange": mulligan_time_range,
                "archetype_id": archetype_id,
            },
        ),
        "stats_overtime": _analytics_url(
            "single_archetype_stats_over_time_v2",
            {
                "GameType": game_type,
                "LeagueRankRange": rank_range,
                "archetype_id": archetype_id,
            },
        ),
    }
    fetched = await asyncio.gather(
        *[
            fetch_hsreplay_json(url, source_id=f"{SOURCE}_{archetype_id}_{key}", cache_key=None)
            for key, url in urls.items()
        ]
    )
    return dict(zip(urls.keys(), fetched, strict=True))


def _build_snapshot(
    *,
    archetype: dict[str, Any],
    common: dict[str, dict[str, Any]],
    personal: dict[str, dict[str, Any]],
    archetype_names: dict[int, dict[str, Any]],
    en_by_dbf: dict[int, dict[str, Any]],
    ru_by_dbf: dict[int, dict[str, Any]],
    game_type: str,
    rank_range: str,
    region: str,
    summary_time_range: str,
    deck_time_range: str,
    mulligan_time_range: str,
) -> dict[str, Any]:
    archetype_id = int(archetype["archetype_id"])
    class_key = archetype.get("class") or archetype.get("player_class")
    popularity_data = ((common["popularity"].get("series") or {}).get("data") or {})
    pop_rows = popularity_data.get(class_key) or []
    pop = next((row for row in pop_rows if int(row.get("archetype_id") or 0) == archetype_id), {}) or {}

    matchup_map = (((common["matchups"].get("series") or {}).get("data") or {}).get(str(archetype_id)) or {})
    negative_class = {}
    for ck, rows in popularity_data.items():
        for row in rows or []:
            if isinstance(row, dict) and int(row.get("archetype_id") or 0) < 0:
                negative_class[int(row["archetype_id"])] = ck
    matchups = []
    for opponent_id_s, row in matchup_map.items():
        if not isinstance(row, dict):
            continue
        opponent_id = int(opponent_id_s)
        label = _archetype_label(opponent_id, archetype_names, negative_class.get(opponent_id))
        matchups.append(
            {
                **label,
                "total_games": _int(row.get("total_games")),
                "win_rate": _num(row.get("win_rate")),
            }
        )
    matchups.sort(key=lambda item: item.get("total_games") or 0, reverse=True)

    mulligan = []
    raw_mulligan = (((personal["mulligan"].get("series") or {}).get("data") or {}).get("ALL") or [])
    for row in raw_mulligan:
        if not isinstance(row, dict) or row.get("dbf_id") is None:
            continue
        rank = _int(row.get("rank"))
        dbf_id = int(row["dbf_id"])
        display_row = bool(rank and rank <= 40 and dbf_id not in MULLIGAN_EXCLUDED_DBF_IDS)
        mulligan.append(
            {
                **_card_meta(dbf_id, en_by_dbf, ru_by_dbf),
                "rank": rank,
                "display_row": display_row,
                "top_30_row": bool(display_row and rank and rank <= 30),
                "times_presented_in_initial_cards": _int(row.get("times_presented_in_initial_cards")),
                "times_kept": _int(row.get("times_kept")),
                "keep_percentage": _num(row.get("keep_percentage")),
                "times_in_opening_hand": _int(row.get("times_in_opening_hand")),
                "opening_hand_winrate": _num(row.get("opening_hand_winrate")),
                "times_card_drawn": _int(row.get("times_card_drawn")),
                "winrate_when_drawn": _num(row.get("winrate_when_drawn")),
                "times_card_played": _int(row.get("times_card_played")),
                "avg_turn_played_on": _num(row.get("avg_turn_played_on")),
                "avg_turns_in_hand": _num(row.get("avg_turns_in_hand")),
                "winrate_when_played": _num(row.get("winrate_when_played")),
            }
        )
    mulligan.sort(key=lambda item: (item["rank"] if item["rank"] else 9999, -(item.get("times_presented_in_initial_cards") or 0)))

    deck_rows = (((common["decks"].get("series") or {}).get("data") or {}).get(class_key) or [])
    decks = []
    for row in deck_rows:
        if not isinstance(row, dict) or int(row.get("archetype_id") or 0) != archetype_id:
            continue
        cards = _parse_deck_cards(row.get("deck_list"), en_by_dbf, ru_by_dbf)
        sideboard = _parse_deck_cards(row.get("deck_sideboard"), en_by_dbf, ru_by_dbf)
        deck_id = row.get("deck_id")
        decks.append(
            {
                "deck_id": deck_id,
                "url": f"https://hsreplay.net/decks/{deck_id}/#rankRange={rank_range}" if deck_id else None,
                "digest": row.get("digest"),
                "total_games": _int(row.get("total_games")),
                "win_rate": _num(row.get("win_rate")),
                "avg_game_length_seconds": _num(row.get("avg_game_length_seconds")),
                "avg_num_player_turns": _num(row.get("avg_num_player_turns")),
                "card_count": sum(card["count"] for card in cards),
                "cards": cards,
                "sideboard": sideboard,
                "raw_deck_list": row.get("deck_list"),
                "raw_deck_sideboard": row.get("deck_sideboard"),
            }
        )
    decks.sort(key=lambda item: (item.get("total_games") or 0, item.get("win_rate") or 0), reverse=True)

    stats_over_time = []
    for series in personal["stats_overtime"].get("series") or []:
        if isinstance(series, dict):
            stats_over_time.append(
                {
                    "name": series.get("name"),
                    "metadata": series.get("metadata") or {},
                    "points": series.get("data") or [],
                }
            )

    return {
        "fetched_at": _now(),
        "filters": {
            "game_type": game_type,
            "rank_range": rank_range,
            "region": region,
            "summary_time_range": summary_time_range,
            "deck_time_range": deck_time_range,
            "mulligan_time_range": mulligan_time_range,
        },
        "as_of": {
            "popularity": common["popularity"].get("as_of"),
            "matchups": common["matchups"].get("as_of"),
            "decks": common["decks"].get("as_of"),
            "mulligan": personal["mulligan"].get("as_of"),
            "stats_overtime": personal["stats_overtime"].get("as_of"),
        },
        "summary": {
            "total_games": _int(pop.get("total_games")),
            "win_rate": _num(pop.get("win_rate")),
            "pct_of_class": _num(pop.get("pct_of_class")),
            "pct_of_total": _num(pop.get("pct_of_total")),
        },
        "matchups": matchups,
        "mulligan": mulligan,
        "decks": decks,
        "stats_over_time": stats_over_time,
        "raw_summary": {
            "mulligan_rows": len(mulligan),
            "mulligan_display_rows": sum(1 for row in mulligan if row["display_row"]),
            "matchup_rows": len(matchups),
            "deck_rows": len(decks),
        },
    }


async def _refresh_hsreplay_archetype_database_unlocked(
    *,
    rank_range: str = "LEGEND",
    game_type: str = "RANKED_STANDARD",
    region: str = "REGION_EU",
    summary_time_range: str = "LAST_7_DAYS",
    deck_time_range: str = "LAST_30_DAYS",
    mulligan_time_range: str = "LAST_30_DAYS",
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    diagnostic = limit is not None
    archetypes = _archetypes_from_index()
    if diagnostic:
        archetypes = archetypes[:limit]
    run_id = (
        None
        if diagnostic
        else _begin_run(
            game_type=game_type,
            rank_range=rank_range,
            region=region,
            summary_time_range=summary_time_range,
            deck_time_range=deck_time_range,
            archetypes_total=len(archetypes),
        )
    )
    if not archetypes:
        message = "HSReplay archetype index is empty; refusing to publish"
        if run_id is not None:
            _finish_run(
                run_id,
                state="failed",
                archetypes_ok=0,
                error=message,
            )
        return {
            "ok": False,
            "state": "diagnostic_failed" if diagnostic else "failed",
            "run_id": run_id,
            "archetypes_total": 0,
            "archetypes_ok": 0,
            "errors": [{"error": message}],
            "snapshots": [],
            "diagnostic": diagnostic,
            "published": False,
            "serving_cached_dataset": (
                _cached_source_dataset() is not None and not diagnostic
            ),
        }
    ok = 0
    errors: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    try:
        en_by_dbf, ru_by_dbf = _card_indexes()
        common, archetype_names = await asyncio.gather(
            _fetch_common_payloads(
                game_type=game_type,
                rank_range=rank_range,
                region=region,
                summary_time_range=summary_time_range,
                deck_time_range=deck_time_range,
            ),
            _archetype_name_map(f"{SOURCE}_dict"),
        )
        for archetype in archetypes:
            archetype_id = int(archetype["archetype_id"])
            try:
                personal = await _fetch_archetype_payloads(
                    archetype_id,
                    game_type=game_type,
                    rank_range=rank_range,
                    mulligan_time_range=mulligan_time_range,
                )
                snapshot = _build_snapshot(
                    archetype=archetype,
                    common=common,
                    personal=personal,
                    archetype_names=archetype_names,
                    en_by_dbf=en_by_dbf,
                    ru_by_dbf=ru_by_dbf,
                    game_type=game_type,
                    rank_range=rank_range,
                    region=region,
                    summary_time_range=summary_time_range,
                    deck_time_range=deck_time_range,
                    mulligan_time_range=mulligan_time_range,
                )
                snapshot_id = (
                    store_archetype_snapshot(
                        run_id=run_id,
                        archetype=archetype,
                        snapshot=snapshot,
                    )
                    if run_id is not None
                    else None
                )
                ok += 1
                snapshots.append(
                    {
                        "snapshot_id": snapshot_id,
                        "archetype_id": archetype_id,
                        "name": archetype.get("archetype"),
                        **snapshot["summary"],
                        **snapshot["raw_summary"],
                    }
                )
                log_action(
                    "hsreplay.archetype_db.snapshot.ok",
                    source_id=SOURCE,
                    extra={"run_id": run_id, "archetype_id": archetype_id, "snapshot_id": snapshot_id},
                )
            except Exception as exc:
                errors.append({"archetype_id": archetype_id, "error": str(exc)[:800]})
                log_action(
                    "hsreplay.archetype_db.snapshot.fail",
                    source_id=SOURCE,
                    level="warn",
                    detail=str(exc)[:1000],
                    extra={"run_id": run_id, "archetype_id": archetype_id},
                )
        if diagnostic:
            state = "diagnostic" if not errors else "diagnostic_failed"
        else:
            # NOTE: SQLite run-state domain ("ok"/"partial"/"failed"/"running"),
            # not app.source_state.SourceState.
            state = "ok" if not errors else ("partial" if ok else "failed")
            assert run_id is not None
            _finish_run(
                run_id,
                state=state,
                archetypes_ok=ok,
                error=json.dumps(errors, ensure_ascii=False) if errors else None,
                write_source_status=state != "ok",
            )
        serving_cached_dataset = False
        if not diagnostic and state != "ok":
            serving_cached_dataset = _cached_source_dataset() is not None
        return {
            "ok": not errors if diagnostic else state == "ok",
            "state": state,
            "run_id": run_id,
            "archetypes_total": len(archetypes),
            "archetypes_ok": ok,
            "errors": errors,
            "snapshots": snapshots,
            "diagnostic": diagnostic,
            "published": not diagnostic and state == "ok",
            "serving_cached_dataset": serving_cached_dataset,
        }
    except Exception as exc:
        if run_id is not None:
            _finish_run(run_id, state="failed", archetypes_ok=ok, error=str(exc)[:1000])
        raise


async def refresh_hsreplay_archetype_database(
    *,
    rank_range: str = "LEGEND",
    game_type: str = "RANKED_STANDARD",
    region: str = "REGION_EU",
    summary_time_range: str = "LAST_7_DAYS",
    deck_time_range: str = "LAST_30_DAYS",
    mulligan_time_range: str = "LAST_30_DAYS",
    limit: int | None = None,
) -> dict[str, Any]:
    """Refresh and publish one complete snapshot under a process-wide source lock.

    A limited run remains useful for diagnostics, but never writes a refresh run,
    snapshots, status, compatibility export, or canonical production dataset.
    """
    locks = ResourceLockSet(
        ["derived:hsreplay-index", SOURCE],
        metadata={"operation": "refresh_hsreplay_archetype_database"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE,
            **exc.as_outcome(),
        }

    try:
        from .hsreplay_client import reset_hsreplay_refresh_state

        reset_hsreplay_refresh_state()
        result = await _refresh_hsreplay_archetype_database_unlocked(
            rank_range=rank_range,
            game_type=game_type,
            region=region,
            summary_time_range=summary_time_range,
            deck_time_range=deck_time_range,
            mulligan_time_range=mulligan_time_range,
            limit=limit,
        )
        if result.get("published"):
            try:
                result["export_path"] = str(export_latest_archetypes_json())
            except Exception as exc:  # publication failure must preserve the LKG
                run_id = result.get("run_id")
                archetypes_ok = int(result.get("archetypes_ok") or 0)
                error = f"publication failed: {type(exc).__name__}"
                if isinstance(run_id, int):
                    _finish_run(
                        run_id,
                        state="failed",
                        archetypes_ok=archetypes_ok,
                        error=error,
                    )
                result.update(
                    {
                        "ok": False,
                        "state": "failed",
                        "published": False,
                        "serving_cached_dataset": (
                            _cached_source_dataset() is not None
                        ),
                        "errors": [*result.get("errors", []), {"error": error}],
                    }
                )
            else:
                run_id = result.get("run_id")
                if isinstance(run_id, int):
                    _save_source_status(
                        run_id,
                        run_state="ok",
                        archetypes_ok=int(result.get("archetypes_ok") or 0),
                        error=None,
                    )
        return result
    finally:
        locks.release()


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _current_snapshot_ids() -> str:
    return """
        SELECT current_s.id
        FROM archetype_snapshots current_s
        WHERE current_s.run_id = (
            SELECT r.id
            FROM archetype_refresh_runs r
            WHERE r.source = ?
              AND r.state = 'ok'
              AND r.game_type = ?
              AND r.rank_range = ?
            ORDER BY r.completed_at DESC, r.id DESC
            LIMIT 1
        )
    """


def list_archetype_snapshots(
    *,
    rank_range: str = "LEGEND",
    game_type: str = "RANKED_STANDARD",
    class_name: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    init_db()
    conn = get_db_connection()
    try:
        where = ["s.id IN (" + _current_snapshot_ids() + ")"]
        params: list[Any] = [SOURCE, game_type, rank_range]
        if class_name:
            where.append("a.player_class = ?")
            params.append(class_name)
        if q:
            where.append("(a.name LIKE ? OR a.slug LIKE ? OR CAST(a.archetype_id AS TEXT) = ?)")
            params.extend([f"%{q}%", f"%{q}%", q])
        base = f"""
            FROM archetype_snapshots s
            JOIN hsreplay_archetypes a ON a.archetype_id = s.archetype_id
            WHERE {' AND '.join(where)}
        """
        total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
        rows = conn.execute(
            """
            SELECT
                s.id AS snapshot_id, s.run_id, s.archetype_id, a.name, a.slug, a.player_class,
                a.class_name, a.url, s.fetched_at, s.as_of_popularity,
                s.total_games, s.win_rate, s.pct_of_class, s.pct_of_total,
                s.rank_range, s.game_type, s.region
            """
            + base
            + " ORDER BY COALESCE(s.pct_of_total, 0) DESC, COALESCE(s.total_games, 0) DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "archetypes": [_row_dict(row) for row in rows]}
    finally:
        conn.close()


def get_latest_archetype_snapshot(archetype_id: int, *, rank_range: str = "LEGEND", game_type: str = "RANKED_STANDARD") -> dict[str, Any] | None:
    init_db()
    conn = get_db_connection()
    try:
        query = (
            """
            SELECT
                s.*, a.name, a.slug, a.player_class, a.class_name, a.url
            FROM archetype_snapshots s
            JOIN hsreplay_archetypes a ON a.archetype_id = s.archetype_id
            WHERE s.archetype_id = ?
              AND s.id IN (
            """
            + _current_snapshot_ids()
            + ")"
        )
        row = conn.execute(
            query,
            (archetype_id, SOURCE, game_type, rank_range),
        ).fetchone()
        return _row_dict(row) if row else None
    finally:
        conn.close()


def get_archetype_detail(archetype_id: int, *, rank_range: str = "LEGEND", game_type: str = "RANKED_STANDARD") -> dict[str, Any] | None:
    snapshot = get_latest_archetype_snapshot(archetype_id, rank_range=rank_range, game_type=game_type)
    if not snapshot:
        return None
    snapshot_id = snapshot["id"]
    conn = get_db_connection()
    try:
        matchups = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM archetype_matchups WHERE snapshot_id = ? ORDER BY total_games DESC",
                (snapshot_id,),
            ).fetchall()
        ]
        mulligan = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM archetype_mulligan
                WHERE snapshot_id = ? AND display_row = 1
                ORDER BY hsreplay_rank ASC
                """,
                (snapshot_id,),
            ).fetchall()
        ]
        decks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM archetype_decks
                WHERE snapshot_id = ?
                ORDER BY total_games DESC
                LIMIT 50
                """,
                (snapshot_id,),
            ).fetchall()
        ]
        # Return each deck's card list with the detail response.  This keeps
        # the archetype view self-contained while cards remain scoped to its
        # exact snapshot.
        for deck in decks:
            deck["cards"] = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM archetype_deck_cards
                    WHERE archetype_deck_id = ?
                    ORDER BY sideboard ASC, COALESCE(cost, 99), card_name
                    """,
                    (deck["id"],),
                ).fetchall()
            ]
        history = [
            dict(row)
            for row in conn.execute(
                """
                SELECT series_name, point_date, value
                FROM archetype_time_series
                WHERE snapshot_id = ?
                ORDER BY series_name, point_date
                """,
                (snapshot_id,),
            ).fetchall()
        ]
        return {
            "snapshot": snapshot,
            "matchups": matchups,
            "mulligan": mulligan,
            "decks": decks,
            "history": history,
        }
    finally:
        conn.close()


def get_archetype_deck_cards(archetype_deck_id: int) -> list[dict[str, Any]]:
    init_db()
    conn = get_db_connection()
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM archetype_deck_cards
                WHERE archetype_deck_id = ?
                ORDER BY sideboard ASC, COALESCE(cost, 99), card_name
                """,
                (archetype_deck_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def latest_run() -> dict[str, Any] | None:
    init_db()
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM archetype_refresh_runs
            WHERE source = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (SOURCE,),
        ).fetchone()
        return _row_dict(row) if row else None
    finally:
        conn.close()


def export_latest_archetypes_json(path: Path | None = None) -> Path:
    path = path or (Path(data_dir()) / "datasets" / "hsreplay_archetypes_db_latest.json")
    generated_at = _now()
    latest = latest_run()
    page = list_archetype_snapshots(limit=500)
    payload = {
        "type": "hsreplay_archetype_database",
        "generated_at": generated_at,
        "latest_run": latest,
        "archetypes": page["archetypes"],
    }
    structured = {
        "type": payload["type"],
        "latest_run": latest,
        **page,
    }

    # The canonical dataset is the authoritative publication boundary. The
    # legacy ``*_db_latest`` file is compatibility-only and must never turn a
    # successful canonical publish into a false LKG/failure if its secondary
    # write fails.
    from .storage import save_dataset, write_json

    save_dataset(
        SOURCE,
        {
            "source_id": SOURCE,
            "site": "hsreplay",
            "category": "meta",
            "url": "https://hsreplay.net/meta/",
            "final_url": "https://hsreplay.net/meta/",
            "fetched_at": generated_at,
            "backend": "hsreplay_api",
            "data": {"structured": structured},
        },
    )
    try:
        write_json(path, payload)
    except Exception as exc:  # canonical publication already succeeded
        log_action(
            "hsreplay.archetype_db.legacy_export.fail",
            source_id=SOURCE,
            level="warn",
            detail=type(exc).__name__,
        )
    return path
