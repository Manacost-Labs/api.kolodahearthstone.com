from __future__ import annotations

import asyncio
import gzip
import json
import math
from typing import Any

import httpx

from .completeness import COMPLETENESS_SCHEMA_VERSION, row_retrieval_evidence
from .config import source_operationally_enabled
from .firestone_comps import _get_static_json
from .sources import Source

# Firestone's own client loads these two public static snapshots together.
# Source: https://github.com/Zero-to-Heroes/firestone/blob/master/libs/constructed/common/src/lib/services/constructed-meta-decks-state-builder.service.ts
FIRESTONE_STANDARD_DECKS_URL = (
    "https://static.zerotoheroes.com/api/constructed/stats/decks/"
    "standard/legend/last-patch/overview-from-hourly.gz.json"
)
FIRESTONE_STANDARD_ARCHETYPES_URL = (
    "https://static.zerotoheroes.com/api/constructed/stats/archetypes/"
    "standard/legend/last-patch/overview-from-hourly.gz.json"
)


def _decode_static_payload(response: httpx.Response, *, label: str) -> dict[str, Any]:
    content = response.content
    if content.startswith(b"\x1f\x8b"):
        try:
            content = gzip.decompress(content)
        except OSError as exc:
            raise ValueError(
                f"Firestone {label} payload has invalid gzip data"
            ) from exc
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Firestone {label} payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"Firestone {label} payload must be a JSON object")
    return payload


def _object_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise TypeError(f"Firestone payload field {key} must be a list")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Firestone payload field {key} contains a non-object row")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _required_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty card ids")
    return [item.strip() for item in value]


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _rate(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and 0.0 <= parsed <= 1.0 else None


def _require_unique_identity(
    rows: list[dict[str, Any]],
    *,
    field: str,
    collection: str,
) -> None:
    identities = [row.get(field) for row in rows]
    if any(value in (None, "") for value in identities):
        raise ValueError(f"Firestone {collection} contains a missing {field}")
    if len(set(identities)) != len(identities):
        raise ValueError(f"Firestone {collection} contains duplicate {field}")


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_points": _non_negative_int(payload.get("dataPoints")),
        "last_updated": str(payload.get("lastUpdated") or "").strip() or None,
        "rank_bracket": str(payload.get("rankBracket") or "").strip() or None,
        "time_period": str(payload.get("timePeriod") or "").strip() or None,
        "format": str(payload.get("format") or "").strip() or None,
    }


def _normalize_deck(row: dict[str, Any]) -> dict[str, Any]:
    variations = row.get("cardVariations")
    variations = variations if isinstance(variations, dict) else {}
    decklist = str(row.get("decklist") or "").strip() or None
    return {
        "decklist": decklist,
        "deck_code": decklist,
        "archetype_id": _non_negative_int(row.get("archetypeId")),
        "archetype_name": str(row.get("archetypeName") or "").strip() or None,
        "player_class": str(row.get("playerClass") or "").strip() or None,
        "games": _non_negative_int(row.get("totalGames")),
        "wins": _non_negative_int(row.get("totalWins")),
        "winrate": _rate(row.get("winrate")),
        "core_cards": _required_string_list(
            row.get("archetypeCoreCards"),
            field="archetypeCoreCards",
        ),
        "card_variations": {
            "added": _string_list(variations.get("added")),
            "removed": _string_list(variations.get("removed")),
        },
        "hero_card_ids": _string_list(row.get("heroCardIds")),
        "format": str(row.get("format") or "").strip() or None,
        "rank_bracket": str(row.get("rankBracket") or "").strip() or None,
        "time_period": str(row.get("timePeriod") or "").strip() or None,
        "last_updated": str(row.get("lastUpdate") or "").strip() or None,
    }


def _normalize_archetype(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "archetype_id": _non_negative_int(row.get("id")),
        "archetype_name": str(row.get("name") or "").strip() or None,
        "player_class": str(row.get("heroCardClass") or "").strip() or None,
        "games": _non_negative_int(row.get("totalGames")),
        "wins": _non_negative_int(row.get("totalWins")),
        "winrate": _rate(row.get("winrate")),
        "core_cards": _required_string_list(
            row.get("coreCards"),
            field="coreCards",
        ),
        "hero_card_ids": _string_list(row.get("heroCardIds")),
        "format": str(row.get("format") or "").strip() or None,
    }


def _annotate_core_card_availability(
    decks: list[dict[str, Any]],
    archetypes: list[dict[str, Any]],
) -> None:
    observed_archetype_ids = {
        row.get("archetype_id")
        for row in decks
        if row.get("archetype_id") is not None
    }
    for row in decks:
        has_core_cards = bool(row.get("core_cards"))
        row["field_availability"] = {
            "core_cards": {
                "available": has_core_cards,
                "reason": (
                    None
                    if has_core_cards
                    else "empty_core_cards_without_deterministic_explanation"
                ),
            }
        }
    for row in archetypes:
        has_core_cards = bool(row.get("core_cards"))
        no_observed_cluster = row.get("archetype_id") not in observed_archetype_ids
        archetype_name = str(row.get("archetype_name") or "").strip().casefold()
        player_class = str(row.get("player_class") or "").strip().casefold()
        generic_class_bucket = bool(archetype_name) and archetype_name == player_class
        explained_empty = (
            not has_core_cards and generic_class_bucket and no_observed_cluster
        )
        row["field_availability"] = {
            "core_cards": {
                "available": has_core_cards,
                "reason": (
                    None
                    if has_core_cards
                    else (
                        "generic_class_bucket_without_observed_deck_cluster"
                        if explained_empty
                        else "empty_core_cards_without_deterministic_explanation"
                    )
                ),
            }
        }


async def fetch_firestone_standard(source: Source) -> dict[str, Any]:
    """Fetch the Standard Legend last-patch deck and archetype overviews."""
    if not source_operationally_enabled(source.id):
        raise PermissionError(
            "Firestone Standard requires written authorization and explicit "
            "HS_FIRESTONE_STANDARD_AUTHORIZED=true opt-in"
        )
    headers = {
        "accept": "application/json,text/plain,*/*",
        "user-agent": "KolodaHS MetaCrawler/1.0 (+https://kolodahs.ru)",
    }
    decks_response, archetypes_response = await asyncio.gather(
        _get_static_json(
            FIRESTONE_STANDARD_DECKS_URL,
            headers=headers,
            source_id=source.id,
        ),
        _get_static_json(
            FIRESTONE_STANDARD_ARCHETYPES_URL,
            headers=headers,
            source_id=source.id,
        ),
    )
    decks_payload = _decode_static_payload(decks_response, label="decks")
    archetypes_payload = _decode_static_payload(
        archetypes_response,
        label="archetypes",
    )
    raw_decks = _object_rows(decks_payload, "deckStats")
    raw_archetypes = _object_rows(archetypes_payload, "archetypeStats")
    decks = [_normalize_deck(row) for row in raw_decks]
    archetypes = [
        _normalize_archetype(row)
        for row in raw_archetypes
    ]
    _require_unique_identity(
        decks,
        field="decklist",
        collection="deckStats",
    )
    _require_unique_identity(
        archetypes,
        field="archetype_id",
        collection="archetypeStats",
    )
    _annotate_core_card_availability(decks, archetypes)
    return {
        "type": "firestone_standard",
        "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
        "row_retrieval": row_retrieval_evidence(
            raw_rows=len(raw_decks) + len(raw_archetypes),
            eligible_rows=len(raw_decks) + len(raw_archetypes),
            normalized_rows=len(decks) + len(archetypes),
            scope="decks+archetypes",
        ),
        "format": "standard",
        "rank_bracket": "legend",
        "time_period": "last-patch",
        "metadata": {
            "decks": _metadata(decks_payload),
            "archetypes": _metadata(archetypes_payload),
        },
        "decks": decks,
        "archetypes": archetypes,
        "total_decks": len(decks),
        "total_archetypes": len(archetypes),
        "_fetch_backend": "proxyless_direct",
    }
