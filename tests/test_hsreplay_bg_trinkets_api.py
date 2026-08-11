from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.api_only_sources import blocks_browser_fallback
from app.fetcher import _fetch_hsreplay_api_source, _preserve_cached_ok_status
from app.hsreplay_bg_trinkets import fetch_battlegrounds_trinkets
from app.publish_gate import validate_candidate_for_publish
from app.source_contracts import HSREPLAY_JSON_CHANNELS, get_contract
from app.source_tiers import BROWSER_PROTECTED_IDS, MEDIUM_API_IDS, SourceTier, tier_for
from app.sources import SOURCE_BY_ID
from app.trinket_slices import (
    LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
    TRINKET_SLICE_SOURCE_IDS,
)


def _raw_trinket(dbf_id: int, group: str) -> dict[str, object]:
    return {
        "trinket_dbf_id": dbf_id,
        "extra_data": None,
        "pick_rate": 12.5,
        "avg_final_placement": 3.75,
        "final_placement_distribution": [20, 15, 15, 15, 10, 10, 10, 5],
        "tier": "a",
        "group": group,
    }


def _card(dbf_id: int) -> dict[str, object]:
    return {
        "dbfId": dbf_id,
        "id": f"BG_TEST_{dbf_id}",
        "name": f"Stable Trinket {dbf_id}",
        "text": "At the start of combat, give your minions +2/+2 permanently.",
        "cost": 2,
    }


def _contract_rows(count: int) -> list[dict[str, str]]:
    return [
        {
            "name": f"Stable Trinket {index}",
            "trinket_id": f"BG_TEST_{index}",
            "description": "At the start of combat, give your minions +2/+2 permanently.",
            "pick_rate": "12.5%",
            "avg_placement": "3.75",
            "trinket_tier": "Lesser" if index % 2 == 0 else "Greater",
        }
        for index in range(count)
    ]


def test_legacy_trinket_adapter_filters_group_and_reuses_slice_cache_key() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_trinkets_lesser"]
    payload = {
        "data": [
            _raw_trinket(1001, "lesser"),
            _raw_trinket(1002, "greater"),
        ]
    }
    fetch_json = AsyncMock(return_value=payload)

    async def run() -> dict[str, object]:
        with (
            patch("app.hsreplay_bg_trinkets.fetch_hsreplay_json", fetch_json),
            patch(
                "app.hsreplay_extract.cards_by_dbfid",
                return_value={1001: _card(1001), 1002: _card(1002)},
            ),
            patch("app.structured.cards_by_id", return_value={}),
        ):
            return await fetch_battlegrounds_trinkets(source)

    structured = asyncio.run(run())

    assert structured["type"] == "bg_trinkets"
    assert structured["parser_level"] == "primary"
    assert structured["dropped_rows"] == 0
    assert [row["trinket_id"] for row in structured["trinkets"]] == ["BG_TEST_1001"]
    assert structured["source"]["mmr_percentile"] == "TOP_1_PERCENT"
    assert structured["source"]["time_range"] == "LAST_7_DAYS"
    fetch_json.assert_awaited_once_with(
        source.fetch_url,
        source_id=source.id,
        cache_key="bg:trinkets:TOP_1_PERCENT:LAST_7_DAYS",
    )


def test_combined_trinket_slice_keeps_lesser_and_greater_rows() -> None:
    source = SOURCE_BY_ID[
        "hsreplay_battlegrounds_trinkets_top_20_percent_current_battlegrounds_patch"
    ]
    lesser_rows = [_raw_trinket(2_000 + index, "lesser") for index in range(130)]
    greater_rows = [_raw_trinket(3_000 + index, "greater") for index in range(142)]
    payload = {"data": [*lesser_rows, *greater_rows]}
    cards = {
        int(row["trinket_dbf_id"]): _card(int(row["trinket_dbf_id"]))
        for row in payload["data"]
    }

    async def run() -> dict[str, object]:
        with (
            patch(
                "app.hsreplay_bg_trinkets.fetch_hsreplay_json",
                new=AsyncMock(return_value=payload),
            ),
            patch(
                "app.hsreplay_extract.cards_by_dbfid",
                return_value=cards,
            ),
            patch("app.structured.cards_by_id", return_value={}),
        ):
            return await fetch_battlegrounds_trinkets(source)

    structured = asyncio.run(run())

    assert {row["trinket_tier"] for row in structured["trinkets"]} == {
        "Lesser",
        "Greater",
    }
    assert structured["active_trinkets"] == 272
    assert structured["dropped_rows"] == 0
    assert structured["source"]["mmr_percentile"] == "TOP_20_PERCENT"
    assert structured["source"]["time_range"] == "CURRENT_BATTLEGROUNDS_PATCH"


def test_all_trinket_sources_are_api_only_medium_sources_with_strict_contracts() -> None:
    all_ids = set(LEGACY_DEFAULT_TRINKET_SOURCE_IDS) | set(TRINKET_SLICE_SOURCE_IDS)

    assert len(all_ids) == 11
    for source_id in all_ids:
        contract = get_contract(source_id)
        assert contract is not None
        assert source_id in MEDIUM_API_IDS
        assert source_id not in BROWSER_PROTECTED_IDS
        assert tier_for(source_id) is SourceTier.MEDIUM_API
        assert contract.preferred_channels == HSREPLAY_JSON_CHANNELS
        assert contract.allow_browser_fallback is False
        assert contract.fallback_policy == "api_only"
        assert contract.min_rows == (
            80 if source_id in LEGACY_DEFAULT_TRINKET_SOURCE_IDS else 160
        )
        assert blocks_browser_fallback(source_id)


def test_trinket_publish_gate_keeps_legacy_and_combined_row_floors() -> None:
    for source_id, minimum in (
        ("hsreplay_battlegrounds_trinkets_lesser", 80),
        (
            "hsreplay_battlegrounds_trinkets_top_20_percent_current_battlegrounds_patch",
            160,
        ),
    ):
        source = SOURCE_BY_ID[source_id]
        accepted = validate_candidate_for_publish(
            source,
            {
                "structured": {
                    "type": "bg_trinkets",
                    "trinkets": _contract_rows(minimum),
                    "parser_level": "primary",
                }
            },
            backend="hsreplay_trinkets_api",
        )
        rejected = validate_candidate_for_publish(
            source,
            {
                "structured": {
                    "type": "bg_trinkets",
                    "trinkets": _contract_rows(minimum - 1),
                    "parser_level": "primary",
                }
            },
            backend="hsreplay_trinkets_api",
        )

        assert accepted.ok, accepted.reason
        assert not rejected.ok
        assert "too few rows" in rejected.reason


def test_combined_trinket_publish_gate_rejects_one_sided_payload() -> None:
    source = SOURCE_BY_ID[
        "hsreplay_battlegrounds_trinkets_top_20_percent_current_battlegrounds_patch"
    ]
    rows = _contract_rows(160)
    for row in rows:
        row["trinket_tier"] = "Lesser"

    report = validate_candidate_for_publish(
        source,
        {
            "structured": {
                "type": "bg_trinkets",
                "trinkets": rows,
                "parser_level": "primary",
            }
        },
        backend="hsreplay_trinkets_api",
    )

    assert not report.ok
    assert "combined bg trinkets slice is incomplete" in report.reason


def test_legacy_published_trinket_snapshot_remains_valid_lkg() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_trinkets_lesser"]
    cached = {
        "fetched_at": "2026-08-10T00:00:00+00:00",
        "backend": "scrape_do",
        "data": {
            "title": "Battlegrounds lesser trinkets",
            "structured": {
                "type": "bg_trinkets",
                "trinkets": _contract_rows(80),
                "parser_level": "primary",
            },
        },
    }
    failed = {
        "state": "fetch_error",
        "fetched_at": "2026-08-11T00:00:00+00:00",
        "detail": "temporary upstream failure",
    }

    with (
        patch(
            "app.parser_control.load_resolved_public_dataset",
            return_value=cached,
        ),
        patch("app.fetcher.save_status"),
        patch("app.fetcher.log_action"),
    ):
        status = _preserve_cached_ok_status(source, failed)

    assert status is not None
    assert status["state"] == "ok"
    assert status["serving_cached_dataset"] is True
    assert status["cached_backend_policy_grandfathered"] is True


def test_fetcher_preserves_adapter_and_scrape_do_transport_provenance() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_trinkets_lesser"]
    structured = {
        "type": "bg_trinkets",
        "trinkets": [],
        "parser_level": "primary",
        "source": {"backend": "hsreplay_trinkets_api"},
    }

    async def run() -> dict[str, object] | None:
        with (
            patch(
                "app.hsreplay_bg_trinkets.fetch_battlegrounds_trinkets",
                new=AsyncMock(return_value=structured),
            ),
            patch(
                "app.hsreplay_client.consume_hsreplay_json_transport_backend",
                return_value="scrape_do",
            ),
        ):
            return await _fetch_hsreplay_api_source(source)

    parsed = asyncio.run(run())

    assert parsed is not None
    assert parsed["_backend"] == "hsreplay_trinkets_api"
    assert parsed["_transport_backend"] == "scrape_do"
    assert parsed["structured"]["source"]["backend"] == "hsreplay_trinkets_api"
