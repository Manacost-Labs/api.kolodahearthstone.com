from __future__ import annotations

import json

from web_scraper import ContentKind, validate_response
from web_scraper.fetchers import RawResponse

from app.parsesunix_contracts import (
    STRICT_HSREPLAY_TRINKET_SOURCE_IDS,
    hsreplay_json_response_contract,
    page_response_contract,
)
from app.sources import SOURCE_BY_ID
from app.trinket_slices import (
    LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
    TRINKET_SLICE_SOURCE_IDS,
)


def _response(body: str, *, content_type: str) -> RawResponse:
    return RawResponse(
        requested_url="https://example.com/data",
        final_url="https://example.com/data",
        status=200,
        headers={"Content-Type": content_type},
        body=body.encode(),
    )


def test_hsguru_page_contracts_are_source_specific() -> None:
    meta = page_response_contract(SOURCE_BY_ID["hsguru_meta_standard_legend"])
    matchups = page_response_contract(SOURCE_BY_ID["hsguru_matchups_legend"])
    streamer = page_response_contract(SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"])

    assert meta.expected_kind is ContentKind.HTML
    assert meta.canaries == ("/archetype/",)
    assert matchups.canaries == ("matchup",)
    assert streamer.canaries == ("streamer",)
    assert meta.min_body_bytes == 25_000
    assert streamer.min_body_bytes == 8_000


def test_hsreplay_trinkets_contract_requires_real_metric_shape() -> None:
    contract = hsreplay_json_response_contract(
        "https://hsreplay.net/api/v1/battlegrounds/trinkets/?format=json"
    )
    body = json.dumps(
        [
            {
                "trinket_dbf_id": 123,
                "group": "lesser",
                "pick_rate": 0.2,
                "avg_final_placement": 4.1,
            }
        ]
    ).ljust(120)

    accepted = validate_response(
        _response(body, content_type="application/json"),
        contract,
    )
    rejected = validate_response(
        _response(
            json.dumps([{"trinket_dbf_id": 123}]).ljust(120),
            content_type="application/json",
        ),
        contract,
    )

    assert accepted.transport_validated is True
    assert rejected.transport_validated is False


def test_strict_hsreplay_rollout_starts_with_all_eleven_trinket_sources() -> None:
    assert STRICT_HSREPLAY_TRINKET_SOURCE_IDS == frozenset(
        (*LEGACY_DEFAULT_TRINKET_SOURCE_IDS, *TRINKET_SLICE_SOURCE_IDS)
    )
    assert len(STRICT_HSREPLAY_TRINKET_SOURCE_IDS) == 11


def test_hsreplay_analytics_contract_requires_series_data() -> None:
    contract = hsreplay_json_response_contract(
        "https://hsreplay.net/analytics/query/card_list/?rank=legend"
    )
    body = json.dumps({"series": {"data": {"ALL": [{"dbfId": 1}]}}}).ljust(120)

    result = validate_response(
        _response(body, content_type="application/json"),
        contract,
    )

    assert result.transport_validated is True
