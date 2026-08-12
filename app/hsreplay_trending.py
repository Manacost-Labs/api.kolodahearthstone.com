from __future__ import annotations

import logging
import math
import re
from typing import Any

from .hsreplay_client import fetch_hsreplay_json
from .hsreplay_meta_api import CLASS_RU_NAMES, _archetype_name_map
from .sources import Source

logger = logging.getLogger(__name__)

TRENDING_DECKS_API_URL = (
    "https://hsreplay.net/analytics/query/trending_decks_by_popularity/"
)
MIN_EXPECTED_TRENDING_CLASSES = 10
MAX_EXPECTED_TRENDING_CLASSES = len(CLASS_RU_NAMES)
_SHORTID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_candidate(
    class_key: str,
    row: dict[str, Any],
    archetype_names: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    shortid = str(row.get("shortid") or "").strip()
    archetype_id = _positive_int(row.get("archetype_id"))
    popularity_delta = _finite_number(row.get("popularity_delta"))
    total_games = _positive_int(row.get("total_games"))
    win_rate = _finite_number(row.get("win_rate"))
    duration_seconds = _finite_number(row.get("avg_game_length_seconds"))
    turns = _finite_number(row.get("avg_num_player_turns"))
    if (
        not _SHORTID_RE.fullmatch(shortid)
        or archetype_id is None
        or popularity_delta is None
        or total_games is None
        or win_rate is None
        or not 0 <= win_rate <= 100
    ):
        return None

    archetype = archetype_names.get(archetype_id) or {}
    archetype_class = str(archetype.get("player_class_name") or "").upper()
    if archetype_class and archetype_class != class_key:
        return None
    name = str(archetype.get("name") or "").strip() or f"Архетип #{archetype_id}"

    return {
        # Backwards-compatible fields produced by the HTML parser.
        "name": name,
        "winrate": f"{win_rate:.1f}%",
        "games": str(total_games),
        "duration": (
            f"{duration_seconds / 60:.1f} min"
            if duration_seconds is not None and duration_seconds > 0
            else None
        ),
        "deck_url": f"/decks/{shortid}/#gameType=RANKED_STANDARD",
        "hsreplay_deck_id": shortid,
        # Stable machine-readable metrics for downstream validation/analysis.
        "class": class_key,
        "class_name": CLASS_RU_NAMES[class_key],
        "archetype_id": archetype_id,
        "archetype_url": archetype.get("url") or None,
        "popularity_delta": popularity_delta,
        "total_games": total_games,
        "win_rate": win_rate,
        "avg_game_length_seconds": duration_seconds,
        "avg_num_player_turns": turns,
    }


def normalize_trending_decks(
    payload: dict[str, Any],
    archetype_names: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select exactly one highest-growth valid deck for every available class."""

    series = payload.get("series")
    data = series.get("data") if isinstance(series, dict) else None
    if not isinstance(data, dict):
        return []

    selected: list[dict[str, Any]] = []
    for class_key in sorted(CLASS_RU_NAMES):
        raw_rows = data.get(class_key)
        if not isinstance(raw_rows, list):
            continue
        candidates = [
            normalized
            for row in raw_rows
            if isinstance(row, dict)
            and (
                normalized := _normalize_candidate(
                    class_key,
                    row,
                    archetype_names,
                )
            )
            is not None
        ]
        if not candidates:
            continue
        # popularity_delta is authoritative. The remaining keys only make
        # ties deterministic across Python/process/upstream ordering changes.
        candidates.sort(
            key=lambda item: (
                -float(item["popularity_delta"]),
                -int(item["total_games"]),
                -float(item["win_rate"]),
                str(item["hsreplay_deck_id"]),
            )
        )
        selected.append(candidates[0])

    shortids = [str(deck["hsreplay_deck_id"]) for deck in selected]
    if len(shortids) != len(set(shortids)):
        raise ValueError("HSReplay trending payload reused one deck across classes")
    return selected


async def fetch_hsreplay_trending(source: Source) -> dict[str, Any]:
    payload = await fetch_hsreplay_json(
        TRENDING_DECKS_API_URL,
        source_id=source.id,
        cache_key="hsreplay:trending-decks:popularity",
    )

    try:
        archetype_names = await _archetype_name_map(source.id)
    except Exception as exc:  # noqa: BLE001 - optional enrichment is fail-open
        # Names are enrichment only. The deterministic archetype-id fallback
        # keeps a complete, truthful snapshot publishable during a transient
        # dictionary outage.
        logger.warning(
            "HSReplay archetype dictionary unavailable for trending decks (%s)",
            type(exc).__name__,
        )
        archetype_names = {}

    decks = normalize_trending_decks(payload, archetype_names)
    if not MIN_EXPECTED_TRENDING_CLASSES <= len(decks) <= MAX_EXPECTED_TRENDING_CLASSES:
        raise RuntimeError(
            "HSReplay trending API returned an incomplete class set "
            f"({len(decks)}; expected {MIN_EXPECTED_TRENDING_CLASSES}-"
            f"{MAX_EXPECTED_TRENDING_CLASSES})"
        )

    candidate_count = sum(
        len(rows)
        for rows in (((payload.get("series") or {}).get("data") or {}).values())
        if isinstance(rows, list)
    )
    resolved_names = sum(
        1
        for deck in decks
        if deck["archetype_id"] in archetype_names
        and archetype_names[deck["archetype_id"]].get("name")
    )
    return {
        "type": "trending_decks",
        "decks": decks,
        "total_decks": len(decks),
        "as_of": payload.get("as_of"),
        "source": {
            "key": "hsreplay",
            "url": source.url,
            "api_url": TRENDING_DECKS_API_URL,
            "backend": "hsreplay_trending_api",
            "selection": "highest_popularity_delta_per_class",
            "candidate_decks": candidate_count,
            "classes": len(decks),
            "archetype_names_resolved": resolved_names,
        },
    }
