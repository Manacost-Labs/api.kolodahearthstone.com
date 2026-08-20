from __future__ import annotations

from typing import Any

from .completeness import COMPLETENESS_SCHEMA_VERSION, row_retrieval_evidence
from .hsreplay_client import fetch_hsreplay_json
from .hsreplay_extract import parse_bg_trinkets_api_payload
from .sources import Source
from .trinket_slices import (
    DEFAULT_TRINKET_MMR,
    DEFAULT_TRINKET_TIME_RANGE,
    LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
    TRINKET_SLICE_BY_SOURCE_ID,
)


def _slice_parameters(source_id: str) -> tuple[str, str, tuple[str, ...]]:
    if source_id == LEGACY_DEFAULT_TRINKET_SOURCE_IDS[0]:
        return DEFAULT_TRINKET_MMR, DEFAULT_TRINKET_TIME_RANGE, ("Lesser",)
    if source_id == LEGACY_DEFAULT_TRINKET_SOURCE_IDS[1]:
        return DEFAULT_TRINKET_MMR, DEFAULT_TRINKET_TIME_RANGE, ("Greater",)
    if source_id in TRINKET_SLICE_BY_SOURCE_ID:
        mmr_percentile, time_range = TRINKET_SLICE_BY_SOURCE_ID[source_id]
        return mmr_percentile, time_range, ("Lesser", "Greater")
    raise ValueError(f"Unsupported HSReplay trinket source: {source_id}")


def _payload_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


async def fetch_battlegrounds_trinkets(source: Source) -> dict[str, Any]:
    """Fetch and normalize one registered HSReplay trinket statistics slice."""

    mmr_percentile, time_range, trinket_types = _slice_parameters(source.id)
    payload = await fetch_hsreplay_json(
        source.fetch_url,
        source_id=source.id,
        cache_key=f"bg:trinkets:{mmr_percentile}:{time_range}",
    )
    payload_data = payload.get("data")
    raw_payload_rows = len(payload_data) if isinstance(payload_data, list) else 0
    raw_rows = _payload_rows(payload)
    trinkets = [
        row
        for trinket_type in trinket_types
        for row in parse_bg_trinkets_api_payload(
            raw_rows,
            trinket_type=trinket_type,
        )
    ]
    selected_groups = {trinket_type.lower() for trinket_type in trinket_types}
    selected_raw_rows = sum(
        1
        for row in raw_rows
        if str(row.get("group") or "").strip().lower() in selected_groups
    )
    unselected_rows = len(raw_rows) - selected_raw_rows
    explained_unselected_rows = (
        unselected_rows
        if source.id in LEGACY_DEFAULT_TRINKET_SOURCE_IDS
        else 0
    )
    unexplained_reasons = {
        reason: count
        for reason, count in (
            ("non_object_payload_row", raw_payload_rows - len(raw_rows)),
            (
                "unknown_trinket_tier",
                unselected_rows - explained_unselected_rows,
            ),
            ("normalization_loss", selected_raw_rows - len(trinkets)),
        )
        if count
    }
    return {
        "type": "bg_trinkets",
        "trinkets": trinkets,
        "active_trinkets": len(trinkets),
        "parser_level": "primary",
        "dropped_rows": max(0, selected_raw_rows - len(trinkets)),
        "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
        "row_retrieval": row_retrieval_evidence(
            raw_rows=raw_payload_rows,
            eligible_rows=selected_raw_rows,
            normalized_rows=len(trinkets),
            explained_reasons=(
                {"unselected_trinket_tier": explained_unselected_rows}
                if explained_unselected_rows
                else None
            ),
            unexplained_reasons=unexplained_reasons,
            scope=f"hsreplay_trinkets:{mmr_percentile}:{time_range}",
        ),
        "source": {
            "key": "hsreplay",
            "url": source.url,
            "api_url": source.fetch_url,
            "backend": "hsreplay_trinkets_api",
            "mmr_percentile": mmr_percentile,
            "time_range": time_range,
            "rows": len(trinkets),
        },
    }
