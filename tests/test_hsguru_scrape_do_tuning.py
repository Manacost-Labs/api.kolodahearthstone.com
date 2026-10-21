"""Regression tests for the benchmarked HSGuru Scrape.do request shape."""

from __future__ import annotations

import urllib.parse
from email.message import Message
from unittest.mock import MagicMock, patch

from app.firecrawl_backend import (
    _HSGURU_SCRAPE_DO_PROFILES,
    _HSGURU_WAIT_SELECTOR,
    _accepted_provider_content,
    _scrape_sync,
)
from app.scrape_do_backend import ScrapeDoScrape, _scrape_url_sync
from app.sources import Source

HSGURU_SOURCE = Source(
    id="hsguru_tuning_test",
    url="https://www.hsguru.com/meta?format=2&rank=top_100",
    site="hsguru",
    category="meta",
)
COOKIE = "__cf_ob=edge-bound; _backend_key=session-value; cf_clearance=edge"
PAGE = (
    '<html><body><div data-phx-session="x" class="deck_stats_viewport">'
    "<table><tr><th>Archetype</th><th>Winrate</th></tr>"
    "<tr><td>Burn Mage</td><td>52%</td></tr></table></div>"
    + " deck row" * 40
    + "</body></html>"
)


def _response(body: bytes = b"<html>ok</html>") -> MagicMock:
    headers = Message()
    headers["Scrape.do-Request-Cost"] = "5"
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.headers = headers
    response.read.return_value = body
    return response


def _sent_params(**kwargs) -> tuple[dict[str, str], dict[str, str]]:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            return_value=_response(),
        ) as urlopen,
    ):
        _scrape_url_sync("https://www.hsguru.com/meta", **kwargs)
    request = urlopen.call_args.args[0]
    query = urllib.parse.urlsplit(request.full_url).query
    return dict(urllib.parse.parse_qsl(query)), dict(request.header_items())


def _scrape(url_results: list[object]) -> tuple[object, MagicMock]:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.firecrawl_backend.decodo_api_key", return_value=None),
        patch(
            "app.firecrawl_backend.scrape_url_sync", side_effect=url_results
        ) as scrape,
        patch("app.firecrawl_backend.time.sleep"),
    ):
        result = _scrape_sync(
            HSGURU_SOURCE,
            formats=["html"],
            headers={"Cookie": COOKIE},
            wait_ms=5_000,
            timeout_ms=120_000,
            skip_providers={"firecrawl", "brightdata", "scrapfly"},
        )
    return result, scrape


def _scraped(html: str, *, super_proxy: bool) -> ScrapeDoScrape:
    return ScrapeDoScrape(
        html=html,
        status_code=200,
        final_url=HSGURU_SOURCE.url,
        request_cost=10 if super_proxy else 5,
        credits_remaining=None,
        super_proxy=super_proxy,
    )


def test_browser_options_are_sent_only_for_rendered_requests() -> None:
    rendered, _ = _sent_params(
        render=True,
        wait_selector="[data-phx-session]",
        block_resources=True,
        session_id=1_000_042,
    )
    assert rendered["waitSelector"] == "[data-phx-session]"
    assert rendered["blockResources"] == "true"
    assert rendered["sessionId"] == "42"
    assert "customWait" not in rendered

    plain, _ = _sent_params(
        render=False,
        super_proxy=True,
        wait_selector="[data-phx-session]",
        block_resources=True,
        session_id=7,
    )
    assert "waitSelector" not in plain
    assert "blockResources" not in plain
    assert "disableRetry" not in plain
    assert plain["sessionId"] == "7"


def test_hsguru_ladder_skips_plain_and_starts_with_datacenter_render() -> None:
    assert _HSGURU_SCRAPE_DO_PROFILES == ((True, False), (False, True), (True, True))


def test_hsguru_render_waits_for_liveview_and_keeps_only_site_session() -> None:
    result, scrape = _scrape([_scraped(PAGE, super_proxy=False)])

    assert result.backend == "scrape_do"
    kwargs = scrape.call_args.kwargs
    assert (kwargs["render"], kwargs["super_proxy"]) == (True, False)
    assert kwargs["wait_selector"] == _HSGURU_WAIT_SELECTOR
    assert kwargs["wait_ms"] is None
    assert kwargs["block_resources"] is True
    assert isinstance(kwargs["session_id"], int)
    assert kwargs["headers"] == {"Cookie": "_backend_key=session-value"}
    assert "disable_retries" not in kwargs


def test_hsguru_residential_profile_is_anonymous_and_keeps_provider_retries() -> None:
    from app.scrape_do_backend import ScrapeDoTransientError

    failed_render = ScrapeDoTransientError("ROTATION_FAILED", status_code=502)
    result, scrape = _scrape([failed_render, _scraped(PAGE, super_proxy=True)])

    assert result.backend == "scrape_do_super"
    kwargs = scrape.call_args.kwargs
    assert (kwargs["render"], kwargs["super_proxy"]) == (False, True)
    # Any forwarded cookie makes Cloudflare block the non-browser request.
    assert kwargs["headers"] is None
    assert "disable_retries" not in kwargs
    assert "wait_selector" not in kwargs
    render_session = scrape.call_args_list[0].kwargs["session_id"]
    assert kwargs["session_id"] != render_session


def test_hsguru_premium_paywall_escalates_to_logged_in_rendered_super() -> None:
    from app.scrape_do_backend import ScrapeDoTransientError

    paywall = (
        "<html><body><div data-phx-session='x'>Meta You do not have access "
        "to these filters. Login to access this page</div>"
        + " navigation" * 60
        + "</body></html>"
    )
    result, scrape = _scrape(
        [
            ScrapeDoTransientError("ROTATION_FAILED", status_code=502),
            _scraped(paywall, super_proxy=True),
            _scraped(PAGE, super_proxy=True),
        ]
    )

    assert [
        (item.kwargs["render"], item.kwargs["super_proxy"])
        for item in scrape.call_args_list
    ] == [(True, False), (False, True), (True, True)]
    assert scrape.call_args.kwargs["headers"] == {
        "Cookie": "_backend_key=session-value"
    }
    assert result.html == PAGE


def test_hsguru_paywall_page_is_never_accepted() -> None:
    page = "<html><body>You do not have access to these filters." + " x" * 400
    assert _accepted_provider_content(page, HSGURU_SOURCE, accept_html=None) is False
    assert (
        _accepted_provider_content(page, HSGURU_SOURCE, accept_html=lambda _: True)
        is False
    )


def test_non_hsguru_sources_keep_their_request_shape() -> None:
    source = Source(
        id="example_tuning_test",
        url="https://example.com/page",
        site="example",
        category="test",
    )
    page = "<html><body>example.com" + " content" * 300 + "</body></html>"
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=ScrapeDoScrape(
                html=page,
                status_code=200,
                final_url=source.url,
                request_cost=5,
                credits_remaining=None,
                super_proxy=False,
            ),
        ) as scrape,
    ):
        _scrape_sync(
            source,
            formats=["html"],
            headers={"Cookie": COOKIE},
            wait_ms=3_000,
            skip_providers={"firecrawl", "brightdata", "scrapfly"},
        )

    kwargs = scrape.call_args.kwargs
    assert kwargs["headers"] == {"Cookie": COOKIE}
    assert kwargs["wait_ms"] == 3_000
    assert "wait_selector" not in kwargs
    assert "session_id" not in kwargs
