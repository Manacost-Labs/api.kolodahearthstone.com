from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from .cards_index import card_label, cards_by_dbfid
from .hsreplay_bg_stats import BG_COMPOSITION_NAMES_API, _composition_names_from_text
from .hsreplay_client import fetch_hsreplay_json, fetch_text_via_flaresolverr
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

SOURCE_ID = "hsreplay_battlegrounds_hero_details"
HEROES_SOURCE_ID = "hsreplay_battlegrounds_heroes"
BG_MMR = "TOP_50_PERCENT"
BG_TIME_RANGE = "CURRENT_BATTLEGROUNDS_PATCH"
ANALYTICS_BASE = "https://hsreplay.net/api/v1/analytics/query"
HEROES_API = "https://hsreplay.net/api/v1/battlegrounds/heroes/"
DUOS_HEROES_API = "https://hsreplay.net/api/v1/battlegrounds/duos/heroes/"

DETAIL_ENDPOINTS = {
    "tavern_up": "battlegrounds_tavern_up_stats_by_hero_and_tier",
    "hero_power": "battlegrounds_hero_power_stats_by_hero_and_tier",
    "combat_winrate": "battlegrounds_combat_winrate_by_hero",
    "composition_stats": "battlegrounds_comp_stats_by_hero",
    "composition_affinity": "battlegrounds_composition_affinity_by_hero",
    "best_final_form": "battlegrounds_best_final_form_comps",
    "canonical_compositions": "battlegrounds_canonical_compositions_by_hero",
}


def _pct(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 3) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _card(dbf_id: int | None) -> dict[str, Any]:
    if dbf_id is None:
        return {}
    return card_label(cards_by_dbfid().get(int(dbf_id)))


def _hero_name(dbf_id: int) -> str:
    return _card(dbf_id).get("name") or f"Hero {dbf_id}"


def _query_url(endpoint: str, params: dict[str, Any]) -> str:
    return f"{ANALYTICS_BASE}/{endpoint}/?{urlencode(params)}"


def _heroes_url(*, duos: bool, mmr: str, time_range: str) -> str:
    base = DUOS_HEROES_API if duos else HEROES_API
    return f"{base}?{urlencode({'BattlegroundsMMRPercentile': mmr, 'BattlegroundsTimeRange': time_range})}"


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = (payload.get("series") or {}).get("data")
    return data if isinstance(data, list) else []


def _series_dict(payload: dict[str, Any]) -> dict[str, Any]:
    data = (payload.get("series") or {}).get("data")
    return data if isinstance(data, dict) else {}


def _as_of(payload: dict[str, Any]) -> str | None:
    value = payload.get("as_of")
    return str(value) if value else None


async def _composition_names(source_id: str = SOURCE_ID) -> dict[int, str]:
    text = await fetch_text_via_flaresolverr(BG_COMPOSITION_NAMES_API, source_id=source_id)
    return _composition_names_from_text(text)


async def _fetch_json(url: str, *, cache_key: str) -> dict[str, Any]:
    return await fetch_hsreplay_json(url, source_id=SOURCE_ID, cache_key=cache_key)


async def fetch_hero_index(
    *,
    duos: bool = False,
    mmr: str = BG_MMR,
    time_range: str = BG_TIME_RANGE,
) -> dict[str, Any]:
    url = _heroes_url(duos=duos, mmr=mmr, time_range=time_range)
    payload = await _fetch_json(url, cache_key=f"bg:heroes:index:{'duos' if duos else 'solo'}:{mmr}:{time_range}")
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    heroes = [_hero_row(row) for row in rows if isinstance(row, dict) and row.get("hero_dbf_id") is not None]
    heroes.sort(key=lambda row: (_tier_sort(row.get("tier")), _num(row.get("avg_placement") or 99), row.get("hero") or ""))
    return {
        "mode": "duos" if duos else "solo",
        "count": len(heroes),
        "heroes": heroes,
        "filters": {"mmr_percentile": mmr, "time_range": time_range},
        "source": {"api_url": url, "backend": "hsreplay_json_api"},
    }


def _tier_sort(tier: Any) -> int:
    order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6}
    return order.get(str(tier or "").upper(), 99)


def _hero_row(row: dict[str, Any], composition_names: dict[int, str] | None = None) -> dict[str, Any]:
    dbf_id = int(row["hero_dbf_id"])
    best_comp = row.get("best_composition")
    best_comp_id = int(best_comp) if best_comp is not None else None
    return {
        "hero": _hero_name(dbf_id),
        "dbfId": dbf_id,
        "id": _card(dbf_id).get("id"),
        "tier": str(row.get("tier_v2") or "").upper() or None,
        "pick_rate": _pct(row.get("pick_rate")),
        "pick_rate_value": _round(row.get("pick_rate"), 2),
        "avg_placement": _round(row.get("avg_final_placement"), 3),
        "adjusted_avg_placement": _round(row.get("adjusted_avg_final_placement"), 3),
        "placement_distribution": [_pct(value) for value in row.get("final_placement_distribution") or []],
        "best_composition_id": best_comp_id,
        "best_composition": composition_names.get(best_comp_id) if composition_names and best_comp_id else None,
        "key_minions_top3": [_card(int(dbf_id)) for dbf_id in row.get("key_minions_top3") or []],
        "anomaly_adjusted": bool(row.get("anomaly_adjusted")),
    }


def _normalize_tavern_up(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _rows(payload):
        turn = row.get("recruit_round")
        tier = row.get("end_of_recruit_round_tier")
        if turn is None or tier is None:
            continue
        out.append(
            {
                "turn": int(turn),
                "tavern_tier": int(tier),
                "occurrences": int(row.get("occurrences") or 0),
                "pct_at_tier": _round(row.get("pct_at_tier"), 2),
                "num_games": int(row.get("num_games") or 0),
            }
        )
    return sorted(out, key=lambda item: (item["turn"], item["tavern_tier"]))


def _tavern_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row["turn"] <= 16:
            by_turn.setdefault(int(row["turn"]), []).append(row)
    out = []
    for turn, items in sorted(by_turn.items()):
        best = max(items, key=lambda row: row.get("pct_at_tier") or 0)
        out.append(
            {
                "turn": turn,
                "recommended_tavern_tier": best["tavern_tier"],
                "pct_at_tier": best["pct_at_tier"],
                "num_games": best["num_games"],
            }
        )
    return out


def _normalize_hero_power(payload: dict[str, Any], *, time_range: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for row in _rows(payload):
        turn = row.get("recruit_round")
        tier = row.get("tavern_period")
        if turn is None or tier is None:
            continue
        rows.append(
            {
                "turn": int(turn),
                "tavern_tier": int(tier),
                "gold": int(row.get("gold") or 0),
                "end_of_round_median_tavern_tier": int(row.get("end_of_round_median_tavern_tier") or 0),
                "times_invoked": _round(row.get("times_invoked"), 2),
                "invoked_rate": _round(row.get("invoked_rate"), 2),
                "total_data_points": int(row.get("total_data_points") or 0),
            }
        )
    threshold = 50 if time_range == "LAST_7_DAYS" else 100
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row["total_data_points"] > threshold:
            by_turn.setdefault(row["turn"], []).append(row)
    aggregate = []
    for turn, items in sorted(by_turn.items()):
        total = sum(_num(item.get("total_data_points")) for item in items)
        if total <= 0:
            continue
        invoked = sum(_num(item.get("invoked_rate")) * _num(item.get("total_data_points")) for item in items) / total
        aggregate.append({"turn": turn, "invoked_rate": round(invoked, 2), "total_data_points": int(total)})
    return sorted(rows, key=lambda item: (item["turn"], item["tavern_tier"])), aggregate


def _normalize_composition_stats(payload: dict[str, Any], names: dict[int, str]) -> list[dict[str, Any]]:
    comps = []
    for row in _rows(payload):
        comp_id = row.get("friendly_composition")
        if comp_id is None:
            continue
        comp_id = int(comp_id)
        comps.append(
            {
                "composition_id": comp_id,
                "name": names.get(comp_id) or f"Composition {comp_id}",
                "num_games": int(row.get("num_games") or 0),
                "avg_placement": _round(row.get("avg_final_placement"), 3),
                "placement_distribution": [_pct(value) for value in row.get("final_placement_distribution") or []],
                "confidence_interval": _round(row.get("confidence_interval"), 3),
                "popularity": _pct(row.get("popularity")),
                "popularity_value": _round(row.get("popularity"), 2),
                "popularity_first_place": _pct(row.get("popularity_first_place")),
                "popularity_top_4": _pct(row.get("popularity_top_4")),
                "is_recent": bool(row.get("is_recent")),
                "num_days": row.get("num_days"),
            }
        )
    comps.sort(key=lambda item: (_num(item.get("avg_placement") or 99), -_num(item.get("popularity_value"))))
    return comps


def _normalize_lineups(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for row in _rows(payload):
        comp_id = row.get("friendly_composition")
        lineup = row.get("lineup")
        if comp_id is None or not isinstance(lineup, list):
            continue
        cards = []
        for item in lineup:
            if not isinstance(item, dict) or item.get("minion_dbf_id") is None:
                continue
            card = _card(int(item["minion_dbf_id"]))
            cards.append(
                {
                    **card,
                    "minion": card.get("name"),
                    "minion_dbf_id": int(item["minion_dbf_id"]),
                    "zone_position": item.get("zone_position"),
                    "premium": bool(item.get("premium")),
                    "attack": item.get("attack"),
                    "health": item.get("health"),
                    "taunt": bool(item.get("taunt")),
                    "poison": bool(item.get("poison")),
                    "divine_shield": bool(item.get("divine_shield")),
                }
            )
        out[int(comp_id)] = sorted(cards, key=lambda item: int(item.get("zone_position") or 99))
    return out


def _normalize_final_form(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    by_comp: dict[int, list[dict[str, Any]]] = {}
    for row in _rows(payload):
        comp_id = row.get("friendly_composition")
        dbf_id = row.get("minion_dbf_id")
        if comp_id is None or dbf_id is None:
            continue
        card = _card(int(dbf_id))
        by_comp.setdefault(int(comp_id), []).append(
            {
                **card,
                "minion": card.get("name"),
                "minion_dbf_id": int(dbf_id),
                "tavern_tier": row.get("tier"),
                "at_least_one": _pct(row.get("at_least_one")),
                "more_than_one": _pct(row.get("more_than_one")),
                "at_least_one_premium": _pct(row.get("at_least_one_premium")),
                "normal_attack_avg": _round(row.get("normal_attack_avg"), 1),
                "normal_health_avg": _round(row.get("normal_health_avg"), 1),
                "premium_attack_avg": _round(row.get("premium_attack_avg"), 1),
                "premium_health_avg": _round(row.get("premium_health_avg"), 1),
                "divine_shield_buff_freq": _pct(row.get("divine_shield_buff_freq")),
                "taunt_buff_freq": _pct(row.get("taunt_buff_freq")),
                "poison_buff_freq": _pct(row.get("poison_buff_freq")),
                "position_freq": [_pct(value) for value in row.get("position_freq") or []],
            }
        )
    for rows in by_comp.values():
        rows.sort(key=lambda item: _num(str(item.get("at_least_one") or "0").rstrip("%")), reverse=True)
    return by_comp


def _combat_for_hero(payload: dict[str, Any], dbf_id: int) -> list[dict[str, Any]]:
    rows = _series_dict(payload).get(str(dbf_id)) or []
    if not isinstance(rows, list):
        return []
    return [
        {
            "combat_round": int(row.get("combat_round") or 0),
            "data_points": int(row.get("data_points") or 0),
            "combat_winrate": _round(row.get("combat_winrate"), 2),
        }
        for row in rows
        if isinstance(row, dict) and row.get("combat_round") is not None
    ]


async def fetch_hero_detail(
    dbf_id: int,
    *,
    hero_index_row: dict[str, Any] | None = None,
    composition_names: dict[int, str] | None = None,
    mmr: str = BG_MMR,
    time_range: str = BG_TIME_RANGE,
) -> dict[str, Any]:
    names = composition_names or await _composition_names()
    params = {"hero_dbf_id": int(dbf_id), "BattlegroundsMMRPercentile": mmr, "BattlegroundsTimeRange": time_range}
    payloads: dict[str, dict[str, Any]] = {}
    for key, endpoint in DETAIL_ENDPOINTS.items():
        payloads[key] = await _fetch_json(
            _query_url(endpoint, params),
            cache_key=f"bg:hero:{dbf_id}:{key}:{mmr}:{time_range}",
        )
    tavern_up = _normalize_tavern_up(payloads["tavern_up"])
    hero_power, hero_power_by_turn = _normalize_hero_power(payloads["hero_power"], time_range=time_range)
    comps = _normalize_composition_stats(payloads["composition_stats"], names)
    lineups = _normalize_lineups(payloads["canonical_compositions"])
    final_forms = _normalize_final_form(payloads["best_final_form"])
    hero = _hero_row(hero_index_row or {"hero_dbf_id": int(dbf_id)}, names)
    best_id = hero.get("best_composition_id") or (comps[0]["composition_id"] if comps else None)
    best_comp = next((comp for comp in comps if comp["composition_id"] == best_id), comps[0] if comps else None)
    if best_comp:
        best_comp = {
            **best_comp,
            "lineup": lineups.get(int(best_comp["composition_id"]), []),
            "final_form_minions": final_forms.get(int(best_comp["composition_id"]), []),
        }
    return {
        "hero": hero,
        "mode": "solo",
        "filters": {"mmr_percentile": mmr, "time_range": time_range},
        "source_url": f"https://hsreplay.net/battlegrounds/heroes/{dbf_id}/-?mmrPercentile={mmr}&timeRange={time_range}",
        "as_of": {key: _as_of(payload) for key, payload in payloads.items()},
        "tavern_up": tavern_up,
        "tavern_up_by_turn": _tavern_recommendations(tavern_up),
        "hero_power": hero_power,
        "hero_power_by_turn": hero_power_by_turn,
        "combat_winrate": _combat_for_hero(payloads["combat_winrate"], int(dbf_id)),
        "compositions": [
            {
                **comp,
                "lineup": lineups.get(int(comp["composition_id"]), []),
                "final_form_minions": final_forms.get(int(comp["composition_id"]), [])[:12],
            }
            for comp in comps
        ],
        "best_composition": best_comp,
    }


async def _refresh_bg_hero_details_unlocked(
    *,
    limit: int | None = None,
    mmr: str = BG_MMR,
    time_range: str = BG_TIME_RANGE,
    concurrency: int = 3,
) -> dict[str, Any]:
    fetched_at = datetime.now(UTC).isoformat()
    names = await _composition_names()
    solo_index = await fetch_hero_index(duos=False, mmr=mmr, time_range=time_range)
    duos_index = await fetch_hero_index(duos=True, mmr=mmr, time_range=time_range)
    index_rows = solo_index["heroes"][: limit or None]
    source_rows = {
        int(row["dbfId"]): {
            "hero_dbf_id": int(row["dbfId"]),
            "tier_v2": row.get("tier"),
            "pick_rate": row.get("pick_rate_value"),
            "avg_final_placement": row.get("avg_placement"),
            "adjusted_avg_final_placement": row.get("adjusted_avg_placement"),
            "final_placement_distribution": [
                _num(str(value).rstrip("%")) for value in row.get("placement_distribution") or []
            ],
            "best_composition": row.get("best_composition_id"),
            "key_minions_top3": [item.get("dbfId") for item in row.get("key_minions_top3") or [] if item.get("dbfId")],
        }
        for row in index_rows
    }
    sem = asyncio.Semaphore(max(1, concurrency))
    errors: list[dict[str, Any]] = []

    async def _one(dbf_id: int) -> dict[str, Any] | None:
        async with sem:
            try:
                return await fetch_hero_detail(
                    dbf_id,
                    hero_index_row=source_rows[dbf_id],
                    composition_names=names,
                    mmr=mmr,
                    time_range=time_range,
                )
            except Exception as exc:
                errors.append({"dbfId": dbf_id, "error": f"{type(exc).__name__}: {str(exc)[:240]}"})
                return None

    details = [item for item in await asyncio.gather(*[_one(int(row["dbfId"])) for row in index_rows]) if item]
    heroes_by_dbf = {int(item["hero"]["dbfId"]): item for item in details}
    heroes = []
    for row in solo_index["heroes"]:
        detail = heroes_by_dbf.get(int(row["dbfId"]))
        heroes.append(
            {
                **row,
                "detail_available": detail is not None,
                "best_composition": (detail or {}).get("best_composition") or {
                    "composition_id": row.get("best_composition_id"),
                    "name": row.get("best_composition"),
                },
            }
        )
    payload = {
        "type": "bg_hero_details",
        "fetched_at": fetched_at,
        "mode": "solo",
        "filters": {"mmr_percentile": mmr, "time_range": time_range},
        "heroes": heroes,
        "details": {str(item["hero"]["dbfId"]): item for item in details},
        "duos": duos_index,
        "errors": errors,
        "source": {
            "backend": "hsreplay_json_api",
            "hero_count": len(heroes),
            "detail_count": len(details),
            "duos_hero_count": len(duos_index.get("heroes") or []),
            "composition_names": len(names),
        },
    }
    solo_count = len(solo_index.get("heroes") or [])
    duos_count = len(duos_index.get("heroes") or [])
    requested_details = len(index_rows)
    detail_coverage = len(details) / max(requested_details, 1)
    quality_errors: list[str] = []
    if limit is not None:
        quality_errors.append("limited refresh is diagnostic and cannot replace the production snapshot")
    if solo_count < 30:
        quality_errors.append(f"solo hero index too small ({solo_count} < 30)")
    if duos_count < 20:
        quality_errors.append(f"duos hero index too small ({duos_count} < 20)")
    minimum_details = max(20, math.ceil(requested_details * 0.70))
    if len(details) < minimum_details:
        quality_errors.append(
            f"hero detail coverage too low ({len(details)}/{requested_details}; minimum {minimum_details})"
        )
    quality_ok = not quality_errors
    dataset = {
        "state": SourceState.OK,
        "fetched_at": fetched_at,
        "http_status": 200,
        "final_url": HEROES_API,
        "content_length": None,
        "backend": "hsreplay_json_api",
        "data": {"structured": payload},
    }
    cached_dataset = load_dataset(SOURCE_ID)
    if quality_ok:
        save_dataset(SOURCE_ID, dataset)
        heroes_dataset = {
            "state": SourceState.OK,
            "fetched_at": fetched_at,
            "http_status": 200,
            "final_url": HEROES_API,
            "content_length": None,
            "backend": "hsreplay_json_api",
            "data": {
                "structured": {
                    "type": "bg_heroes",
                    "fetched_at": fetched_at,
                    "heroes": heroes,
                    "blocked": False,
                    "filters": payload["filters"],
                    "source": {
                        "backend": "hsreplay_json_api",
                        "url": solo_index.get("source", {}).get("api_url") or HEROES_API,
                        "mirrored_from": SOURCE_ID,
                    },
                }
            },
        }
        save_dataset(HEROES_SOURCE_ID, heroes_dataset)
        save_status(
            HEROES_SOURCE_ID,
            {
                "source_id": HEROES_SOURCE_ID,
                "site": "hsreplay",
                "category": "battlegrounds",
                "url": HEROES_API,
                "state": SourceState.OK,
                "fetched_at": fetched_at,
                "http_status": 200,
                "backend": "hsreplay_json_api",
                "rows_total": len(heroes),
                "detail": (
                    f"BG hero index mirrored from {SOURCE_ID}: "
                    f"{len(heroes)} solo heroes."
                ),
                "serving_cached_dataset": False,
                "last_refresh_state": SourceState.OK,
                "last_refresh_at": fetched_at,
            },
        )
    save_status(
        SOURCE_ID,
        {
            "source_id": SOURCE_ID,
            "site": "hsreplay",
            "category": "battlegrounds",
            "url": HEROES_API,
            "state": SourceState.OK if quality_ok else SourceState.PARTIAL,
            "fetched_at": fetched_at,
            "http_status": 200,
            "backend": "hsreplay_json_api",
            "rows_total": len(details),
            "detail": (
                f"BG hero details: {len(details)}/{requested_details} requested, "
                f"{solo_count} solo heroes, {duos_count} duos heroes."
                + (f" Quality gate: {'; '.join(quality_errors)}" if quality_errors else "")
            ),
            "errors": errors[:10],
            "quality_errors": quality_errors,
            "detail_coverage": round(detail_coverage, 4),
            "serving_cached_dataset": bool(cached_dataset) and not quality_ok,
            "last_refresh_state": SourceState.OK if quality_ok else SourceState.PARTIAL,
            "last_refresh_at": fetched_at,
        },
    )
    return {
        "ok": quality_ok,
        "published": quality_ok,
        "serving_cached_dataset": bool(cached_dataset) and not quality_ok,
        "source_id": SOURCE_ID,
        "fetched_at": fetched_at,
        "heroes": len(heroes),
        "details": len(details),
        "duos_heroes": len(duos_index.get("heroes") or []),
        "errors": errors,
        "quality_errors": quality_errors,
        "detail_coverage": round(detail_coverage, 4),
    }


async def refresh_bg_hero_details(
    *,
    limit: int | None = None,
    mmr: str = BG_MMR,
    time_range: str = BG_TIME_RANGE,
    concurrency: int = 3,
) -> dict[str, Any]:
    """Refresh hero details unless another process owns this source."""
    locks = ResourceLockSet(
        [SOURCE_ID, HEROES_SOURCE_ID],
        metadata={"operation": "refresh_bg_hero_details"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE_ID,
            **exc.as_outcome(),
        }

    try:
        from .hsreplay_client import reset_hsreplay_refresh_state

        reset_hsreplay_refresh_state()
        return await _refresh_bg_hero_details_unlocked(
            limit=limit,
            mmr=mmr,
            time_range=time_range,
            concurrency=concurrency,
        )
    finally:
        locks.release()


def _fallback_heroes_dataset() -> dict[str, Any] | None:
    dataset = load_dataset(HEROES_SOURCE_ID) or {}
    structured = (dataset.get("data") or {}).get("structured") or {}
    if structured.get("type") != "bg_heroes":
        return None
    return structured


def load_bg_hero_details() -> dict[str, Any]:
    dataset = load_dataset(SOURCE_ID) or {}
    structured = (dataset.get("data") or {}).get("structured") or {}
    if structured.get("type") == "bg_hero_details":
        return structured
    fallback = _fallback_heroes_dataset()
    heroes = fallback.get("heroes") if fallback else []
    return {
        "type": "bg_hero_details",
        "fetched_at": dataset.get("fetched_at"),
        "mode": "fallback",
        "filters": fallback.get("filters") if fallback else {},
        "heroes": heroes or [],
        "details": {},
        "duos": {"mode": "duos", "count": 0, "heroes": []},
        "source": {"backend": "fallback_hsreplay_battlegrounds_heroes", "detail_count": 0},
    }


def list_bg_heroes(*, mode: str = "solo", q: str | None = None) -> dict[str, Any]:
    payload = load_bg_hero_details()
    if mode == "duos":
        rows = list((payload.get("duos") or {}).get("heroes") or [])
    else:
        rows = list(payload.get("heroes") or [])
    if q:
        needle = q.lower()
        rows = [row for row in rows if needle in str(row.get("hero") or "").lower()]
    rows.sort(key=lambda row: (_tier_sort(row.get("tier")), _num(row.get("avg_placement") or 99), row.get("hero") or ""))
    return {
        "type": "bg_heroes",
        "mode": mode,
        "count": len(rows),
        "fetched_at": payload.get("fetched_at"),
        "filters": payload.get("filters"),
        "source": payload.get("source"),
        "heroes": rows,
    }


def get_bg_hero(dbf_id: int) -> dict[str, Any] | None:
    payload = load_bg_hero_details()
    details = payload.get("details") or {}
    detail = details.get(str(int(dbf_id)))
    if detail:
        return detail
    for row in payload.get("heroes") or []:
        if int(row.get("dbfId") or 0) == int(dbf_id):
            return {
                "hero": row,
                "mode": "fallback",
                "filters": payload.get("filters"),
                "source_url": f"https://hsreplay.net/battlegrounds/heroes/{dbf_id}/-",
                "tavern_up": [],
                "tavern_up_by_turn": [],
                "hero_power": [],
                "hero_power_by_turn": [],
                "combat_winrate": [],
                "compositions": [],
                "best_composition": row.get("best_composition"),
            }
    return None
