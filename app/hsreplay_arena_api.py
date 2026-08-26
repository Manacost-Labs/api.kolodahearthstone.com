from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from hearthstone.enums import CardClass

from .cards_index import card_from_id
from .completeness import (
    COMPLETENESS_SCHEMA_VERSION,
    build_hsreplay_arena_upstream_freshness,
    row_retrieval_evidence,
)
from .deck_decode import decode_deck_code
from .hsreplay_client import fetch_hsreplay_json, get_hsreplay_json_target_headers
from .storage import load_dataset

logger = logging.getLogger(__name__)

# Max decks kept in JSON cache + SQLite feed (deduped by draft_id / deckstring).
ARENA_WINNING_DECKS_FEED_CAP = 500

WINNING_DECKS_URL = "https://hsreplay.net/arena/winning_decks/#playerClass=ALL"
WINNING_DECKS_API_URL = "https://hsreplay.net/api/v1/arena/winning_decks/"

ARENA_PAGE_URL = "https://hsreplay.net/arena/"
CLASSES_STATS_API_URL = "https://hsreplay.net/api/v1/arena/classes_stats/"

ARENA_CARDS_PAGE_URL = "https://hsreplay.net/arena/cards/#view=advanced"
# HSReplay exposes card tiers via card_stats (cards/ often 404 behind CF).
# The request still accepts LAST_4_DAYS after patch 36.4, while the response
# identifies the effective slice as CURRENT_META_PERIOD_UNDERGROUND.  Keep the
# transport parameter separate from the stricter response freshness contract.
ARENA_CARD_STATS_API_URL = (
    "https://hsreplay.net/api/v1/arena/card_stats/"
    "?ArenaTimestampRangeFilter=LAST_4_DAYS&format=json"
)

REGION_NAMES = {
    1: "US",
    2: "EU",
    3: "KR",
    4: "TW",
    5: "CN",
}


def winrate_to_tier(win_rate: float | None) -> str | None:
    if win_rate is None:
        return None
    if win_rate >= 58:
        return "S"
    if win_rate >= 55:
        return "A"
    if win_rate >= 52:
        return "B"
    if win_rate >= 49:
        return "C"
    if win_rate >= 46:
        return "D"
    if win_rate >= 43:
        return "E"
    return "F"


def _arena_percent_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.endswith("%"):
            value = value[:-1].strip()
        value = value.replace(",", ".")
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 100.0:
        return None
    return number


def _pct(value: Any) -> str | None:
    number = _arena_percent_number(value)
    return f"{number:.2f}%" if number is not None else None


def _strict_optional_percent(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    number = _arena_percent_number(value)
    if number is None:
        raise ValueError(f"{field} must be a finite percentage in 0..100")
    return number


def _strict_optional_non_negative_number(
    value: Any,
    *,
    field: str,
) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return value


def _win_rate_sort_key(row: dict[str, Any]) -> float:
    try:
        return float(row.get("win_rate") or 0)
    except (TypeError, ValueError):
        return 0.0


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _required_non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _arena_rate_availability(*, sample_size: int) -> dict[str, object]:
    if sample_size > 0:
        return {"available": True, "reason": None}
    return {"available": False, "reason": "no_games_in_window"}


def _class_name(class_id: int | str | None) -> str | None:
    if class_id is None:
        return None
    try:
        return CardClass(int(class_id)).name.replace("_", " ").title()
    except (TypeError, ValueError, KeyError):
        return None


def _region_name(region: Any) -> str | None:
    if region is None:
        return None
    try:
        return REGION_NAMES.get(int(region))
    except (TypeError, ValueError):
        return None


def _cards_from_ids(card_ids: list[str] | None, *, locale: str = "ruRU") -> list[dict[str, Any]]:
    if not card_ids:
        return []
    return _group_cards([_card_ref(card_id, locale=locale) for card_id in card_ids if card_id])


def _card_ref(card_id: str, *, locale: str = "ruRU") -> dict[str, Any]:
    meta = card_from_id(card_id, locale=locale)
    return {"count": 1, "card_id": card_id, **meta}


def _group_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        key = str(card.get("id") or card.get("card_id") or card.get("dbfId") or "")
        if not key:
            continue
        if key in grouped:
            grouped[key]["count"] = int(grouped[key].get("count") or 1) + 1
        else:
            grouped[key] = dict(card)
            grouped[key]["count"] = 1
    return list(grouped.values())


def _legendary_group_name(package_key_card_id: str | None, locale: str) -> str | None:
    if not package_key_card_id:
        return None
    meta = card_from_id(package_key_card_id, locale=locale)
    return meta.get("name") or package_key_card_id


def normalize_winning_deck(row: dict[str, Any], *, locale: str = "ruRU") -> dict[str, Any] | None:
    deckstring = (row.get("final_deckstring") or "").strip()
    if not deckstring:
        return None

    decoded = decode_deck_code(deckstring)
    final_deck = decoded.get("cards") or [] if decoded.get("ok") else []

    added = _cards_from_ids(row.get("cards_added"), locale=locale)
    discarded = _cards_from_ids(row.get("cards_removed"), locale=locale)
    package_key = row.get("package_key_card_id")

    wins = row.get("final_wins")
    losses = row.get("final_losses")
    record = f"{wins} - {losses}" if wins is not None and losses is not None else None
    draft_id = row.get("draft_id")
    url = f"https://hsreplay.net/arena/run/{draft_id}" if draft_id else None

    return {
        "draft_id": draft_id,
        "player": row.get("battletag"),
        "region": _region_name(row.get("region")),
        "record": record,
        "class": _class_name(row.get("primary_deck_class")),
        "main_class": _class_name(row.get("primary_deck_class")),
        "hero_power_class": _class_name(row.get("secondary_deck_class")),
        "played_at": row.get("latest_match_end") or row.get("latest_match_start"),
        "url": url,
        "final_deckstring": deckstring,
        "final_deck": final_deck,
        "discarded": discarded,
        "added": added,
        "redraft": {"discarded": discarded, "added": added},
        "legendary_group": _legendary_group_name(package_key, locale),
        "package_key_card_id": package_key,
        "package_cards": _cards_from_ids(row.get("package_card_ids"), locale=locale),
    }


def normalize_class_row(row: dict[str, Any]) -> dict[str, Any]:
    deck_class = row.get("deck_class")
    win_rate = row.get("win_rate")
    return {
        "class": _class_name(deck_class),
        "deck_class": deck_class,
        "winrate": _pct(win_rate),
        "win_rate": win_rate,
        "num_drafts": row.get("num_drafts"),
        "pick_rate": row.get("pick_rate"),
        "pct_7_plus": row.get("pct_7_plus"),
    }


def normalize_arena_card_row(row: dict[str, Any], *, locale: str = "ruRU") -> dict[str, Any] | None:
    card_id = (row.get("card_id") or row.get("id") or "").strip()
    if not card_id:
        dbf = row.get("dbf_id") or row.get("dbfId")
        if dbf is not None:
            from .cards_index import cards_by_dbfid

            try:
                card = cards_by_dbfid().get(int(dbf))
            except (TypeError, ValueError):
                card = None
            card_id = (card or {}).get("id") or ""
    if not card_id:
        return None

    meta = card_from_id(card_id, locale=locale)
    win_rate = _strict_optional_percent(row.get("win_rate"), field="win_rate")
    sample_size = _required_non_negative_int(row.get("num_games"), field="num_games")
    deck_winrate = _pct(win_rate)
    winrate_when_drawn = _pct(
        _strict_optional_percent(
            _first_present(
                row,
                "winrate_when_drawn",
                "winrateWhenDrawn",
                "drawn_winrate",
                "drawn_win_rate",
                "drawn_wr",
            ),
            field="winrate_when_drawn",
        )
    )
    winrate_when_played = _pct(
        _strict_optional_percent(
            _first_present(
                row,
                "winrate_when_played",
                "winrateWhenPlayed",
                "played_winrate",
                "played_win_rate",
                "played_wr",
            ),
            field="winrate_when_played",
        )
    )
    popularity = _strict_optional_percent(
        _first_present(
            row,
            "popularity",
            "in_runs",
            "in_runs_pct",
            "run_popularity",
        ),
        field="popularity",
    )
    avg_copies = _strict_optional_non_negative_number(
        row.get("avg_copies_in_deck"),
        field="avg_copies_in_deck",
    )

    return {
        **meta,
        "card_id": card_id,
        "tier": winrate_to_tier(win_rate),
        "deck_winrate": deck_winrate,
        "win_rate": win_rate,
        "pick_rate": row.get("pick_rate"),
        "offer_rate": row.get("offer_rate"),
        "offer_bin": row.get("offer_bin"),
        "popularity": row.get("popularity"),
        "in_runs": _pct(popularity),
        "avg_copies": avg_copies,
        "times_played": sample_size,
        "winrate_when_drawn": winrate_when_drawn,
        "winrate_when_played": winrate_when_played,
        "score": row.get("score"),
        "field_availability": {
            field: _arena_rate_availability(sample_size=sample_size)
            for field in (
                "deck_winrate",
                "winrate_when_drawn",
                "winrate_when_played",
            )
        },
    }


def _parse_arena_cards_payload(payload: dict[str, Any], *, locale: str = "ruRU") -> dict[str, list[dict[str, Any]]]:
    raw = payload.get("data")
    by_class: dict[str, list[dict[str, Any]]] = {}
    if isinstance(raw, dict):
        for class_key, rows in raw.items():
            if not isinstance(rows, list):
                continue
            parsed: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                card = normalize_arena_card_row(row, locale=locale)
                if card:
                    card["arena_class"] = class_key
                    parsed.append(card)
            if parsed:
                parsed.sort(key=_win_rate_sort_key, reverse=True)
                by_class[str(class_key)] = parsed
        return by_class
    if isinstance(raw, list):
        parsed = []
        for row in raw:
            if isinstance(row, dict):
                card = normalize_arena_card_row(row, locale=locale)
                if card:
                    parsed.append(card)
        if parsed:
            parsed.sort(key=_win_rate_sort_key, reverse=True)
            by_class["ALL"] = parsed
    return by_class


def _selected_arena_rows(
    payload: dict[str, Any],
    primary_class: str,
) -> tuple[str, list[Any]]:
    raw = payload.get("data")
    if isinstance(raw, list):
        return ("ALL", raw) if primary_class == "ALL" else (primary_class, [])
    if not isinstance(raw, dict):
        return primary_class, []
    rows = raw.get(primary_class)
    if isinstance(rows, list):
        return primary_class, rows
    return primary_class, []


async def _fetch_arena_cards_payload(source_id: str) -> tuple[dict[str, Any], str]:
    # Keep every transport attempt on the canonical card_stats endpoint.  The
    # older /arena/cards API variants now return 404/non-JSON responses, and a
    # curl/proxy failure must not move the refresh away from the valid endpoint
    # before the remaining JSON channels have had a chance to run.
    payload = await fetch_hsreplay_json(
        ARENA_CARD_STATS_API_URL,
        source_id=source_id,
    )
    if not _parse_arena_cards_payload(payload):
        raise RuntimeError("arena card stats payload empty")
    return payload, ARENA_CARD_STATS_API_URL


async def fetch_class_stats(
    *,
    source_id: str = "hsreplay_arena",
) -> dict[str, Any]:
    payload = await fetch_hsreplay_json(CLASSES_STATS_API_URL, source_id=source_id)
    classes = [
        normalize_class_row(row)
        for row in payload.get("data") or []
        if isinstance(row, dict)
    ]
    classes.sort(key=_win_rate_sort_key, reverse=True)

    return {
        "type": "arena_class_matrix",
        "classes": classes,
        # dual-class арена удалена из игры; ключ сохранён пустым для
        # совместимости с потребителями датасета (уберём в Phase 8).
        "matchups": [],
        "source": {
            "key": "hsreplay",
            "url": ARENA_PAGE_URL,
            "api_url": CLASSES_STATS_API_URL,
            "backend": "hsreplay_api",
        },
    }


async def fetch_arena_card_tiers(
    *,
    source_id: str = "hsreplay_arena_cards_advanced",
    locale: str = "ruRU",
    primary_class: str = "ALL",
) -> dict[str, Any]:
    try:
        payload, api_url = await _fetch_arena_cards_payload(source_id)
        by_class = _parse_arena_cards_payload(payload, locale=locale)
        selected_class, raw_rows = _selected_arena_rows(payload, primary_class)
        cards = by_class.get(selected_class) or []
        if not cards:
            raise RuntimeError("arena card stats payload empty")
        eligible_rows = sum(1 for row in raw_rows if isinstance(row, dict))
        upstream_freshness = build_hsreplay_arena_upstream_freshness(
            payload,
            response_headers=get_hsreplay_json_target_headers(api_url),
        )
        return {
            "type": "arena_card_tiers",
            "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
            "upstream_freshness": upstream_freshness,
            "population_completeness": "unverifiable",
            "row_retrieval": row_retrieval_evidence(
                raw_rows=len(raw_rows),
                eligible_rows=eligible_rows,
                normalized_rows=len(cards),
                unexplained_reasons={
                    "non_object_row": len(raw_rows) - eligible_rows,
                    "normalizer_rejected": eligible_rows - len(cards),
                },
                scope=f"primary_class:{selected_class}",
            ),
            "cards": cards,
            "by_class": {key: len(rows) for key, rows in by_class.items()},
            "total_cards": len(cards),
            "primary_class": primary_class,
            "selected_class": selected_class,
            "source": {
                "key": "hsreplay",
                "url": ARENA_CARDS_PAGE_URL,
                "api_url": api_url,
                "backend": "hsreplay_api",
            },
        }
    except Exception as exc:
        logger.warning("HSReplay arena card tiers API failed for %s: %s", source_id, exc)
        raise RuntimeError(
            "HSReplay Arena advanced API unavailable; preserving previous complete Arenasmith dataset"
        ) from exc


def _deck_identity_key(deck: dict[str, Any]) -> str:
    draft_id = deck.get("draft_id")
    if draft_id is not None and str(draft_id).strip():
        return f"draft:{draft_id}"
    deckstring = (deck.get("final_deckstring") or "").strip()
    if deckstring:
        return f"deck:{deckstring}"
    return ""


def _played_at_sort_key(deck: dict[str, Any]) -> float:
    raw = deck.get("played_at")
    if not raw:
        return 0.0
    text = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _load_cached_winning_decks(source_id: str) -> list[dict[str, Any]]:
    dataset = load_dataset(source_id)
    if not dataset:
        return []
    data = dataset.get("data") or {}
    structured = data.get("structured") or data.get("hsreplay_extracted") or {}
    decks = structured.get("decks")
    return list(decks) if isinstance(decks, list) else []


def merge_winning_deck_feed(
    new_decks: list[dict[str, Any]],
    previous_decks: list[dict[str, Any]],
    *,
    feed_cap: int = ARENA_WINNING_DECKS_FEED_CAP,
) -> tuple[list[dict[str, Any]], int]:
    """Merge runs into a deduped feed (newest first). Returns (merged, newly_added_count)."""
    merged: dict[str, dict[str, Any]] = {}

    for deck in previous_decks:
        key = _deck_identity_key(deck)
        if key:
            merged[key] = deck

    for deck in new_decks:
        key = _deck_identity_key(deck)
        if not key:
            continue
        if key not in merged:
            merged[key] = deck
        else:
            # Refresh metadata/cards for the same draft without duplicating the row.
            merged[key] = {**merged[key], **deck}

    ordered = sorted(merged.values(), key=_played_at_sort_key, reverse=True)
    if feed_cap > 0:
        ordered = ordered[:feed_cap]

    existing_keys = {_deck_identity_key(d) for d in previous_decks if _deck_identity_key(d)}
    added = sum(1 for d in new_decks if _deck_identity_key(d) and _deck_identity_key(d) not in existing_keys)
    return ordered, added


async def fetch_winning_decks(
    *,
    source_id: str = "hsreplay_arena_winning_decks",
    feed_cap: int = ARENA_WINNING_DECKS_FEED_CAP,
    locale: str = "ruRU",
) -> dict[str, Any]:
    payload = await fetch_hsreplay_json(WINNING_DECKS_API_URL, source_id=source_id)
    api_rows = payload.get("data") or []
    new_decks: list[dict[str, Any]] = []
    for row in api_rows:
        if not isinstance(row, dict):
            continue
        deck = normalize_winning_deck(row, locale=locale)
        if deck:
            new_decks.append(deck)

    previous = _load_cached_winning_decks(source_id)
    decks, added_count = merge_winning_deck_feed(new_decks, previous, feed_cap=feed_cap)

    logger.info(
        "Arena winning decks: api_rows=%s normalized=%s feed=%s new_unique=%s",
        len(api_rows),
        len(new_decks),
        len(decks),
        added_count,
    )

    return {
        "type": "arena_winning_decks",
        "decks": decks,
        "total_decks": len(decks),
        "api_rows": len(api_rows),
        "fetched_this_run": len(new_decks),
        "new_unique_decks": added_count,
        "feed_cap": feed_cap,
        "source": {
            "key": "hsreplay",
            "url": WINNING_DECKS_URL,
            "api_url": WINNING_DECKS_API_URL,
            "backend": "hsreplay_api",
        },
    }
