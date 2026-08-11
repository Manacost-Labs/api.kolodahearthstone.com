from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs

from .cards_index import card_from_id, card_label, cards_by_dbfid, resolve_card_name
from .sources import Source

logger = logging.getLogger(__name__)

_CARD_API_PATH_HINTS = ("/api/", "card", "analytics", "meta", "stats")


def _sort_mode(source: Source) -> str:
    frag = source.fragment or ""
    if "includedWinrate" in frag or "winrate" in source.id:
        return "winrate"
    return "popularity"


def _fmt_pct(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}%"
    text = str(value).strip()
    if not text:
        return None
    return text if "%" in text else f"{text}%"


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _card_entry_from_dbf_id(dbf_id: int, *, locale: str = "ruRU") -> dict[str, Any]:
    meta = card_label(cards_by_dbfid().get(dbf_id))
    if locale == "ruRU" and meta.get("id"):
        ru = card_from_id(str(meta["id"]), locale=locale)
        if ru.get("name"):
            meta["name"] = ru["name"]
    return {"count": 1, **meta}


def _normalize_card_row(row: dict[str, Any], *, sort_mode: str, locale: str = "ruRU") -> dict[str, Any] | None:
    dbf_id = row.get("dbfId") or row.get("dbf_id") or row.get("card_dbf_id")
    card_id = row.get("cardId") or row.get("card_id")
    if not card_id and isinstance(row.get("id"), str) and not str(row["id"]).startswith("http"):
        card_id = row.get("id")
    if dbf_id is not None:
        try:
            entry = _card_entry_from_dbf_id(int(dbf_id), locale=locale)
        except (TypeError, ValueError):
            entry = None
    elif card_id and not str(card_id).startswith("http"):
        entry = {**card_from_id(str(card_id), locale=locale), "count": 1}
    else:
        return None
    if not entry or not entry.get("id"):
        return None

    pop = _first_present(
        row,
        "includedPopularity",
        "included_popularity",
        "deck_popularity",
        "popularity",
    )
    wr = _first_present(
        row,
        "includedWinrate",
        "included_winrate",
        "deckWinrate",
        "deck_winrate",
        "winrate",
        "win_rate",
    )
    copies = _first_present(
        row,
        "included_count",
        "includedCount",
        "avgCopies",
        "avg_copies",
        "averageCopiesInDeck",
    )
    played = _first_present(row, "timesPlayed", "times_played", "numGames")
    winrate_when_played = _first_present(
        row, "winrate_when_played", "winrateWhenPlayed"
    )
    winrate_when_drawn = _first_present(
        row, "winrate_when_drawn", "winrateWhenDrawn"
    )
    keep_percentage = _first_present(row, "keep_percentage", "keepPercentage")
    opening_hand_winrate = _first_present(
        row, "opening_hand_winrate", "openingHandWinrate"
    )
    avg_turns_in_hand = _first_present(row, "avg_turns_in_hand", "avgTurnsInHand")
    avg_turn_played_on = _first_present(
        row, "avg_turn_played_on", "avgTurnPlayedOn"
    )

    if wr is not None:
        entry["deck_winrate"] = _fmt_pct(wr)
    if pop is not None:
        entry["deck_popularity"] = _fmt_pct(pop)
    if copies is not None:
        entry["avg_copies"] = copies
    if played is not None:
        entry["times_played"] = played
    if winrate_when_played is not None:
        entry["winrate_when_played"] = _fmt_pct(winrate_when_played)
    if winrate_when_drawn is not None:
        entry["winrate_when_drawn"] = _fmt_pct(winrate_when_drawn)
    if keep_percentage is not None:
        entry["keep_percentage"] = _fmt_pct(keep_percentage)
    if opening_hand_winrate is not None:
        entry["opening_hand_winrate"] = _fmt_pct(opening_hand_winrate)
    if avg_turns_in_hand is not None:
        entry["avg_turns_in_hand"] = avg_turns_in_hand
    if avg_turn_played_on is not None:
        entry["avg_turn_played_on"] = avg_turn_played_on
    return entry


def _coerce_card_row(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if (
        item.get("dbfId")
        or item.get("dbf_id")
        or item.get("cardId")
        or item.get("card_id")
        or item.get("included_popularity") is not None
        or item.get("included_winrate") is not None
    ):
        return item
    card = item.get("card")
    if isinstance(card, dict):
        merged = {**card, **{k: v for k, v in item.items() if k != "card"}}
        return merged
    return item if any(k in item for k in ("includedWinrate", "includedPopularity", "deckWinrate")) else None


def _rows_from_series_data(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            row = _coerce_card_row(item)
            if row:
                rows.append(row)
        return rows
    if not isinstance(data, dict):
        return rows
    for val in data.values():
        if isinstance(val, list):
            for item in val:
                row = _coerce_card_row(item)
                if row:
                    rows.append(row)
        elif isinstance(val, dict):
            if val.get("dbfId") or val.get("cardId"):
                row = _coerce_card_row(val)
                if row:
                    rows.append(row)
            else:
                for inner in val.values():
                    if isinstance(inner, list):
                        for item in inner:
                            row = _coerce_card_row(item)
                            if row:
                                rows.append(row)
                    elif isinstance(inner, dict):
                        row = _coerce_card_row(inner)
                        if row:
                            rows.append(row)
    return rows


def _parse_card_list_analytics(body: dict[str, Any]) -> list[dict[str, Any]]:
    series = body.get("series")
    if isinstance(series, dict):
        data = series.get("data")
        if isinstance(data, dict) and isinstance(data.get("ALL"), list):
            rows = [row for row in (_coerce_card_row(item) for item in data["ALL"]) if row]
        else:
            rows = _rows_from_series_data(data)
        if rows:
            return rows
    return _rows_from_series_data(body.get("data"))


def _flatten_api_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in (_coerce_card_row(x) for x in body) if r]
    if not isinstance(body, dict):
        return []
    if "card_list" in str(body.get("render_as") or "").lower() or "series" in body:
        parsed = _parse_card_list_analytics(body)
        if parsed:
            return parsed
    for key in ("data", "cards", "results", "rows", "card_stats", "all_cards"):
        val = body.get(key)
        if isinstance(val, list):
            rows = [r for r in (_coerce_card_row(x) for x in val) if r]
            if rows:
                return rows
        if isinstance(val, dict):
            rows = _rows_from_series_data(val)
            if rows:
                return rows
    series = body.get("series")
    if isinstance(series, dict):
        rows = _rows_from_series_data(series.get("data"))
        if rows:
            return rows
    return []


def _analytics_card_list_url(source: Source) -> str:
    rank = (_query_param(source, "rankRange") or "GOLD").upper()
    time_range = _query_param(source, "timeRange") or "LAST_14_DAYS"
    game_type = (_query_param(source, "gameType") or "RANKED_STANDARD").upper()
    return (
        "https://hsreplay.net/analytics/query/card_list/"
        f"?GameType={game_type}&TimeRange={time_range}&LeagueRankRange={rank}"
    )


def parse_cards_from_api_payloads(
    payloads: list[tuple[str, Any]],
    *,
    sort_mode: str,
    locale: str = "ruRU",
) -> list[dict[str, Any]]:
    cards_by_dbf: dict[int, dict[str, Any]] = {}
    for url, body in payloads:
        lower = url.lower()
        if "card_list" not in lower and not any(h in lower for h in _CARD_API_PATH_HINTS):
            continue
        rows = _flatten_api_rows(body)
        if not rows:
            continue
        logger.info("hsreplay cards API %s rows=%s", url[:120], len(rows))
        for row in rows:
            entry = _normalize_card_row(row, sort_mode=sort_mode, locale=locale)
            if not entry:
                continue
            dbf = entry.get("dbfId")
            if not dbf:
                continue
            dbf_id = int(dbf)
            current = cards_by_dbf.get(dbf_id)
            if current is None:
                cards_by_dbf[dbf_id] = entry
                continue
            current.update({key: value for key, value in entry.items() if value is not None})
    return list(cards_by_dbf.values())


def _api_payload_diagnostics(payloads: list[tuple[str, Any]]) -> dict[str, Any]:
    row_counts: list[dict[str, Any]] = []
    for url, body in payloads:
        rows = _flatten_api_rows(body)
        row_counts.append(
            {
                "url": url[:180],
                "rows": len(rows),
                "body_type": type(body).__name__,
            }
        )
    return {
        "api_payloads": len(payloads),
        "api_payload_rows_total": sum(item["rows"] for item in row_counts),
        "api_payload_row_counts": row_counts[:12],
    }


def parse_card_stats_from_lines(lines: list[str], *, sort_mode: str) -> list[dict[str, Any]]:
    """Parse virtualized cards list from #react-root innerText."""
    cards: list[dict[str, Any]] = []
    skip_headers = {
        "карта", "card", "mana cost", "shown columns", "deck winrate", "times played",
        "mulligan winrate", "keep percentage", "avg. copies", "в % колод", "содержится",
    }
    i = 0
    while i < len(lines):
        line = lines[i]
        if line == "★":
            i += 1
            continue
        mana: int | None = None
        if line == "7+":
            mana = 7
            i += 1
        elif line.isdigit() and int(line) <= 10:
            mana = int(line)
            i += 1
        else:
            i += 1
            continue
        if i < len(lines) and lines[i] == "★":
            i += 1
        if i >= len(lines):
            break
        name = lines[i]
        if name.lower() in skip_headers or len(name) < 2:
            i += 1
            continue
        resolved = resolve_card_name(name)
        if not resolved.get("id"):
            i += 1
            continue
        entry: dict[str, Any] = {"name": name, "mana": mana, **resolved}
        i += 1
        stats: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if nxt == "★" or nxt == "7+" or (nxt.isdigit() and int(nxt) <= 10):
                break
            if nxt.lower() in skip_headers or len(nxt) > 80:
                i += 1
                continue
            if resolve_card_name(nxt).get("id"):
                break
            if re.match(r"^[\d.,]+%?$", nxt.replace(",", ".")):
                stats.append(nxt)
                i += 1
                continue
            i += 1
        if stats:
            primary = _fmt_pct(stats[0])
            if sort_mode == "winrate":
                entry["deck_winrate"] = primary
            else:
                entry["deck_popularity"] = primary
            if len(stats) > 1 and not str(stats[1]).endswith("%"):
                entry["avg_copies"] = stats[1]
            if len(stats) > 2:
                entry["times_played"] = stats[2]
        cards.append(entry)
    return cards


def _has_metric(card: dict[str, Any]) -> bool:
    return bool(card.get("deck_winrate") or card.get("deck_popularity"))


async def fetch_hsreplay_ranked_cards(source: Source, *, locale: str = "ruRU") -> dict[str, Any]:
    """Load HSReplay Gold cards (14d) via analytics JSON."""
    from .hsreplay_client import fetch_hsreplay_json

    sort_mode = _sort_mode(source)
    api_url = _analytics_card_list_url(source)
    proxy_attempts: list[dict[str, str]] = []
    fetch_backend: str | None = None
    try:
        api_payload = await fetch_hsreplay_json(
            api_url,
            source_id=source.id,
            cache_key=(
                f"cards:{(_query_param(source, 'gameType') or 'RANKED_STANDARD').upper()}:"
                f"{(_query_param(source, 'rankRange') or 'GOLD').upper()}:"
                f"{_query_param(source, 'timeRange') or 'LAST_14_DAYS'}"
            ),
        )
        backend = "hsreplay_cards_api"
    except Exception as direct_error:
        from .hsreplay_card_periods import fetch_hsreplay_card_period_json

        fallback = await fetch_hsreplay_card_period_json(
            api_url,
            source_id=source.id,
        )
        api_payload = fallback.payload
        backend = f"hsreplay_cards_api+{fallback.backend}"
        fetch_backend = fallback.backend
        proxy_attempts = [
            {"backend": "direct", "state": "failed", "error_type": type(direct_error).__name__},
            *fallback.attempts,
        ]
    cards = parse_cards_from_api_payloads(
        [(api_url, api_payload)], sort_mode=sort_mode, locale=locale
    )
    metrics = sum(1 for c in cards if _has_metric(c))
    diagnostics = {
        **_api_payload_diagnostics([(api_url, api_payload)]),
        "api_cards": len(cards),
        "dom_cards": 0,
        "line_cards": 0,
        "merged_cards": len(cards),
        "cards_with_metrics": metrics,
        "line_count": 0,
        "blocked_marker": False,
        "final_url": api_url,
        "http_status": 200,
        "proxy_attempts": proxy_attempts,
    }
    from .post_patch_policy import (
        HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS,
        policy_for,
    )

    early_current_patch = (
        source.id in HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS
        and policy_for(source.id) is not None
    )
    minimum_cards = 20 if early_current_patch else 30
    if len(cards) < minimum_cards or metrics < 20:
        from .refresh_log import log_action

        log_action(
            "api.validate.fail",
            source_id=source.id,
            level="warn",
            detail=(
                f"HSReplay cards API sparse: cards={len(cards)}/{minimum_cards} "
                f"metrics={metrics}/20 "
                f"api_payloads={diagnostics['api_payloads']}"
            ),
            extra={"diagnostics": diagnostics},
        )
        raise RuntimeError(
            f"HSReplay cards API sparse: cards={len(cards)}/{minimum_cards} "
            f"metrics={metrics}/20"
        )

    structured = {
        "type": "card_stats",
        "cards": cards,
        "blocked": False,
        "sort_mode": sort_mode,
        "game_type": _query_param(source, "gameType") or "RANKED_STANDARD",
        "rank_range": _query_param(source, "rankRange"),
        "time_range": _query_param(source, "timeRange"),
        "source": {
            "key": "hsreplay",
            "url": source.url,
            "backend": backend,
            "api_calls": 1,
            "cards_with_metrics": metrics,
            "diagnostics": diagnostics,
        },
    }
    if fetch_backend is not None:
        structured["_fetch_backend"] = fetch_backend
    return structured


def _query_param(source: Source, key: str) -> str | None:
    frag = source.fragment or ""
    params = parse_qs(frag, keep_blank_values=True)
    vals = params.get(key)
    return vals[0] if vals else None
