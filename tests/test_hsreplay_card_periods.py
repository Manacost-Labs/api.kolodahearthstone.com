from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from app.hsreplay_card_periods import (
    CardPeriodFetch,
    HSREPLAY_CARD_PERIOD_SOURCE_SPECS,
    HSREPLAY_CARD_PERIOD_SOURCE_IDS,
    fetch_hsreplay_card_period_json,
)
from app.hsreplay_cards_api import fetch_hsreplay_ranked_cards
from app.source_contracts import get_contract
from app.source_tiers import SourceTier, tier_for
from app.sources import SOURCE_BY_ID, Source


class _Response:
    def __init__(self, payload: object):
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


def test_period_sources_cover_standard_and_wild() -> None:
    expected = {
        f"hsreplay_cards_{wild}{rank}_{period}"
        for wild in ("", "wild_")
        for rank in ("legend", "diamond_4_1", "diamond", "platinum")
        for period in ("1d", "3d", "7d", "14d", "patch")
    }
    assert expected == set(HSREPLAY_CARD_PERIOD_SOURCE_IDS)
    assert expected.issubset(SOURCE_BY_ID)
    for source_id in expected:
        source = SOURCE_BY_ID[source_id]
        expected_rank_range = next(
            spec.rank_range
            for spec in HSREPLAY_CARD_PERIOD_SOURCE_SPECS
            if spec.source_id == source_id
        )
        assert f"rankRange={expected_rank_range}" in source.fragment
        assert "timeRange=" in source.fragment
        assert ("gameType=RANKED_WILD" in source.fragment) == ("wild_" in source_id)


def test_period_sources_have_quality_contracts_and_refresh_tiers() -> None:
    for source_id in HSREPLAY_CARD_PERIOD_SOURCE_IDS:
        contract = get_contract(source_id)
        assert contract is not None
        assert contract.structured_type == "card_stats"
        assert contract.allow_browser_fallback is False
        assert tier_for(source_id) is SourceTier.MEDIUM_API


def test_firecrawl_reads_raw_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-redacted")
    monkeypatch.delenv("HS_SCRAPE_DO_TOKEN", raising=False)
    card_list = {"series": {"data": [{"dbfId": 1, "includedPopularity": 2.5}]}}
    envelope = {"success": True, "data": {"rawHtml": json.dumps(card_list)}}
    with patch.object(urllib.request, "urlopen", return_value=_Response(envelope)):
        result = asyncio.run(fetch_hsreplay_card_period_json("https://hsreplay.net/analytics/query/card_list/"))
    assert result.backend == "firecrawl"
    assert result.payload == card_list
    assert result.attempts == ({"backend": "firecrawl", "state": "ok"},)


def test_scrape_do_is_used_after_firecrawl_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-redacted")
    monkeypatch.setenv("HS_SCRAPE_DO_TOKEN", "scrape-test-redacted")
    card_list = {"series": {"data": [{"dbfId": 2, "includedPopularity": 3.5}]}}
    calls = 0

    def urlopen(_request: urllib.request.Request, **_kwargs: object) -> _Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("firecrawl unavailable")
        return _Response(card_list)

    with patch.object(urllib.request, "urlopen", side_effect=urlopen):
        result = asyncio.run(fetch_hsreplay_card_period_json("https://hsreplay.net/analytics/query/card_list/"))
    assert result.backend == "scrape_do"
    assert result.payload == card_list
    assert result.attempts == (
        {"backend": "firecrawl", "state": "failed", "error_type": "URLError"},
        {"backend": "scrape_do", "state": "ok"},
    )


def test_provider_errors_never_expose_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-private-value")
    monkeypatch.setenv("HS_SCRAPE_DO_TOKEN", "scrape-private-value")

    def urlopen(request: urllib.request.Request, **_kwargs: object) -> _Response:
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "forbidden",
            {},
            io.BytesIO(b"blocked"),
        )

    with patch.object(urllib.request, "urlopen", side_effect=urlopen):
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(fetch_hsreplay_card_period_json("https://hsreplay.net/analytics/query/card_list/"))
    message = str(caught.value)
    assert "fc-private-value" not in message
    assert "scrape-private-value" not in message
    assert "firecrawl=failed" in message
    assert "scrape_do=failed" in message


def test_ranked_cards_uses_proxy_fallback_after_direct_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import hsreplay_card_periods, hsreplay_cards_api, hsreplay_client

    source = Source(
        "hsreplay_cards_legend_3d",
        "https://hsreplay.net/cards/#rankRange=LEGEND&timeRange=LAST_3_DAYS&gameType=RANKED_STANDARD",
        "hsreplay",
        "ranked",
    )

    async def direct_failure(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("blocked")

    async def fallback_success(_url: str) -> CardPeriodFetch:
        return CardPeriodFetch(
            payload={"series": {"data": []}},
            backend="scrape_do",
            attempts=({"backend": "scrape_do", "state": "ok"},),
        )

    cards = [
        {"id": f"CARD_{index}", "dbfId": index, "deck_popularity": "1.0%"}
        for index in range(1, 31)
    ]
    monkeypatch.setattr(hsreplay_client, "fetch_hsreplay_json", direct_failure)
    monkeypatch.setattr(hsreplay_card_periods, "fetch_hsreplay_card_period_json", fallback_success)
    monkeypatch.setattr(hsreplay_cards_api, "parse_cards_from_api_payloads", lambda *_args, **_kwargs: cards)

    result = asyncio.run(fetch_hsreplay_ranked_cards(source))
    assert result["time_range"] == "LAST_3_DAYS"
    assert result["source"]["backend"] == "hsreplay_cards_api+scrape_do"
    assert result["source"]["diagnostics"]["proxy_attempts"][0] == {
        "backend": "direct",
        "state": "failed",
        "error_type": "RuntimeError",
    }
