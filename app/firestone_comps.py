from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import re
from typing import Any

import httpx

from .cards_index import card_from_id
from .post_patch_policy import effective_firestone_minimum_sample
from .refresh_log import log_action
from .sources import Source

logger = logging.getLogger(__name__)

FIRESTONE_STATIC_HOST = "static.zerotoheroes.com"


def _firestone_http_kwargs() -> dict:
    """Firestone CDN is stable; fetch directly without residential proxy."""
    from .config import request_timeout_seconds

    return {
        "timeout": request_timeout_seconds(),
        "follow_redirects": True,
        "limits": httpx.Limits(max_connections=4, max_keepalive_connections=0),
    }


async def _get_static_json(
    url: str,
    *,
    headers: dict[str, str],
    retries: int = 3,
    source_id: str | None = None,
) -> httpx.Response:
    import time

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        started = time.monotonic()
        log_action(
            "firestone.static.try",
            source_id=source_id,
            url=url,
            attempt=attempt,
            extra={"direct": True},
        )
        try:
            async with httpx.AsyncClient(**_firestone_http_kwargs()) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                log_action(
                    "firestone.static.ok",
                    source_id=source_id,
                    url=url,
                    attempt=attempt,
                    http_status=response.status_code,
                    bytes_out=len(response.content),
                    duration_ms=(time.monotonic() - started) * 1000,
                )
                return response
        except Exception as exc:  # noqa: BLE001 - retry any transport failure
            last_exc = exc
            logger.warning("Firestone static fetch %s attempt %d failed: %s", url, attempt, exc)
            log_action(
                "firestone.static.fail",
                source_id=source_id,
                url=url,
                attempt=attempt,
                error_type=type(exc).__name__,
                detail=str(exc)[:1000],
                duration_ms=(time.monotonic() - started) * 1000,
                level="error",
            )
            if attempt < retries:
                await asyncio.sleep(random.uniform(2.5, 6.0) * attempt)  # FIX: jitter
    assert last_exc is not None
    raise last_exc


FIRESTONE_STRATEGIES_URL = "https://static.zerotoheroes.com/hearthstone/data/battlegrounds-strategies/bgs-comps-strategies.gz.json"
FIRESTONE_STATS_URL = "https://static.zerotoheroes.com/api/bgs/comp-stats/past-three/overview-from-hourly.gz.json?v=2"

def slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")

def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()

def difficulty_ru(val: str) -> str:
    v = val.strip().lower()
    if v == "easy":
        return "Легкая"
    if v == "medium":
        return "Средняя"
    if v == "hard":
        return "Сложная"
    return val

def first_tip(strategy: dict[str, Any]) -> str:
    tips = strategy.get("tips") or []
    if not tips:
        return ""
    # Try ruRU first
    for t in tips:
        if t.get("language") == "ruRU" and t.get("tip"):
            return t["tip"]
    # Try enUS
    for t in tips:
        if t.get("language") == "enUS" and t.get("tip"):
            return t["tip"]
    # Fallback to the first available tip
    return tips[0].get("tip") or ""

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

def _card_from_firestone_entry(entry: dict[str, Any]) -> dict[str, Any]:
    card_id = entry.get("cardId")
    name = entry.get("name") or ""
    
    # Resolve card details using our cached and localized HearthstoneJSON index
    card_meta = card_from_id(card_id, locale="ruRU")
    
    res = {
        "count": 1,
        "card_id": card_id,
        "dbfId": card_meta.get("dbfId"),
        "id": card_id,
        "name": card_meta.get("name") or name,
        "image_url": f"https://art.hearthstonejson.com/v1/256x/{card_id}.png",
        "status": str(entry.get("status") or "").strip().upper() or "ADDON",
    }
    
    if entry.get("finalBoardWeight") is not None:
        try:
            res["final_board_weight"] = int(entry["finalBoardWeight"])
        except (ValueError, TypeError):
            res["final_board_weight"] = 0
            
    for k in ("cost", "type", "rarity"):
        if card_meta.get(k):
            res[k] = card_meta[k]
            
    return res

async def fetch_firestone_comps(source: Source) -> dict[str, Any]:
    """
    Fetch and assemble Firestone Battlegrounds compositions.
    This parses:
      1) bgs-comps-strategies.gz.json (strategies metadata)
      2) overview-from-hourly.gz.json (stats, win rates, pick rates, etc.)
    And maps them to the unified bg_comps format.
    """
    headers = {
        "accept": "application/json,text/plain,*/*",
        "user-agent": "KolodaHS BattlegroundsCrawler/1.0 (+https://kolodahs.ru)",
    }
    
    resp_strategies, resp_stats = await asyncio.gather(
        _get_static_json(FIRESTONE_STRATEGIES_URL, headers=headers, source_id=source.id),
        _get_static_json(FIRESTONE_STATS_URL, headers=headers, source_id=source.id),
    )

    content_strategies = resp_strategies.content
    if content_strategies.startswith(b"\x1f\x8b"):
        content_strategies = gzip.decompress(content_strategies)

    content_stats = resp_stats.content
    if content_stats.startswith(b"\x1f\x8b"):
        content_stats = gzip.decompress(content_stats)

    strategies_data = json.loads(content_strategies)
    stats_data = json.loads(content_stats)

    # Index stats by archetype (compId) for fast lookup
    comp_stats_list = stats_data.get("compStats") or []
    stats_by_archetype = {s.get("archetype"): s for s in comp_stats_list if s.get("archetype")}
    
    comps: list[dict[str, Any]] = []
    
    for strategy in strategies_data:
        comp_id = str(strategy.get("compId") or "").strip()
        title_probe = str(strategy.get("name") or "").strip()
        if not comp_id or comp_id == "#N/A" or title_probe in {"", "#N/A"}:
            continue
        if not (strategy.get("cards") or []):
            continue
            
        stats = stats_by_archetype.get(comp_id) or {}
        
        main_cards = []
        additional_cards = []
        
        for entry in strategy.get("cards") or []:
            if not entry or not entry.get("cardId"):
                continue
                
            status = str(entry.get("status") or "").strip().upper()
            card = _card_from_firestone_entry(entry)
            
            if status == "CORE":
                main_cards.append(card)
            else:
                additional_cards.append(card)
                
        # Format and append composition
        title_val = strategy.get("name") or slug_to_title(comp_id)
        description_val = first_tip(strategy)
        difficulty_val = str(strategy.get("difficulty") or "").strip()
        power_level_val = strategy.get("powerLevel")
        
        avg_placement_val = ""
        avg_placement_raw = stats.get("averagePlacement")
        if avg_placement_raw is not None:
            try:
                avg_placement_val = f"{float(avg_placement_raw):.2f}"
            except (ValueError, TypeError):
                pass
                
        games_val = None
        data_points_raw = stats.get("dataPoints")
        if data_points_raw is not None:
            try:
                games_val = int(data_points_raw)
            except (ValueError, TypeError):
                pass
                
        comps.append({
            "id": f"firestone-{slugify(comp_id or title_val)}",
            "comp_id": comp_id,
            "source": "firestone",
            "source_id": comp_id,
            "source_label": "Firestone",
            "title": title_val,
            "name": title_val,
            "description": description_val,
            "difficulty": difficulty_val,
            "difficulty_ru": difficulty_ru(difficulty_val),
            "tier": str(power_level_val) if power_level_val is not None else "",
            "games": games_val,
            "avg_placement": avg_placement_val,
            "main_cards": _group_cards(main_cards),
            "additional_cards": _group_cards(additional_cards),
            "minions": [c.get("name") for c in _group_cards(additional_cards) if c.get("name")],
            "url": "https://www.firestoneapp.com/battlegrounds/comps",
        })
        
    return {
        "type": "bg_comps",
        "comps": comps,
    }


FIRESTONE_CARDS_URL = (
    "https://static.zerotoheroes.com/api/bgs/card-stats/"
    "mmr-100/past-three/overview-from-hourly.gz.json?v=2"
)

async def fetch_firestone_cards(source: Source) -> dict[str, Any]:
    """
    Fetch and assemble Firestone Battlegrounds card stats grouped by Tavern Tier.
    Parses the explicit all-MMR (mmr-100), past-three snapshot used by Firestone.
    If source.id ends with '_spells' or has 'type=spell', filters specifically for BATTLEGROUND_SPELL type.
    Otherwise, filters for minion card types (ignores BATTLEGROUND_SPELL).
    """
    headers = {
        "accept": "application/json,text/plain,*/*",
        "user-agent": "KolodaHS BattlegroundsCrawler/1.0 (+https://kolodahs.ru)",
    }
    
    is_spell = "type=spell" in source.url or source.id.endswith("_spells")
    
    resp = await _get_static_json(FIRESTONE_CARDS_URL, headers=headers, source_id=source.id)
    content = resp.content
    if content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)

    data = json.loads(content)

    card_stats_list = data.get("cardStats") or []
    
    # We will group cards by Tavern Tier (1 to 7)
    tiers: dict[str, list[dict[str, Any]]] = {str(i): [] for i in range(1, 8)}
    tiers["other"] = []
    
    for stat in card_stats_list:
        card_id = stat.get("cardId")
        if not card_id:
            continue
            
        card_meta = card_from_id(card_id, locale="ruRU")
        
        # Apply strict filter to keep only currently active pool minions or spells, 
        # filtering out non-collectible tokens, standard cards, and out-of-rotation cards.
        if is_spell:
            if not card_meta.get("isBattlegroundsPoolSpell"):
                continue
        else:
            if not card_meta.get("isBattlegroundsPoolMinion"):
                continue
                
        tech_level = card_meta.get("techLevel")
        
        avg_placement = stat.get("averagePlacement")
        avg_placement_other = stat.get("averagePlacementOther")
        
        impact = None
        if avg_placement is not None and avg_placement_other is not None:
            try:
                impact = round(float(avg_placement_other) - float(avg_placement), 4)
            except (ValueError, TypeError):
                pass
                
        card_entry = {
            "id": card_id,
            "card_id": card_id,
            "dbfId": card_meta.get("dbfId"),
            "name": card_meta.get("name") or card_id,
            "image_url": f"https://art.hearthstonejson.com/v1/256x/{card_id}.png",
            "tavern_tier": tech_level,
            "total_played": stat.get("totalPlayed"),
            "average_placement": avg_placement,
            "average_placement_other": avg_placement_other,
            "impact": impact,
        }
        
        if tech_level is not None and str(tech_level) in tiers:
            tiers[str(tech_level)].append(card_entry)
        else:
            # Only put cards that have total played stats into 'other' to avoid cluttering with non-BG entries
            if stat.get("totalPlayed", 0) > 10:
                tiers["other"].append(card_entry)
            
    for tier_name in list(tiers.keys()):
        if tier_name == "other" and not tiers[tier_name]:
            tiers.pop(tier_name)
            continue
            
        tiers[tier_name].sort(
            key=lambda c: (
                c.get("impact") if c.get("impact") is not None else -999,
                c.get("total_played") if c.get("total_played") is not None else -999
            ),
            reverse=True
        )
        
    return {
        "type": "bg_card_stats",
        "tiers": tiers,
        "last_update_date": data.get("lastUpdateDate"),
        "total_data_points": data.get("dataPoints"),
        "mmr_bracket": "mmr-100",
        "time_period": "past-three",
        "_fetch_backend": "proxyless_direct",
    }


async def fetch_firestone_arena(source: Source) -> dict[str, Any]:
    """
    Fetch and assemble Firestone Arena card statistics.
    Parses cards/arena/past-3/global.gz.json and draft/arena/past-3/global.gz.json.
    Supports Regular Arena vs Underground Arena (arena-underground),
    and filters by Legendary cards if requested.
    """
    is_underground = "underground" in source.id or "arena-underground" in source.url
    mode = "arena-underground" if is_underground else "arena"
    
    is_legendary_only = "legendary" in source.id or "legendaries" in source.id or "legendary" in source.url
    
    card_stats_url = f"https://static.zerotoheroes.com/api/arena/stats/cards/{mode}/past-3/global.gz.json?v=4"
    draft_stats_url = f"https://static.zerotoheroes.com/api/arena/stats/draft/{mode}/past-3/global.gz.json?v=4"
    
    headers = {
        "accept": "application/json,text/plain,*/*",
        "accept-encoding": "gzip",
        "user-agent": "KolodaHS ArenaCrawler/1.0 (+https://kolodahs.ru)",
    }

    resp_cards = await _get_static_json(
        card_stats_url, headers=headers, source_id=source.id
    )
    content_cards = resp_cards.content
    if content_cards.startswith(b"\x1f\x8b"):
        content_cards = gzip.decompress(content_cards)
    cards_data = json.loads(content_cards.decode("utf-8"))

    draft_data: dict[str, Any] = {}
    try:
        resp_draft = await _get_static_json(
            draft_stats_url, headers=headers, source_id=source.id
        )
        content_draft = resp_draft.content
        if content_draft.startswith(b"\x1f\x8b"):
            content_draft = gzip.decompress(content_draft)
        draft_data = json.loads(content_draft.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - draft statistics are optional
        logger.warning("Failed to parse draft stats: %s", e)
                
    draft_stats_list = draft_data.get("stats") or []
    draft_by_card = {d.get("cardId"): d for d in draft_stats_list if d.get("cardId")}
    
    card_stats_list = cards_data.get("stats") or []
    processed_cards = []
    upstream_scope_stats_count = 0
    
    for stat in card_stats_list:
        card_id = stat.get("cardId")
        if not card_id:
            continue
            
        card_meta = card_from_id(card_id, locale="ruRU")
        rarity = card_meta.get("rarity")
        
        # Apply legendary-only filter
        if is_legendary_only and rarity != "LEGENDARY":
            continue

        # Count the exact upstream population targeted by this source before
        # the local minimum-sample filter removes statistically weak rows.
        upstream_scope_stats_count += 1
                
        stats = stat.get("stats") or {}
        
        # Calculate rates
        decks_with_card = stats.get("decksWithCard") or stats.get("inStartingDeck") or 0
        
        # Keep one-game anomalies out even during the temporary early post-patch window.
        minimum_sample = effective_firestone_minimum_sample(source.id, 30)
        if decks_with_card < minimum_sample:
            continue
            
        decks_with_card_then_win = stats.get("decksWithCardThenWin") or stats.get("wins") or 0
        
        deck_winrate = None
        win_rate = None
        if decks_with_card > 0:
            win_rate = round((decks_with_card_then_win / decks_with_card) * 100, 2)
            deck_winrate = f"{win_rate:.1f}%"
            
        drawn = stats.get("drawn", 0)
        drawn_then_win = stats.get("drawnThenWin", 0)
        drawn_winrate = None
        if drawn > 0:
            drawn_winrate = f"{round((drawn_then_win / drawn) * 100, 1)}%"
            
        in_hand_after_mulligan = stats.get("inHandAfterMulligan", 0)
        in_hand_after_mulligan_then_win = stats.get("inHandAfterMulliganThenWin", 0)
        mulligan_winrate = None
        if in_hand_after_mulligan > 0:
            mulligan_winrate = f"{round((in_hand_after_mulligan_then_win / in_hand_after_mulligan) * 100, 1)}%"
            
        drawn_before_mulligan = stats.get("drawnBeforeMulligan", 0)
        kept_in_mulligan = stats.get("keptInMulligan", 0)
        kept_rate = None
        if drawn_before_mulligan > 0:
            kept_rate = f"{round((kept_in_mulligan / drawn_before_mulligan) * 100, 1)}%"
            
        # Draft stats
        pick_rate_6plus = None
        draft_stat = draft_by_card.get(card_id) or {}
        stat_by_wins = draft_stat.get("statsByWins") or {}
        offered_6plus = 0
        picked_6plus = 0
        for wins_str, counts in stat_by_wins.items():
            try:
                w = int(wins_str)
                if w >= 6:
                    offered_6plus += counts.get("offered", 0)
                    picked_6plus += counts.get("picked", 0)
            except (ValueError, TypeError):
                pass
        if offered_6plus > 0:
            pick_rate_6plus = f"{round((picked_6plus / offered_6plus) * 100, 1)}%"
            
        def winrate_to_tier(wr: float | None) -> str | None:
            if wr is None:
                return None
            if wr >= 58: return "S"
            if wr >= 55: return "A"
            if wr >= 52: return "B"
            if wr >= 49: return "C"
            if wr >= 46: return "D"
            if wr >= 43: return "E"
            return "F"
            
        card_entry = {
            **card_meta,
            "card_id": card_id,
            "image_url": f"https://art.hearthstonejson.com/v1/256x/{card_id}.png",
            "tier": winrate_to_tier(win_rate),
            "deck_winrate": deck_winrate,
            "win_rate": win_rate,
            "drawn_winrate": drawn_winrate,
            "mulligan_winrate": mulligan_winrate,
            "kept_rate": kept_rate,
            "pick_rate": pick_rate_6plus,
            "times_played": decks_with_card,
            "total_games": decks_with_card,
        }
        processed_cards.append(card_entry)
        
    # Sort by winrate descending
    processed_cards.sort(
        key=lambda c: (
            c.get("win_rate") if c.get("win_rate") is not None else -999,
            c.get("total_games") if c.get("total_games") is not None else -999
        ),
        reverse=True
    )
    
    return {
        "type": "arena_card_tiers",
        "cards": processed_cards,
        "total_cards": len(processed_cards),
        "last_update_date": cards_data.get("lastUpdated"),
        "total_data_points": cards_data.get("dataPoints") or len(processed_cards),
        # Keep enough upstream lineage for the regression gate to distinguish a
        # complete post-patch card-pool shrink from an adapter truncation.
        "upstream_stats_count": len(card_stats_list),
        "upstream_scope_stats_count": upstream_scope_stats_count,
        "upstream_context": cards_data.get("context"),
        "arena_mode": mode,
        "legendary_only": is_legendary_only,
    }
