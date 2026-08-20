from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.hsreplay_meta_api import fetch_hsreplay_meta_archetypes
from app.source_contracts import contract_quality_report
from app.sources import SOURCE_BY_ID


def _row(index: int) -> dict[str, object]:
    return {
        "archetype_id": 10_000 + index,
        "total_games": 100 + index,
        "pct_of_class": 4.0,
        "pct_of_total": 1.0,
        "win_rate": 51.0,
    }


def _fetch(payload: dict[str, object]) -> dict[str, object]:
    source = SOURCE_BY_ID["hsreplay_meta_archetypes_legend_eu_1d"]
    with (
        patch(
            "app.hsreplay_meta_api.scrape_source",
            new=AsyncMock(side_effect=RuntimeError("optional page unavailable")),
        ),
        patch(
            "app.hsreplay_meta_api.fetch_hsreplay_json",
            new=AsyncMock(return_value=payload),
        ),
        patch(
            "app.hsreplay_meta_api._archetype_name_map",
            new=AsyncMock(return_value={}),
        ),
    ):
        return asyncio.run(fetch_hsreplay_meta_archetypes(source))


def test_hsreplay_meta_proves_row_and_identity_completeness() -> None:
    structured = _fetch(
        {"series": {"data": {"DRUID": [_row(index) for index in range(20)]}}}
    )
    report = contract_quality_report(
        "hsreplay_meta_archetypes_legend_eu_1d",
        structured,
    )

    assert structured["completeness_schema_version"] == 1
    assert structured["row_retrieval"] == {
        "raw_rows": 20,
        "eligible_rows": 20,
        "normalized_rows": 20,
        "explained_drops": 0,
        "unexplained_drops": 0,
        "drop_reasons": {"explained": {}, "unexplained": {}},
        "scope": "hsreplay_meta_archetype_rows",
    }
    assert report["ok"], report["warnings"]
    assert report["retrieval_complete"] is True
    assert report["identity_checks"]["archetypes"]["complete"] is True


def test_hsreplay_meta_fails_closed_on_malformed_upstream_row() -> None:
    rows: list[object] = [_row(index) for index in range(20)]
    rows.append("broken")
    structured = _fetch({"series": {"data": {"DRUID": rows}}})
    report = contract_quality_report(
        "hsreplay_meta_archetypes_legend_eu_1d",
        structured,
    )

    assert structured["row_retrieval"]["raw_rows"] == 21
    assert structured["row_retrieval"]["normalized_rows"] == 20
    assert structured["row_retrieval"]["drop_reasons"]["unexplained"] == {
        "invalid_archetype_row": 1
    }
    assert report["retrieval_complete"] is False
    assert "row_retrieval has unexplained dropped rows" in report["warnings"]
