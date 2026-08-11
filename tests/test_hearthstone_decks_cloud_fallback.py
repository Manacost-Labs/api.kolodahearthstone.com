from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.firecrawl_backend import FirecrawlScrape
from app.hearthstone_decks import (
    _fetch_cloud_deck_code,
    _fetch_wordpress_format,
    _parse_wordpress_posts,
    fetch_hearthstone_decks,
)
from app.proxy_errors import ProxyPaymentRequiredError
from app.sources import SOURCE_BY_ID, Source

SOURCE = SOURCE_BY_ID["hearthstone_decks"]
VALID_DECK_CODE = (
    "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6h"
    "Bom1BoSZB+C+B43cBwAA"
)


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
                    "deck_code": VALID_DECK_CODE,
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


def _wordpress_posts(
    format_name: str,
    *,
    category_id: int,
    count: int = 20,
) -> list[dict]:
    slug = format_name.lower()
    return [
        {
            "id": category_id * 1_000 + index,
            "date": "2026-08-11T12:34:56",
            "modified": "2026-08-11T13:45:01",
            "link": f"https://hearthstone-decks.net/{slug}-api-deck-{index}/",
            "title": {
                "rendered": (
                    f"{format_name} &amp; Deck {index} #1 Legend &#8211; Player {index}"
                )
            },
            "content": {
                "rendered": (
                    f'<button data-clipboard-text="{VALID_DECK_CODE}">'
                    "Copy deck</button>"
                )
            },
            "categories": [category_id, 100 + index],
        }
        for index in range(count)
    ]


def _wordpress_unavailable() -> AsyncMock:
    return AsyncMock(side_effect=RuntimeError("wordpress REST unavailable"))


def test_wordpress_parser_extracts_codes_and_decodes_titles_without_details() -> None:
    rows = _parse_wordpress_posts(
        _wordpress_posts("Standard", category_id=3),
        format_name="Standard",
        category_id=3,
    )

    assert len(rows) == 20
    assert rows[0]["title"] == "Standard & Deck 0 #1 Legend – Player 0"
    assert rows[0]["archetype"] == "Standard & Deck 0"
    assert rows[0]["deck_code"] == VALID_DECK_CODE
    assert rows[0]["deck_code_status"] == "ok"
    assert rows[0]["deck_code_source"] == "wordpress_content"
    assert rows[0]["detail_attempts"] == 0
    assert rows[0]["published_at"] == "2026-08-11T12:34:56"
    assert rows[0]["modified_at"] == "2026-08-11T13:45:01"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda posts: posts.pop(), "coverage incomplete"),
        (
            lambda posts: posts[1].__setitem__("id", posts[0]["id"]),
            "duplicate post id",
        ),
        (
            lambda posts: posts[1].__setitem__("link", posts[0]["link"]),
            "duplicate post URL",
        ),
        (
            lambda posts: posts[0].__setitem__("categories", [999]),
            "missing category",
        ),
    ],
)
def test_wordpress_parser_rejects_incomplete_or_ambiguous_payloads(
    mutate,
    message: str,
) -> None:
    posts = _wordpress_posts("Standard", category_id=3)
    mutate(posts)

    with pytest.raises(RuntimeError, match=message):
        _parse_wordpress_posts(
            posts,
            format_name="Standard",
            category_id=3,
        )


def test_wordpress_parser_accepts_one_missing_code_but_rejects_two() -> None:
    posts = _wordpress_posts("Standard", category_id=3)
    posts[0]["content"] = {"rendered": "No deck code"}

    rows = _parse_wordpress_posts(
        posts,
        format_name="Standard",
        category_id=3,
    )

    assert rows[0]["deck_code"] == ""
    assert rows[0]["deck_code_status"] == "missing"
    assert rows[0]["deck_code_error"] == "missing from wordpress content"

    posts[1]["content"] = {"rendered": "No deck code either"}
    with pytest.raises(RuntimeError, match="deck-code coverage incomplete"):
        _parse_wordpress_posts(
            posts,
            format_name="Standard",
            category_id=3,
        )


def test_wordpress_parser_does_not_count_regex_shaped_garbage_as_a_deck() -> None:
    posts = _wordpress_posts("Standard", category_id=3)
    posts[0]["content"] = {
        "rendered": f'<button data-clipboard-text="{_deck_code(0)}">Copy</button>'
    }

    rows = _parse_wordpress_posts(
        posts,
        format_name="Standard",
        category_id=3,
    )

    assert rows[0]["deck_code"] == ""
    assert rows[0]["deck_code_status"] == "missing"


def test_wordpress_transport_sends_bounded_filtered_request() -> None:
    async def run() -> list[dict]:
        def respond(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "hearthstone-decks.net"
            assert request.url.params["categories"] == "3"
            assert request.url.params["per_page"] == "20"
            assert request.url.params["page"] == "1"
            assert request.url.params["orderby"] == "date"
            assert request.url.params["order"] == "desc"
            assert "content" in request.url.params["_fields"]
            assert "modified" in request.url.params["_fields"]
            return httpx.Response(
                200,
                json=_wordpress_posts("Standard", category_id=3),
                request=request,
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            follow_redirects=True,
        ) as client:
            return await _fetch_wordpress_format(
                client,
                format_name="Standard",
                category_id=3,
            )

    rows = asyncio.run(run())

    assert len(rows) == 20
    assert all(row["deck_code"] == VALID_DECK_CODE for row in rows)


def test_wordpress_transport_rejects_non_json_and_foreign_redirects() -> None:
    async def non_json() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html>not JSON</html>",
                headers={"content-type": "text/html"},
                request=request,
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
        ) as client:
            with pytest.raises(RuntimeError, match="response is not JSON"):
                await _fetch_wordpress_format(
                    client,
                    format_name="Standard",
                    category_id=3,
                )

    async def foreign_redirect() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host == "hearthstone-decks.net":
                return httpx.Response(
                    302,
                    headers={"location": "https://example.invalid/posts"},
                    request=request,
                )
            return httpx.Response(200, json=[], request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            follow_redirects=True,
        ) as client:
            with pytest.raises(RuntimeError, match="response URL rejected"):
                await _fetch_wordpress_format(
                    client,
                    format_name="Standard",
                    category_id=3,
                )

    asyncio.run(non_json())
    asyncio.run(foreign_redirect())


def test_wordpress_rest_is_primary_and_needs_only_two_list_requests() -> None:
    async def wordpress_lists() -> dict[str, list[dict]]:
        return {
            "Standard": _parse_wordpress_posts(
                _wordpress_posts("Standard", category_id=3),
                format_name="Standard",
                category_id=3,
            ),
            "Wild": _parse_wordpress_posts(
                _wordpress_posts("Wild", category_id=13),
                format_name="Wild",
                category_id=13,
            ),
        }

    with (
        patch(
            "app.hearthstone_decks._fetch_wordpress_lists",
            side_effect=wordpress_lists,
        ) as rest,
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            new=AsyncMock(side_effect=AssertionError("HTML fallback must stay idle")),
        ) as provider,
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(side_effect=AssertionError("residential must stay idle")),
        ) as residential,
        patch("app.hearthstone_decks.load_dataset", return_value=None),
    ):
        structured = asyncio.run(fetch_hearthstone_decks(SOURCE))

    assert structured["total_decks"] == 40
    assert structured["with_deck_code"] == 40
    assert structured["deck_code_fill_rate"] == 1.0
    assert structured["_fetch_backend"] == "wordpress_rest_direct"
    assert structured["fetch_strategy"] == "wordpress_rest"
    assert structured["wordpress_rest_requests"] == 2
    assert structured["wordpress_rest_accepted_formats"] == 2
    assert structured["html_list_pages"] == 0
    assert structured["detail_page_attempts"] == 0
    assert all(deck["detail_attempts"] == 0 for deck in structured["decks"])
    from app.fetcher import _dataset_from_structured

    parsed = _dataset_from_structured(
        SOURCE,
        structured,
        backend="hearthstone_decks_api",
    )
    assert parsed["_transport_backend"] == "wordpress_rest_direct"
    rest.assert_awaited_once()
    provider.assert_not_awaited()
    residential.assert_not_awaited()


def test_wordpress_rest_reuses_lkg_for_one_missing_content_code() -> None:
    standard_posts = _wordpress_posts("Standard", category_id=3)
    standard_posts[0]["content"] = {"rendered": "Deck code temporarily missing"}
    lists = {
        "Standard": _parse_wordpress_posts(
            standard_posts,
            format_name="Standard",
            category_id=3,
        ),
        "Wild": _parse_wordpress_posts(
            _wordpress_posts("Wild", category_id=13),
            format_name="Wild",
            category_id=13,
        ),
    }
    cached = {
        "data": {
            "structured": {
                "type": "hearthstone_decks",
                "decks": [
                    {
                        "url": lists["Standard"][0]["url"],
                        "deck_code": VALID_DECK_CODE,
                        "deck_code_status": "ok",
                        "detail_attempts": 1,
                    }
                ],
            }
        }
    }

    with (
        patch(
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=AsyncMock(return_value=lists),
        ),
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            new=AsyncMock(side_effect=AssertionError("detail fetch must stay idle")),
        ) as provider,
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            new=AsyncMock(side_effect=AssertionError("residential must stay idle")),
        ) as residential,
        patch("app.hearthstone_decks.load_dataset", return_value=cached),
    ):
        structured = asyncio.run(fetch_hearthstone_decks(SOURCE))

    restored = structured["decks"][0]
    assert structured["with_deck_code"] == 40
    assert structured["cached_deck_codes_reused"] == 1
    assert structured["detail_page_attempts"] == 0
    assert restored["deck_code"] == VALID_DECK_CODE
    assert restored["deck_code_source"] == "last_known_good"
    assert restored["previous_detail_attempts"] == 1
    assert "deck_code_error" not in restored
    provider.assert_not_awaited()
    residential.assert_not_awaited()


def test_detail_fallback_rejects_regex_shaped_garbage_deck_code() -> None:
    fake_html = f'<button data-clipboard-text="{_deck_code(0)}">Copy fake deck</button>'
    with patch(
        "app.hearthstone_decks._fetch_cloud_html",
        new=AsyncMock(return_value=(fake_html, "scrape_do")),
    ):
        result, backend = asyncio.run(
            _fetch_cloud_deck_code(
                SOURCE,
                "https://hearthstone-decks.net/example-deck/",
            )
        )

    assert result["deck_code"] == ""
    assert result["deck_code_status"] == "missing"
    assert result["deck_code_error"] == "invalid deck code"
    assert backend is None


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


def test_validated_cloud_lists_avoid_residential_and_reuse_cached_codes() -> None:
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
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=_wordpress_unavailable(),
        ),
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
    residential.assert_not_awaited()


def test_cloud_failure_uses_residential_lists_as_last_fallback() -> None:
    async def residential(_source_id: str, url: str) -> str:
        format_name = "Wild" if "/wild-decks/" in url else "Standard"
        return _list_html(format_name)

    with (
        patch(
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=_wordpress_unavailable(),
        ),
        patch(
            "app.hearthstone_decks._fetch_residential_html",
            side_effect=residential,
        ) as residential_fetch,
        patch(
            "app.hearthstone_decks.scrape_source_with_options",
            new=AsyncMock(side_effect=RuntimeError("cloud unavailable")),
        ) as provider,
        patch(
            "app.hearthstone_decks.load_dataset",
            return_value=_cached_dataset(),
        ),
    ):
        structured = asyncio.run(fetch_hearthstone_decks(SOURCE))

    assert structured["total_decks"] == 40
    assert structured["with_deck_code"] == 40
    assert structured["_fetch_backend"] == "residential_httpx"
    assert provider.await_count == 2
    assert residential_fetch.await_count == 2


def test_cloud_list_candidate_with_fewer_than_twenty_rows_fails_closed() -> None:
    async def cloud(source: Source, **_options):
        format_name = "Wild" if "/wild-decks/" in source.url else "Standard"
        count = 19 if format_name == "Wild" else 20
        return _provider_result(source, _list_html(format_name, count=count))

    with (
        patch(
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=_wordpress_unavailable(),
        ),
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
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=_wordpress_unavailable(),
        ),
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
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=_wordpress_unavailable(),
        ),
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


def test_cloud_detail_requests_cover_current_lists_with_bounded_concurrency() -> None:
    detail_urls: list[str] = []
    active_details = 0
    max_active_details = 0

    async def cloud(source: Source, **options):
        nonlocal active_details, max_active_details
        if source.url.endswith("-decks/"):
            format_name = "Wild" if "/wild-decks/" in source.url else "Standard"
            html = _list_html(format_name)
        else:
            detail_urls.append(source.url)
            active_details += 1
            max_active_details = max(max_active_details, active_details)
            try:
                await asyncio.sleep(0.001)
                html = f'<button data-clipboard-text="{VALID_DECK_CODE}">Copy</button>'
            finally:
                active_details -= 1
        candidate = _provider_result(source, html)
        assert options["accept_result"](candidate)
        return candidate

    with (
        patch(
            "app.hearthstone_decks._fetch_wordpress_lists",
            new=_wordpress_unavailable(),
        ),
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
    assert structured["with_deck_code"] == 40
    assert structured["missing_deck_code_count"] == 0
    assert structured["cloud_detail_attempts"] == 40
    assert len(detail_urls) == 40
    assert 1 < max_active_details <= 4
    assert not any(
        deck.get("deck_code_status") == "deferred" for deck in structured["decks"]
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
