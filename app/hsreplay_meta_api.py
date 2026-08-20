from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from .completeness import COMPLETENESS_SCHEMA_VERSION, row_retrieval_evidence
from .firecrawl_backend import scrape_source
from .hsreplay_client import fetch_hsreplay_json
from .sources import Source

HSREPLAY_ANALYTICS_BASE = "https://hsreplay.net/analytics/query"
ARCHETYPE_DICT_URL = "https://hsreplay.net/api/v1/archetypes/?hl=ru"

CLASS_RU_NAMES = {
    "DEATHKNIGHT": "Рыцарь смерти",
    "DEMONHUNTER": "Охотник на демонов",
    "DRUID": "Друид",
    "HUNTER": "Охотник",
    "MAGE": "Маг",
    "PALADIN": "Паладин",
    "PRIEST": "Жрец",
    "ROGUE": "Разбойник",
    "SHAMAN": "Шаман",
    "WARLOCK": "Чернокнижник",
    "WARRIOR": "Воин",
}


def _query_param(source: Source, key: str) -> str | None:
    params = parse_qs(source.fragment or "", keep_blank_values=True)
    values = params.get(key)
    return values[0] if values else None


def _fmt_pct(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return f"{float(value):.2f}%"


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _meta_archetypes_url(source: Source) -> str:
    rank = (_query_param(source, "rankRange") or "LEGEND").upper()
    region = (_query_param(source, "region") or "REGION_EU").upper()
    time_range = (_query_param(source, "timeFrame") or _query_param(source, "timeRange") or "LAST_1_DAY").upper()
    game_type = (_query_param(source, "gameType") or "RANKED_STANDARD").upper()
    return (
        f"{HSREPLAY_ANALYTICS_BASE}/archetype_popularity_distribution_stats_v2/"
        f"?GameType={game_type}&LeagueRankRange={rank}&Region={region}&TimeRange={time_range}"
    )


async def _archetype_name_map(source_id: str) -> dict[int, dict[str, Any]]:
    payload = await fetch_hsreplay_json(
        ARCHETYPE_DICT_URL,
        source_id=source_id,
        cache_key="hsreplay:archetype-dictionary:ru",
    )
    raw = payload.get("data")
    if not isinstance(raw, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        out[int(item["id"])] = item
    return out


def _fallback_archetype_name(archetype_id: int, class_key: str) -> str:
    if archetype_id < 0:
        return f"Другое ({CLASS_RU_NAMES.get(class_key, class_key.title())})"
    return f"Архетип #{archetype_id}"


def normalize_meta_archetypes(
    payload: dict[str, Any],
    archetype_names: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    data = ((payload.get("series") or {}).get("data") or {})
    if not isinstance(data, dict):
        return []

    classes: list[dict[str, Any]] = []
    for class_key in sorted(data):
        rows = data.get(class_key) or []
        if not isinstance(rows, list):
            continue
        archetypes: list[dict[str, Any]] = []
        class_games = 0
        for row in rows:
            if not isinstance(row, dict) or row.get("archetype_id") is None:
                continue
            archetype_id = int(row["archetype_id"])
            archetype_meta = archetype_names.get(archetype_id) or {}
            games = int(row.get("total_games") or 0)
            class_games += games
            archetypes.append(
                {
                    "archetype_id": archetype_id,
                    "archetype": archetype_meta.get("name")
                    or _fallback_archetype_name(archetype_id, class_key),
                    "url": archetype_meta.get("url") or None,
                    "winrate": _fmt_pct(row.get("win_rate")),
                    "popularity": _fmt_pct(row.get("pct_of_total")),
                    "class_popularity": _fmt_pct(row.get("pct_of_class")),
                    "games": games,
                    "raw_winrate": _num(row.get("win_rate")),
                    "raw_popularity": _num(row.get("pct_of_total")),
                }
            )
        archetypes.sort(key=lambda item: (item["games"], item["raw_popularity"]), reverse=True)
        if archetypes:
            classes.append(
                {
                    "class": class_key,
                    "class_name": CLASS_RU_NAMES.get(class_key, class_key.title()),
                    "games": class_games,
                    "archetypes": archetypes,
                }
            )
    classes.sort(key=lambda item: item["games"], reverse=True)
    return classes


def _meta_archetype_row_evidence(
    payload: dict[str, Any],
    *,
    normalized_rows: int,
) -> dict[str, Any]:
    data = ((payload.get("series") or {}).get("data") or {})
    if not isinstance(data, dict):
        return row_retrieval_evidence(
            raw_rows=1,
            eligible_rows=1,
            normalized_rows=0,
            unexplained_reasons={"invalid_class_payload": 1},
            scope="hsreplay_meta_archetype_rows",
        )

    raw_rows = 0
    invalid_class_payloads = 0
    invalid_archetype_rows = 0
    for rows in data.values():
        if not isinstance(rows, list):
            raw_rows += 1
            invalid_class_payloads += 1
            continue
        raw_rows += len(rows)
        invalid_archetype_rows += sum(
            1
            for row in rows
            if not isinstance(row, dict) or row.get("archetype_id") is None
        )

    unexplained_reasons = {
        reason: count
        for reason, count in (
            ("invalid_class_payload", invalid_class_payloads),
            ("invalid_archetype_row", invalid_archetype_rows),
        )
        if count
    }
    unclassified_loss = raw_rows - normalized_rows - sum(
        unexplained_reasons.values()
    )
    if unclassified_loss:
        unexplained_reasons["normalization_loss"] = unclassified_loss
    return row_retrieval_evidence(
        raw_rows=raw_rows,
        eligible_rows=raw_rows,
        normalized_rows=normalized_rows,
        unexplained_reasons=unexplained_reasons,
        scope="hsreplay_meta_archetype_rows",
    )


async def fetch_hsreplay_meta_archetypes(source: Source) -> dict[str, Any]:
    api_url = _meta_archetypes_url(source)
    firecrawl_page: dict[str, Any] = {}
    try:
        scraped = await scrape_source(source)
        firecrawl_page = {
            "ok": True,
            "final_url": scraped.final_url,
            "status_code": scraped.status_code,
            "content_length": scraped.content_length,
            "markdown_length": len(scraped.markdown),
            "credits_used": scraped.metadata.get("creditsUsed"),
        }
    except Exception as exc:
        firecrawl_page = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    payload = await fetch_hsreplay_json(
        api_url,
        source_id=source.id,
        cache_key=f"meta-archetypes:{source.fragment or source.id}",
    )
    archetype_names = await _archetype_name_map(source.id)
    classes = normalize_meta_archetypes(payload, archetype_names)
    total_archetypes = sum(len(item.get("archetypes") or []) for item in classes)
    return {
        "type": "hsreplay_meta_archetypes",
        "classes": classes,
        "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
        "row_retrieval": _meta_archetype_row_evidence(
            payload,
            normalized_rows=total_archetypes,
        ),
        "total_classes": len(classes),
        "total_archetypes": total_archetypes,
        "filters": {
            "game_type": _query_param(source, "gameType") or "RANKED_STANDARD",
            "rank_range": _query_param(source, "rankRange") or "LEGEND",
            "region": _query_param(source, "region") or "REGION_EU",
            "time_range": _query_param(source, "timeFrame") or "LAST_1_DAY",
        },
        "as_of": payload.get("as_of"),
        "source": {
            "key": "hsreplay",
            "url": source.url,
            "api_url": api_url,
            "backend": "firecrawl+hsreplay_meta_api" if firecrawl_page.get("ok") else "hsreplay_meta_api",
            "firecrawl_page": firecrawl_page,
            "classes": len(classes),
            "archetypes": total_archetypes,
        },
    }
