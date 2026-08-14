from __future__ import annotations

import json
import math
from typing import Any

from bs4 import BeautifulSoup

from .cards_index import card_label, cards_by_dbfid
from .completeness import (
    COMPLETENESS_SCHEMA_VERSION,
    build_hsreplay_bg_upstream_freshness,
    row_retrieval_evidence,
)
from .firecrawl_backend import scrape_source
from .hsreplay_client import (
    fetch_hsreplay_json,
    fetch_text_via_flaresolverr,
    get_hsreplay_json_target_headers,
)
from .sources import Source

BG_MMR = "TOP_50_PERCENT"
# The rolling seven-day window can mix the previous and current Battlegrounds
# card pools immediately after a patch.  The tier list must only rank minions
# that are legal in the live patch, so use HSReplay's patch-scoped window.
BG_TIME_RANGE = "CURRENT_BATTLEGROUNDS_PATCH"
BG_ANALYTICS_BASE = "https://hsreplay.net/analytics/query"
BG_COMPOSITION_NAMES_API = "https://hsreplay.net/api/v1/battlegrounds/compositions/?hl=en"
COMPOSITION_RU_NAMES = {
    "Beasts": "Звери",
    "Demons": "Демоны",
    "Dragons": "Драконы",
    "Elementals": "Элементали",
    "Mechs": "Механизмы",
    "Murlocs": "Мурлоки",
    "Naga": "Нага",
    "Pirates": "Пираты",
    "Quilboar": "Свинобраз",
    "Undead": "Нежить",
}


def _pct(value: float | int | None) -> str | None:
    if value is None:
        return None
    return f"{float(value):.2f}%"


def _round(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _pct_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace("%", ""))
    except ValueError:
        return 0.0


def _pct_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _metric_availability(
    value: object,
    *,
    aggregates: list[dict[str, Any]],
) -> dict[str, object]:
    if value is not None:
        return {"available": True, "reason": None}
    reason = (
        "no_current_patch_aggregates"
        if not aggregates
        else "insufficient_current_patch_sample"
    )
    return {"available": False, "reason": reason}


def _required_non_negative_number(row: dict[str, Any], field: str) -> float:
    value = row.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"normal_aggregates.{field} must be a non-negative number")
    return float(value)


def _required_non_negative_int(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"normal_aggregates.{field} must be a non-negative integer")
    return value


def _placement_sum(
    row: dict[str, Any],
    *,
    field: str,
    count: int,
) -> float:
    value = _required_non_negative_number(row, field)
    if (count == 0 and value != 0) or (
        count > 0 and not float(count) <= value <= float(count * 8)
    ):
        raise ValueError(
            f"normal_aggregates.{field} must reconcile with a 1..8 placement "
            "for every counted game"
        )
    return value


def _query_url(key: str) -> str:
    return (
        f"{BG_ANALYTICS_BASE}/{key}/"
        f"?BattlegroundsMMRPercentile={BG_MMR}&BattlegroundsTimeRange={BG_TIME_RANGE}"
    )


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = (payload.get("series") or {}).get("data")
    return data if isinstance(data, list) else []


def _card_from_dbf(dbf_id: int) -> dict[str, Any]:
    return card_label(cards_by_dbfid().get(int(dbf_id)))


def _minion_stats(row: dict[str, Any]) -> dict[str, Any] | None:
    dbf_id = row.get("minion_dbf_id")
    if dbf_id is None:
        return None
    if isinstance(dbf_id, bool) or not isinstance(dbf_id, int) or dbf_id <= 0:
        raise ValueError("minion_dbf_id must be a positive integer")
    card = _card_from_dbf(dbf_id)
    if not card.get("id"):
        return None
    aggregates = row.get("normal_aggregates")
    if not isinstance(aggregates, list):
        raise TypeError("normal_aggregates must be a list")
    if any(not isinstance(item, dict) for item in aggregates):
        raise TypeError("normal_aggregates entries must be objects")

    with_count = 0.0
    without_count = 0.0
    with_places = 0.0
    without_places = 0.0
    wins = 0.0
    losses = 0.0
    combat_rounds: list[dict[str, Any]] = []

    for item in aggregates:
        combat_round = item.get("combat_round")
        if combat_round is not None and (
            isinstance(combat_round, bool)
            or not isinstance(combat_round, int)
            or not 1 <= combat_round <= 16
        ):
            raise ValueError("normal_aggregates.combat_round must be 1..16 or null")
        c_with = _required_non_negative_int(item, "count_of_games_with_minion")
        c_without = _required_non_negative_int(item, "count_of_games_without_minion")
        round_with_places = _placement_sum(
            item,
            field="sum_of_placements_for_players_with_minion",
            count=c_with,
        )
        round_without_places = _placement_sum(
            item,
            field="sum_of_placements_for_players_without_minion",
            count=c_without,
        )
        round_wins = _required_non_negative_int(item, "total_wins")
        round_losses = _required_non_negative_int(item, "total_losses")
        round_avg_with = round_with_places / c_with if c_with else None
        round_avg_without = round_without_places / c_without if c_without else None
        round_impact = (
            round_avg_without - round_avg_with
            if round_avg_with is not None and round_avg_without is not None
            else None
        )
        with_count += c_with
        without_count += c_without
        with_places += round_with_places
        without_places += round_without_places
        wins += round_wins
        losses += round_losses
        combat_rounds.append(
            {
                "combat_round": int(combat_round) if isinstance(combat_round, int) else None,
                "games_with_minion": int(c_with) if c_with else 0,
                "games_without_minion": int(c_without) if c_without else 0,
                "avg_placement_with": _round(round_avg_with),
                "avg_placement_without": _round(round_avg_without),
                "impact": _round(round_impact),
                "combat_winrate": _pct(round_wins / (round_wins + round_losses) * 100 if round_wins + round_losses else None),
                "combat_winrate_value": _pct_float(round_wins / (round_wins + round_losses) * 100 if round_wins + round_losses else None),
                "wins": int(round_wins) if round_wins else 0,
                "losses": int(round_losses) if round_losses else 0,
            }
        )

    avg_with = with_places / with_count if with_count else None
    avg_without = without_places / without_count if without_count else None
    impact = (
        avg_without - avg_with
        if avg_with is not None and avg_without is not None
        else None
    )
    combat_winrate = wins / (wins + losses) * 100 if wins + losses else None
    popularity = with_count / (with_count + without_count) * 100 if with_count + without_count else None
    impact_value = _round(impact)
    win_share = _pct(combat_winrate)
    popularity_pct = _pct(popularity)

    return {
        **card,
        "minion": card.get("name"),
        "minion_dbf_id": int(dbf_id),
        "tavern_tier": row.get("minion_tier") or card.get("techLevel"),
        "impact": impact_value,
        "avg_placement_with": _round(avg_with),
        "avg_placement_without": _round(avg_without),
        "combat_winrate": _pct(combat_winrate),
        "combat_winrate_value": _pct_float(combat_winrate),
        "win_share": win_share,
        "popularity": popularity_pct,
        "popularity_value": _pct_float(popularity),
        "games_with_minion": int(with_count) if with_count else None,
        "games_without_minion": int(without_count) if without_count else None,
        "combat_rounds": sorted(
            [item for item in combat_rounds if item.get("combat_round") is not None],
            key=lambda item: int(item["combat_round"]),
        ),
        "field_availability": {
            "impact": _metric_availability(
                impact_value,
                aggregates=aggregates,
            ),
            "win_share": _metric_availability(
                win_share,
                aggregates=aggregates,
            ),
            "popularity": _metric_availability(
                popularity_pct,
                aggregates=aggregates,
            ),
        },
    }


async def fetch_battlegrounds_minions(source_id: str) -> dict[str, Any]:
    url = _query_url("battlegrounds_minion_list")
    cache_key = f"bg:minions:{BG_MMR}:{BG_TIME_RANGE}"
    firecrawl_page: dict[str, Any] = {}
    try:
        scraped = await scrape_source(
            Source(
                source_id,
                "https://hsreplay.net/battlegrounds/minions/#view=advanced",
                "hsreplay",
                "battlegrounds",
                description="HSReplay Battlegrounds minions advanced stats.",
            )
        )
        firecrawl_page = {
            "ok": True,
            "final_url": scraped.final_url,
            "status_code": scraped.status_code,
            "content_length": scraped.content_length,
            "credits_used": scraped.metadata.get("creditsUsed"),
        }
    except Exception as exc:
        firecrawl_page = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    payload = await fetch_hsreplay_json(
        url,
        source_id=source_id,
        cache_key=cache_key,
    )
    upstream_freshness = build_hsreplay_bg_upstream_freshness(
        payload,
        response_headers=get_hsreplay_json_target_headers(cache_key),
    )
    raw_rows = _rows(payload)
    eligible_rows = [row for row in raw_rows if isinstance(row, dict)]
    minions = [
        item
        for row in eligible_rows
        if (item := _minion_stats(row)) is not None
    ]
    minion_ids = [item.get("minion_dbf_id") for item in minions]
    if len(set(minion_ids)) != len(minion_ids):
        raise ValueError("battlegrounds minion payload contains duplicate minion_dbf_id")
    minions.sort(key=lambda item: _pct_number(item.get("popularity")), reverse=True)
    return {
        "type": "bg_minions",
        "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
        "upstream_freshness": upstream_freshness,
        "population_completeness": "unverifiable",
        "row_retrieval": row_retrieval_evidence(
            raw_rows=len(raw_rows),
            eligible_rows=len(eligible_rows),
            normalized_rows=len(minions),
            unexplained_reasons={
                "non_object_row": len(raw_rows) - len(eligible_rows),
                "normalizer_rejected": len(eligible_rows) - len(minions),
            },
            scope="current_patch_minion_rows",
        ),
        "minions": minions,
        "filters": {"mmr_percentile": BG_MMR, "time_range": BG_TIME_RANGE, "turns": "1-16"},
        "source": {
            "key": "hsreplay",
            "url": "https://hsreplay.net/battlegrounds/minions/#view=advanced",
            "api_url": url,
            "backend": "firecrawl+hsreplay_bg_api" if firecrawl_page.get("ok") else "hsreplay_bg_api",
            "firecrawl_page": firecrawl_page,
            "rows": len(minions),
        },
    }


def _composition_names_from_text(text: str) -> dict[int, str]:
    soup = BeautifulSoup(text, "html.parser")
    pre = soup.find("pre")
    raw_text = pre.get_text() if pre else text
    try:
        raw = json.loads(raw_text or "[]")
    except json.JSONDecodeError:
        return {}
    out: dict[int, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            comp_id = item.get("comp_id")
            if comp_id is None:
                comp_id = item.get("id")
            if comp_id is None and isinstance(item.get("friendly_composition"), int):
                comp_id = item.get("friendly_composition")
            friendly_name = item.get("friendly_composition")
            name = item.get("comp_name") or item.get("name")
            if not name and isinstance(friendly_name, str):
                name = friendly_name
            if comp_id is not None and name:
                out[int(comp_id)] = COMPOSITION_RU_NAMES.get(str(name), str(name))
    return out


async def _fetch_composition_names(source_id: str) -> dict[int, str]:
    text = await fetch_text_via_flaresolverr(BG_COMPOSITION_NAMES_API, source_id=source_id)
    return _composition_names_from_text(text)


def _first_place_share(rows: list[dict[str, Any]]) -> dict[int, float]:
    weights: dict[int, float] = {}
    total = 0.0
    for row in rows:
        comp_id = row.get("friendly_composition")
        if comp_id is None or int(comp_id) < 0:
            continue
        distribution = row.get("final_placement_distribution") or []
        if not isinstance(distribution, list) or not distribution:
            continue
        weight = float(row.get("popularity") or 0) / 100 * float(distribution[0] or 0) / 100
        weights[int(comp_id)] = weight
        total += weight
    if not total:
        return {}
    return {comp_id: weight / total * 100 for comp_id, weight in weights.items()}


def _composition_row(
    row: dict[str, Any],
    names: dict[int, str],
    first_place_shares: dict[int, float] | None = None,
) -> dict[str, Any] | None:
    comp_id = row.get("friendly_composition")
    if comp_id is None or int(comp_id) < 0:
        return None
    distribution = row.get("final_placement_distribution") or []
    if not isinstance(distribution, list):
        distribution = []
    first_raw = float(distribution[0] if distribution else 0)
    popularity = float(row.get("popularity") or 0)
    first_place = (
        first_place_shares.get(int(comp_id))
        if first_place_shares is not None
        else first_raw
    )
    return {
        "composition_id": int(comp_id),
        "type": names.get(int(comp_id)) or f"Composition {comp_id}",
        "first_place": _pct(first_place),
        "avg_placement": _round(row.get("avg_final_placement")),
        "popularity": _pct(popularity),
        "placement_distribution": [_pct(value) for value in distribution],
        "games": row.get("num_games"),
    }


async def fetch_battlegrounds_compositions(source_id: str) -> dict[str, Any]:
    stats_url = _query_url("battlegrounds_comp_stats")
    payload = await fetch_hsreplay_json(
        stats_url,
        source_id=source_id,
        cache_key=f"bg:compositions:{BG_MMR}:{BG_TIME_RANGE}",
    )
    names = await _fetch_composition_names(source_id)
    rows = _rows(payload)
    first_place_shares = _first_place_share(rows)
    comps = [
        item
        for row in rows
        if isinstance(row, dict)
        if (item := _composition_row(row, names, first_place_shares)) is not None
    ]
    comps.sort(key=lambda item: _pct_number(item.get("first_place")), reverse=True)
    return {
        "type": "bg_compositions",
        "compositions": comps,
        "filters": {"mmr_percentile": BG_MMR, "time_range": BG_TIME_RANGE},
        "source": {
            "key": "hsreplay",
            "url": "https://hsreplay.net/battlegrounds/compositions/",
            "api_url": stats_url,
            "backend": "hsreplay_bg_api",
            "rows": len(comps),
        },
    }
