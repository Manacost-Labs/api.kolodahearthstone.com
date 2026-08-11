from __future__ import annotations

from unittest.mock import call, patch

import pytest

from app.firecrawl_backend import _scrape_sync
from app.scrape_do_backend import ScrapeDoScrape
from app.sources import Source

SOURCE = Source(
    id="provider_chain_test",
    url="https://example.com/page",
    site="example",
    category="test",
)


def scrape_do_result(
    *,
    html: str = "<html><body><a href='/deck/1'>Deck</a></body></html>",
    screenshot: str | None = None,
    super_proxy: bool = False,
) -> ScrapeDoScrape:
    return ScrapeDoScrape(
        html=html,
        status_code=200,
        final_url=SOURCE.url,
        request_cost=25 if super_proxy else 5,
        credits_remaining=249_995,
        super_proxy=super_proxy,
        screenshot=screenshot,
    )


def firecrawl_result() -> object:
    return type(
        "Scraped",
        (),
        {
            "html": "<html><body>firecrawl</body></html>",
            "markdown": "firecrawl",
            "screenshot": None,
            "metadata": {"backend": "firecrawl", "creditsUsed": 1},
            "status_code": 200,
            "final_url": SOURCE.url,
        },
    )()


def firecrawl_lease() -> object:
    return type(
        "Lease",
        (),
        {
            "key": type(
                "Key",
                (),
                {
                    "key": "fc-test",
                    "label": "primary",
                    "fingerprint": "fc-test…test",
                },
            )()
        },
    )()


def test_scrape_do_is_primary_before_firecrawl_and_scrapfly() -> None:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(),
        ) as scrape_do,
        patch("app.firecrawl_backend.acquire_firecrawl_key") as firecrawl,
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch("app.firecrawl_backend.scrapfly_scrape_url_sync") as scrapfly,
    ):
        result = _scrape_sync(
            SOURCE,
            formats=["html", "markdown"],
            wait_ms=5_000,
            timeout_ms=30_000,
        )

    assert result.backend == "scrape_do"
    assert result.request_credits == 5
    assert "[Deck](/deck/1)" in result.markdown
    scrape_do.assert_called_once()
    firecrawl.assert_not_called()
    scrapfly.assert_not_called()


def test_firecrawl_runs_after_scrape_do_failure() -> None:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            side_effect=RuntimeError("scrape.do down"),
        ),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            return_value=firecrawl_lease(),
        ),
        patch(
            "app.firecrawl_backend._scrape_once",
            return_value=firecrawl_result(),
        ),
        patch(
            "app.firecrawl_backend.record_firecrawl_credits",
            return_value={"ok": True},
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch("app.firecrawl_backend.scrapfly_scrape_url_sync") as scrapfly,
    ):
        result = _scrape_sync(SOURCE, formats=["html"])

    assert result.backend == "firecrawl"
    assert result.firecrawl_credits_used == 1
    scrapfly.assert_not_called()


def test_scrapfly_runs_after_scrape_do_and_firecrawl() -> None:
    from app.scrapfly_backend import ScrapflyScrape

    scrapfly_result = ScrapflyScrape(
        html="<html><body><a href='/deck/2'>Alt</a></body></html>",
        status_code=200,
        final_url=SOURCE.url,
        request_cost=5,
        credits_remaining=900,
        asp=False,
        render_js=True,
        key_label="primary",
        key_fingerprint="scp-live-…03d6b",
    )
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            side_effect=RuntimeError("scrape.do down"),
        ),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=RuntimeError("All Firecrawl API keys are exhausted"),
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch(
            "app.firecrawl_backend.scrapfly_scrape_url_sync",
            return_value=scrapfly_result,
        ),
    ):
        result = _scrape_sync(SOURCE, formats=["html", "markdown"])

    assert result.backend == "scrapfly"
    assert result.scrapfly_credits_used == 5
    assert "[Alt](/deck/2)" in result.markdown


def test_brightdata_is_fourth_after_scrape_do_firecrawl_and_scrapfly() -> None:
    from app.brightdata_backend import BrightDataScrape

    calls: list[str] = []
    brightdata_options: dict[str, object] = {}

    def scrape_do_failure(*args, **kwargs):
        calls.append("scrape_do")
        raise RuntimeError("scrape.do down")

    def firecrawl_failure():
        calls.append("firecrawl")
        raise RuntimeError("All Firecrawl API keys are exhausted")

    def scrapfly_failure(*args, **kwargs):
        calls.append("scrapfly")
        raise RuntimeError("Scrapfly down")

    def brightdata_success(*args, **kwargs):
        calls.append("brightdata")
        brightdata_options.update(kwargs)
        return BrightDataScrape(
            html="<html><body><a href='/deck/3'>Unlocked</a></body></html>",
            status_code=200,
            final_url=SOURCE.url,
            billable_requests=1,
            request_id="hl_test_123",
            rendered=True,
            budget_remaining=9,
        )

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.firecrawl_backend.scrape_url_sync", side_effect=scrape_do_failure),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=firecrawl_failure,
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch(
            "app.firecrawl_backend.scrapfly_scrape_url_sync",
            side_effect=scrapfly_failure,
        ),
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=True,
        ),
        patch(
            "app.firecrawl_backend.brightdata_scrape_url_sync",
            side_effect=brightdata_success,
        ),
    ):
        result = _scrape_sync(SOURCE, formats=["html", "markdown"])

    assert list(dict.fromkeys(calls)) == [
        "scrape_do",
        "firecrawl",
        "scrapfly",
        "brightdata",
    ]
    assert result.backend == "brightdata_web_unlocker"
    assert result.brightdata_credits_used == 1
    assert result.request_credits == 1
    assert "[Unlocked](/deck/3)" in result.markdown
    assert result.metadata["brightDataRequestId"] == "hl_test_123"
    assert result.metadata["brightDataBudgetRemaining"] == 9
    assert brightdata_options["render"] is True
    accept_html = brightdata_options["accept_html"]
    assert callable(accept_html)
    assert accept_html(
        "<html><title>Just a moment</title>challenges.cloudflare.com</html>"
    ) is False
    assert accept_html("<html>" + "real page " * 300 + "</html>") is True


def test_invalid_scrapfly_configuration_does_not_block_brightdata() -> None:
    from app.brightdata_backend import BrightDataScrape

    brightdata_result = BrightDataScrape(
        html="<html><body>unlocked</body></html>",
        status_code=200,
        final_url=SOURCE.url,
        billable_requests=1,
        request_id=None,
        rendered=True,
        budget_remaining=9,
    )
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value=None),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=RuntimeError("Firecrawl unavailable"),
        ),
        patch(
            "app.firecrawl_backend.scrapfly_configured",
            side_effect=ValueError("invalid Scrapfly pool"),
        ),
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=True,
        ),
        patch(
            "app.firecrawl_backend.brightdata_scrape_url_sync",
            return_value=brightdata_result,
        ) as brightdata,
    ):
        result = _scrape_sync(SOURCE, formats=["html"])

    assert result.backend == "brightdata_web_unlocker"
    brightdata.assert_called_once()


@pytest.mark.parametrize(
    "options",
    [
        {"headers": {"Cookie": "session=secret"}},
        {"formats": [{"type": "screenshot", "fullPage": True}]},
        {"formats": ["map"]},
        {"formats": ["json"]},
    ],
)
def test_brightdata_never_receives_headers_screenshots_or_map(options) -> None:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value=None),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=RuntimeError("Firecrawl unavailable"),
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=False),
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=True,
        ),
        patch("app.firecrawl_backend.brightdata_scrape_url_sync") as brightdata,
        pytest.raises(RuntimeError, match="All scrape providers failed"),
    ):
        _scrape_sync(SOURCE, **options)

    brightdata.assert_not_called()


def test_scrape_do_challenge_escalates_standard_to_super() -> None:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            side_effect=[
                scrape_do_result(
                    html="<html><title>Just a moment</title>challenges.cloudflare.com</html>"
                ),
                scrape_do_result(
                    html="<html><body>real page</body></html>",
                    super_proxy=True,
                ),
            ],
        ) as scrape_do,
    ):
        result = _scrape_sync(SOURCE, formats=["html"])

    assert scrape_do.call_count == 2
    assert scrape_do.call_args_list[0].kwargs["super_proxy"] is False
    assert scrape_do.call_args_list[1].kwargs["super_proxy"] is True
    assert result.backend == "scrape_do_super"


def test_scrape_do_transient_error_is_retried_once() -> None:
    from app.scrape_do_backend import ScrapeDoTransientError

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            side_effect=[
                ScrapeDoTransientError(
                    "Scrape.do HTTP 429",
                    status_code=429,
                    retry_after_seconds=7,
                ),
                scrape_do_result(),
            ],
        ) as scrape_do,
        patch("app.firecrawl_backend.time.sleep") as sleep,
        patch("app.firecrawl_backend.random.uniform", return_value=1.0),
    ):
        result = _scrape_sync(SOURCE, formats=["html"])

    assert scrape_do.call_count == 2
    assert sleep.call_args_list == [call(7.0)]
    assert result.backend == "scrape_do"
    assert result.metadata["scrapeDoAttempts"] == 2


def test_all_providers_missing_raises_combined_error() -> None:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value=None),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=RuntimeError("All Firecrawl API keys are exhausted"),
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=False),
        pytest.raises(RuntimeError, match="All scrape providers failed"),
    ):
        _scrape_sync(SOURCE)
