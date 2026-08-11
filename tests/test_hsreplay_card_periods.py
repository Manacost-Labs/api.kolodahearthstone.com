from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from app import firecrawl_backend
from app.firecrawl_backend import FirecrawlScrape
from app.hsreplay_card_periods import (
    HSREPLAY_CARD_PERIOD_SOURCE_IDS,
    HSREPLAY_CARD_PERIOD_SOURCE_SPECS,
    CardPeriodFetch,
    _accept_card_period_json,
    _scrape_card_period_page,
    fetch_hsreplay_card_period_json,
)
from app.hsreplay_cards_api import fetch_hsreplay_ranked_cards
from app.source_contracts import get_contract
from app.source_tiers import SourceTier, tier_for
from app.sources import SOURCE_BY_ID, Source


class _Response:
    def __init__(self, payload: object):
        self.payload = payload

    def __enter__(self) -> _Response:  # noqa: PYI034
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
        result = asyncio.run(
            fetch_hsreplay_card_period_json(
                "https://hsreplay.net/analytics/query/card_list/",
                source_id="hsreplay_cards_legend_patch",
            )
        )
    assert result.backend == "firecrawl"
    assert result.payload == card_list
    assert result.attempts == ({"backend": "firecrawl", "state": "ok"},)


def test_firecrawl_rejects_empty_raw_json_body() -> None:
    source = Source(
        "hsreplay_card_period_proxy",
        "https://hsreplay.net/analytics/query/card_list/",
        "hsreplay",
        "ranked_cards",
    )
    envelope = {"success": True, "data": {"rawHtml": ""}}

    with (
        patch.object(urllib.request, "urlopen", return_value=_Response(envelope)),
        pytest.raises(RuntimeError, match="did not include html"),
    ):
        firecrawl_backend._scrape_once(
            source,
            api_key="fc-canary",
            formats=["rawHtml"],
        )


def test_firecrawl_prefers_filtered_source_url_over_canonical_og_url() -> None:
    filtered_url = "https://www.hsguru.com/decks?format=2&period=patch_36.2.0"
    source = Source(
        "hsguru_filtered_url_test",
        filtered_url,
        "hsguru",
        "deck_catalog",
    )
    envelope = {
        "success": True,
        "data": {
            "html": '<div class="deck_stats_viewport">ok</div>',
            "metadata": {
                "statusCode": 200,
                "sourceURL": filtered_url,
                "ogUrl": "https://www.hsguru.com/decks",
            },
        },
    }

    with patch.object(
        urllib.request,
        "urlopen",
        return_value=_Response(envelope),
    ):
        result = firecrawl_backend._scrape_once(
            source,
            api_key="fc-canary",
            formats=["html"],
        )

    assert result.final_url == filtered_url


def test_shared_cascade_result_preserves_scrape_do_backend() -> None:
    from app import hsreplay_card_periods

    card_list = {"series": {"data": [{"dbfId": 2, "includedPopularity": 3.5}]}}

    async def scrape_card_period_page(_url: str, *, source_id: str) -> FirecrawlScrape:
        assert source_id == "hsreplay_cards_legend_patch"
        return FirecrawlScrape(
            html=json.dumps(card_list),
            markdown="",
            screenshot=None,
            metadata={"backend": "scrape_do", "scrapeDoCreditsUsed": 5},
            status_code=200,
            final_url="https://hsreplay.net/analytics/query/card_list/",
        )

    with patch.object(
        hsreplay_card_periods,
        "_scrape_card_period_page",
        side_effect=scrape_card_period_page,
    ):
        result = asyncio.run(
            fetch_hsreplay_card_period_json(
                "https://hsreplay.net/analytics/query/card_list/",
                source_id="hsreplay_cards_legend_patch",
            )
        )
    assert result.backend == "scrape_do"
    assert result.payload == card_list
    assert result.attempts == ({"backend": "scrape_do", "state": "ok"},)


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

    with (
        patch.object(urllib.request, "urlopen", side_effect=urlopen),
        pytest.raises(RuntimeError) as caught,
    ):
        asyncio.run(
            fetch_hsreplay_card_period_json(
                "https://hsreplay.net/analytics/query/card_list/",
                source_id="hsreplay_cards_legend_patch",
            )
        )
    message = str(caught.value)
    assert "fc-private-value" not in message
    assert "scrape-private-value" not in message
    assert "scrape_do:" in message
    assert "firecrawl:" in message


def test_card_period_uses_one_shared_provider_cascade() -> None:
    card_list = {"series": {"data": [{"dbfId": 3, "includedPopularity": 4.5}]}}
    provider_calls: list[str] = []

    def fail_provider(name: str):
        def fail(*_args: object, **_kwargs: object) -> FirecrawlScrape:
            provider_calls.append(name)
            raise RuntimeError(f"{name} unavailable")

        return fail

    def scrapfly_success(*_args: object, **_kwargs: object) -> FirecrawlScrape:
        provider_calls.append("scrapfly")
        return FirecrawlScrape(
            html=json.dumps(card_list),
            markdown="",
            screenshot=None,
            metadata={"backend": "scrapfly", "scrapflyCreditsUsed": 5},
            status_code=200,
            final_url="https://hsreplay.net/analytics/query/card_list/",
        )

    with (
        patch.object(firecrawl_backend, "scrape_do_token", return_value="configured"),
        patch.object(firecrawl_backend, "scrapfly_configured", return_value=True),
        patch.object(
            firecrawl_backend,
            "_scrape_via_scrape_do",
            side_effect=fail_provider("scrape_do"),
        ),
        patch.object(
            firecrawl_backend,
            "_scrape_via_firecrawl",
            side_effect=fail_provider("firecrawl"),
        ),
        patch.object(
            firecrawl_backend,
            "_scrape_via_scrapfly",
            side_effect=scrapfly_success,
        ),
        patch(
            "app.hsreplay_card_periods.firecrawl_api_key",
            return_value=None,
            create=True,
        ),
        patch(
            "app.hsreplay_card_periods._scrape_do_token",
            return_value=None,
            create=True,
        ),
    ):
        result = asyncio.run(
            fetch_hsreplay_card_period_json(
                "https://hsreplay.net/analytics/query/card_list/",
                source_id="hsreplay_cards_legend_patch",
            )
        )

    assert provider_calls == ["scrape_do", "firecrawl", "scrapfly"]
    assert result.backend == "scrapfly"
    assert result.payload == card_list


def test_ranked_cards_uses_proxy_fallback_after_direct_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import hsreplay_card_periods, hsreplay_cards_api, hsreplay_client

    source = Source(
        "hsreplay_cards_legend_3d",
        "https://hsreplay.net/cards/#rankRange=LEGEND&timeRange=LAST_3_DAYS&gameType=RANKED_STANDARD",
        "hsreplay",
        "ranked",
    )

    async def direct_failure(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("blocked")

    async def fallback_success(_url: str, *, source_id: str) -> CardPeriodFetch:
        assert source_id == source.id
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
    monkeypatch.setattr(
        hsreplay_card_periods, "fetch_hsreplay_card_period_json", fallback_success
    )
    monkeypatch.setattr(
        hsreplay_cards_api,
        "parse_cards_from_api_payloads",
        lambda *_args, **_kwargs: cards,
    )

    result = asyncio.run(fetch_hsreplay_ranked_cards(source))
    assert result["time_range"] == "LAST_3_DAYS"
    assert result["source"]["backend"] == "hsreplay_cards_api+scrape_do"
    assert result["_fetch_backend"] == "scrape_do"
    assert result["source"]["diagnostics"]["proxy_attempts"][0] == {
        "backend": "direct",
        "state": "failed",
        "error_type": "RuntimeError",
    }


def test_dynamic_card_period_uses_final_source_id_and_json_bright_acceptance() -> None:
    from app import firecrawl_backend

    source_id = "hsreplay_cards_wild_legend_patch"
    card_list = {"series": {"data": [{"dbfId": 1, "includedPopularity": 0.1}]}}
    captured: dict[str, object] = {}

    async def scrape(source: Source, **options: object) -> FirecrawlScrape:
        captured["source"] = source
        captured.update(options)
        return FirecrawlScrape(
            html=json.dumps(card_list),
            markdown="",
            screenshot=None,
            metadata={"backend": "brightdata_web_unlocker"},
            status_code=200,
            final_url=source.url,
        )

    with patch.object(
        firecrawl_backend, "scrape_source_with_options", side_effect=scrape
    ):
        result = asyncio.run(
            _scrape_card_period_page(
                "https://hsreplay.net/analytics/query/card_list/",
                source_id=source_id,
            )
        )

    assert isinstance(captured["source"], Source)
    assert captured["source"].id == source_id
    assert captured["formats"] == ["rawHtml"]
    assert captured["brightdata_render"] is False
    accept = captured["brightdata_accept_html"]
    assert callable(accept)
    assert accept(json.dumps(card_list)) is True
    assert accept('{"series":{"data":[]}}') is False
    assert accept("<html>Just a moment... cf-chl</html>") is False
    assert result.backend == "brightdata_web_unlocker"


def test_card_period_json_acceptance_rejects_malformed_and_challenge_bodies() -> None:
    assert _accept_card_period_json(
        '{"series":{"data":[{"dbfId":1,"includedPopularity":0.1}]}}'
    )
    assert not _accept_card_period_json('{"series":{"data":[]}}')
    assert not _accept_card_period_json('{"series":{}}')
    assert not _accept_card_period_json("not json")
    assert not _accept_card_period_json("<html>Just a moment... cf-chl</html>")


def test_card_period_json_acceptance_supports_real_nested_series_fixture() -> None:
    fixture = Path("tests/fixtures/contracts/hsreplay_card_list.json").read_text(
        encoding="utf-8"
    )
    assert _accept_card_period_json(fixture)
