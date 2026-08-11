from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.firecrawl_backend import FirecrawlScrape
from app.hearthstone_decks import fetch_hearthstone_decks
from app.proxy_errors import ProxyPaymentRequiredError
from app.sources import SOURCE_BY_ID, Source

SOURCE = SOURCE_BY_ID["hearthstone_decks"]


def _deck_code(index: int) -> str:
    return f"AAECAf0GBMABCD{index:02d}1234567890abcdefghijklmnop=="


def _list_html(format_name: str, *, count: int = 20) -> str:
    slug = format_name.lower()
    articles = "".join(
        (
            '<article><h3 class="elementor-post__title">'
            f'<a href="https://hearthstone-decks.net/{slug}-deck-{index}/">'
            f"{format_name} Deck {index} #1 Legend – Player {index}"
            "</a></h3>"
            '<span class="elementor-post-date">August 11, 2026</span>'
            "</article>"
        )
        for index in range(count)
    )
    return f"<html><head><title>Hearthstone Decks</title></head><body>{articles}</body></html>"


def _cached_dataset(*, per_format: int = 30) -> dict:
    decks = []
    for format_name in ("Standard", "Wild"):
        slug = format_name.lower()
        for index in range(per_format):
            decks.append(
                {
                    "title": f"{format_name} Deck {index} #1 Legend – Player {index}",
                    "url": f"https://hearthstone-decks.net/{slug}-deck-{index}/",
                    "format": format_name,
                    "deck_code": _deck_code(index),
                    "deck_code_status": "ok",
                }
            )
    return {
        "fetched_at": "2026-08-10T00:00:00+00:00",
        "data": {
            "structured": {
                "type": "hearthstone_decks",
                "decks": decks,
            }
        },
    }


def _provider_result(
    source: Source,
    html: str,
    *,
    final_url: str | None = None,
) -> FirecrawlScrape:
    return FirecrawlScrape(
        html=html,
        markdown="",
        screenshot=None,
        metadata={"backend": "scrape_do", "scrapeDoCreditsUsed": 5},
        status_code=200,
        final_url=final_url or source.url,
    )


def test_proxy_402_uses_validated_cloud_lists_and_reuses_nested_cached_codes() -> None:
    provider_urls: list[str] = []

    async def cloud(source: Source, **options):
        provider_urls.append(source.url)
        assert "headers" not in options
        assert options["formats"] == ["html"]
        assert options["only_main_content"] is False
        assert options["max_age_ms"] == 0
        format_name = "Wild" if "/wild-decks/" in source.url else "Standard"
        html = _list_html(format_name)
        candidate = _provider_result(source, html)
        assert options["brightdata_accept_html"](html)
        assert options["accept_result"](candidate)
        assert not options["accept_result"](
            _provider_result(source, "<html><body>Just a moment...</body></html>")
        )
        return candidate

    with (
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(
                side_effect=ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            ),
        ) as residential,
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            side_effect=cloud,
        ) as provider,
        patch(
            "app.hearthstone_decks.load_dataset",
            return_value=_cached_dataset(),
        ),
        patch("app.scrapers.rotator.record_residential_proxy_failure"),
    ):
        structured = asyncio.run(fetch_hearthstone_decks(SOURCE))

    assert structured["total_decks"] == 40
    assert structured["standard_count"] == 20
    assert structured["wild_count"] == 20
    assert structured["with_deck_code"] == 40
    assert structured["_fetch_backend"] == "scrape_do"
    assert all(deck["deck_code_reused"] for deck in structured["decks"])
    assert len(provider_urls) == 2
    assert provider.await_count == 2
    assert residential.await_count == 1


def test_cloud_list_candidate_with_fewer_than_twenty_rows_fails_closed() -> None:
    async def cloud(source: Source, **_options):
        format_name = "Wild" if "/wild-decks/" in source.url else "Standard"
        count = 19 if format_name == "Wild" else 20
        return _provider_result(source, _list_html(format_name, count=count))

    with (
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(
                side_effect=ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            ),
        ),
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            side_effect=cloud,
        ),
        patch("app.hearthstone_decks.load_dataset", return_value=None),
        patch("app.scrapers.rotator.record_residential_proxy_failure"),
        pytest.raises(RuntimeError, match="cloud response failed validation"),
    ):
        asyncio.run(fetch_hearthstone_decks(SOURCE))


def test_cloud_list_candidate_redirected_to_wrong_path_fails_closed() -> None:
    async def cloud(source: Source, **_options):
        return _provider_result(
            source,
            _list_html("Standard"),
            final_url="https://hearthstone-decks.net/",
        )

    with (
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(
                side_effect=ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            ),
        ),
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            side_effect=cloud,
        ),
        patch("app.hearthstone_decks.load_dataset", return_value=None),
        patch("app.scrapers.rotator.record_residential_proxy_failure"),
        pytest.raises(RuntimeError, match="cloud response failed validation"),
    ):
        asyncio.run(fetch_hearthstone_decks(SOURCE))


def test_standard_and_wild_lists_must_not_reuse_the_same_deck_urls() -> None:
    async def cloud(source: Source, **_options):
        return _provider_result(source, _list_html("Standard"))

    with (
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(
                side_effect=ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            ),
        ),
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            side_effect=cloud,
        ),
        patch("app.hearthstone_decks.load_dataset", return_value=None),
        patch("app.scrapers.rotator.record_residential_proxy_failure"),
        pytest.raises(RuntimeError, match="format lists overlap"),
    ):
        asyncio.run(fetch_hearthstone_decks(SOURCE))


def test_cloud_detail_requests_are_bounded_when_no_cached_codes_exist() -> None:
    detail_urls: list[str] = []

    async def cloud(source: Source, **options):
        if source.url.endswith("-decks/"):
            format_name = "Wild" if "/wild-decks/" in source.url else "Standard"
            html = _list_html(format_name)
        else:
            detail_urls.append(source.url)
            index = len(detail_urls)
            html = f'<button data-clipboard-text="{_deck_code(index)}">Copy</button>'
        candidate = _provider_result(source, html)
        assert options["accept_result"](candidate)
        return candidate

    with (
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(
                side_effect=ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            ),
        ),
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            side_effect=cloud,
        ),
        patch("app.hearthstone_decks.load_dataset", return_value=None),
        patch("app.scrapers.rotator.record_residential_proxy_failure"),
    ):
        structured = asyncio.run(fetch_hearthstone_decks(SOURCE))

    assert structured["total_decks"] == 40
    assert structured["with_deck_code"] == 8
    assert structured["missing_deck_code_count"] == 32
    assert len(detail_urls) == 8
    assert (
        sum(deck.get("deck_code_status") == "deferred" for deck in structured["decks"])
        == 32
    )


def test_residential_helper_never_falls_back_to_direct_server_egress() -> None:
    from app.hearthstone_decks import _fetch_residential_html

    with (
        patch(
            "app.hearthstone_decks.httpx_client_kwargs",
            return_value={"timeout": 30.0},
        ),
        patch("app.hearthstone_decks.httpx.AsyncClient") as client,
        pytest.raises(RuntimeError, match="residential proxy route unavailable"),
    ):
        asyncio.run(
            _fetch_residential_html(
                SOURCE.id,
                SOURCE.url,
            )
        )

    client.assert_not_called()


def test_scrape_do_makes_hearthstone_decks_independent_of_proxy_circuit() -> None:
    from app.fetch_routes import (
        source_can_run_without_residential_proxy,
        source_has_cloud_html_route,
    )

    with (
        patch("app.fetch_routes.scrape_do_token", return_value="configured"),
        patch("app.fetch_routes.fetch_direct_enabled", return_value=False),
        patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set()),
        patch("app.fetch_routes.firecrawl_fallback_source_ids", return_value=set()),
    ):
        assert source_has_cloud_html_route(SOURCE)
        assert source_can_run_without_residential_proxy(SOURCE)
