from __future__ import annotations

import gzip
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .cards_index import card_from_id
from .scrapers.proxy import httpx_client_kwargs
from .sources import Source

logger = logging.getLogger(__name__)

FIRESTONE_BG_HEROES_API = (
    "https://static.zerotoheroes.com/api/bgs/hero-stats/{mmr}/{time_period}/overview-from-hourly.gz.json"
)
DEFAULT_MMR = "mmr-100"
DEFAULT_TIME_PERIOD = "past-three"

_HEADERS = {
    "accept": "application/json,text/plain,*/*",
    "user-agent": "Hearthstone-Parses/1.0 (+https://github.com/Manacost-Labs/api.kolodahearthstone.com)",
}


def _parse_page_params(url: str) -> tuple[str, str]:
    query = parse_qs(urlparse(url).query)
    time_period = (query.get("time") or [DEFAULT_TIME_PERIOD])[0]
    mmr = (query.get("mmr") or [DEFAULT_MMR])[0]
    if not mmr.startswith("mmr-"):
        mmr = DEFAULT_MMR
    return mmr, time_period


def _pick_rate_pct(total_offered: int | float | None, total_picked: int | float | None) -> tuple[str | None, float | None]:
    if not total_offered:
        return None, None
    try:
        value = 100.0 * float(total_picked or 0) / float(total_offered)
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None
    return f"{value:.2f}%", round(value, 2)


def normalize_hero_row(row: dict[str, Any], *, locale: str = "ruRU") -> dict[str, Any] | None:
    hero_card_id = (row.get("heroCardId") or "").strip()
    if not hero_card_id:
        return None

    meta = card_from_id(hero_card_id, locale=locale)
    pick_rate, pick_rate_value = _pick_rate_pct(row.get("totalOffered"), row.get("totalPicked"))

    avg_raw = row.get("averagePosition")
    avg_placement: float | None = None
    if avg_raw is not None:
        try:
            avg_placement = round(float(avg_raw), 2)
        except (TypeError, ValueError):
            avg_placement = None

    games_raw = row.get("dataPoints")
    games: int | None = None
    if games_raw is not None:
        try:
            games = int(games_raw)
        except (TypeError, ValueError):
            games = None

    return {
        "hero": meta.get("name") or hero_card_id,
        "hero_card_id": hero_card_id,
        "id": hero_card_id,
        "dbfId": meta.get("dbfId"),
        "avg_placement": avg_placement,
        "average_position": avg_placement,
        "pick_rate": pick_rate,
        "pick_rate_value": pick_rate_value,
        "games": games,
        "data_points": games,
        "total_offered": row.get("totalOffered"),
        "total_picked": row.get("totalPicked"),
        "conservative_position": row.get("conservativePositionEstimate"),
        "mmr_percentile": row.get("mmrPercentile"),
    }


async def fetch_firestone_bg_heroes(source: Source, *, locale: str = "ruRU") -> dict[str, Any]:
    mmr, time_period = _parse_page_params(source.url)
    api_url = FIRESTONE_BG_HEROES_API.format(mmr=mmr, time_period=time_period)

    async with httpx.AsyncClient(**httpx_client_kwargs(source.id, timeout=60.0)) as client:
        response = await client.get(api_url, headers=_HEADERS)
        response.raise_for_status()
        raw = response.content

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    payload = json.loads(raw)

    heroes: list[dict[str, Any]] = []
    for row in payload.get("heroStats") or []:
        if not isinstance(row, dict):
            continue
        hero = normalize_hero_row(row, locale=locale)
        if hero:
            heroes.append(hero)

    heroes.sort(key=lambda h: (h.get("avg_placement") is None, h.get("avg_placement") or 99))

    return {
        "type": "bg_heroes",
        "heroes": heroes,
        "blocked": False,
        "time_period": time_period,
        "mmr": mmr,
        "last_update": payload.get("lastUpdateDate"),
        "source": {
            "key": "firestone",
            "url": source.url,
            "api_url": api_url,
            "backend": "firestone_api",
        },
    }
