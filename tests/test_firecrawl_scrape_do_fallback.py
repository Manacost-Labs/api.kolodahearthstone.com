from __future__ import annotations

import asyncio
import base64
import binascii
import random
import struct
import zlib
from unittest.mock import call, patch

import pytest

from app.firecrawl_backend import _scrape_sync, scrape_source
from app.scrape_do_backend import ScrapeDoScrape
from app.sources import Source

SOURCE = Source(
    id="provider_chain_test",
    url="https://example.com/page",
    site="example",
    category="test",
)

HSREPLAY_SOURCE = Source(
    id="provider_chain_hsreplay_test",
    url="https://hsreplay.net/cards/",
    site="hsreplay",
    category="test",
)

HSGURU_SOURCE = Source(
    id="provider_chain_hsguru_test",
    url="https://www.hsguru.com/meta?format=2&rank=legend",
    site="hsguru",
    category="meta",
)


def valid_html(body: str, *, identity: str = "example.com") -> str:
    return f"<html><body>{identity}{body}{' page content' * 250}</body></html>"


def valid_small_hsguru_html() -> str:
    body = (
        '<div class="deck_stats_viewport"><table><tr><th>Archetype</th>'
        "<th>Winrate</th><th>Popularity</th></tr><tr><td>Control Warrior</td>"
        "<td>52.4%</td><td>3.1%</td></tr></table></div>"
    )
    return f"<html><body>{body}{' deck row' * 24}</body></html>"


def valid_png_bytes() -> bytes:
    width = height = 64
    rng = random.Random(0)
    scanlines = b"".join(b"\x00" + rng.randbytes(width * 3) for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", checksum)
        )

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(scanlines)),
            chunk(b"IEND", b""),
        )
    )


def valid_png_data_uri() -> str:
    return "data:image/png;base64," + base64.b64encode(valid_png_bytes()).decode()


def scrape_do_result(
    *,
    html: str | None = None,
    screenshot: str | None = None,
    super_proxy: bool = False,
    source: Source = SOURCE,
) -> ScrapeDoScrape:
    return ScrapeDoScrape(
        html=(html if html is not None else valid_html("<a href='/deck/1'>Deck</a>")),
        status_code=200,
        final_url=source.url,
        request_cost=25 if super_proxy else 5,
        credits_remaining=249_995,
        super_proxy=super_proxy,
        screenshot=screenshot,
    )


def firecrawl_result(
    *,
    html: str | None = None,
    source: Source = SOURCE,
) -> object:
    return type(
        "Scraped",
        (),
        {
            "html": html if html is not None else valid_html("firecrawl"),
            "markdown": "firecrawl",
            "screenshot": None,
            "metadata": {"backend": "firecrawl", "creditsUsed": 1},
            "status_code": 200,
            "final_url": source.url,
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


def test_scrapfly_runs_after_scrape_do_firecrawl_and_unavailable_brightdata() -> None:
    from app.scrapfly_backend import ScrapflyScrape

    scrapfly_result = ScrapflyScrape(
        html=valid_html("<a href='/deck/2'>Alt</a>"),
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
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=False,
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


def test_brightdata_is_third_before_scrapfly() -> None:
    from app.brightdata_backend import BrightDataScrape

    calls: list[str] = []
    brightdata_options: dict[str, object] = {}

    def scrape_do_failure(*args, **kwargs):
        calls.append("scrape_do")
        raise RuntimeError("scrape.do down")

    def firecrawl_failure():
        calls.append("firecrawl")
        raise RuntimeError("All Firecrawl API keys are exhausted")

    def brightdata_success(*args, **kwargs):
        calls.append("brightdata")
        brightdata_options.update(kwargs)
        return BrightDataScrape(
            html=valid_html("<a href='/deck/3'>Unlocked</a>"),
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
        patch(
            "app.firecrawl_backend.scrapfly_configured", return_value=True
        ) as configured,
        patch("app.firecrawl_backend.scrapfly_scrape_url_sync") as scrapfly,
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
    assert (
        accept_html(
            "<html><title>Just a moment</title>challenges.cloudflare.com</html>"
        )
        is False
    )
    assert accept_html("<html>" + "real page " * 300 + "</html>") is True
    configured.assert_not_called()
    scrapfly.assert_not_called()
    assert result.metadata["providerPolicy"] == (
        "scrape_do_firecrawl_brightdata_scrapfly"
    )


def test_brightdata_success_does_not_inspect_scrapfly_configuration() -> None:
    from app.brightdata_backend import BrightDataScrape

    brightdata_result = BrightDataScrape(
        html=valid_html("unlocked"),
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
        ) as scrapfly_configured,
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
    scrapfly_configured.assert_not_called()


def test_scrapfly_is_fourth_after_brightdata_failure() -> None:
    from app.scrapfly_backend import ScrapflyScrape

    calls: list[str] = []

    def scrape_do_failure(*args, **kwargs):
        calls.append("scrape_do")
        raise RuntimeError("scrape.do down")

    def firecrawl_failure():
        calls.append("firecrawl")
        raise RuntimeError("All Firecrawl API keys are exhausted")

    def brightdata_failure(*args, **kwargs):
        calls.append("brightdata")
        raise RuntimeError("Bright Data down")

    def scrapfly_success(*args, **kwargs):
        calls.append("scrapfly")
        return ScrapflyScrape(
            html=valid_html("final fallback"),
            status_code=200,
            final_url=SOURCE.url,
            request_cost=5,
            credits_remaining=900,
            asp=False,
            render_js=True,
        )

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.firecrawl_backend.scrape_url_sync", side_effect=scrape_do_failure),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=firecrawl_failure,
        ),
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=True,
        ),
        patch(
            "app.firecrawl_backend.brightdata_scrape_url_sync",
            side_effect=brightdata_failure,
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch(
            "app.firecrawl_backend.scrapfly_scrape_url_sync",
            side_effect=scrapfly_success,
        ),
    ):
        result = _scrape_sync(SOURCE, formats=["html"])

    assert list(dict.fromkeys(calls)) == [
        "scrape_do",
        "firecrawl",
        "brightdata",
        "scrapfly",
    ]
    assert result.backend == "scrapfly"


def test_invalid_2xx_content_continues_through_every_provider() -> None:
    from app.brightdata_backend import BrightDataScrape
    from app.scrapfly_backend import ScrapflyScrape

    calls: list[str] = []
    wrong_page = valid_html("wrong tenant", identity="unrelated.example")
    challenge = valid_html(
        "<title>Just a moment</title>challenges.cloudflare.com",
        identity="hsreplay.net",
    )

    def scrape_do_wrong(*args, **kwargs):
        calls.append("scrape_do")
        return scrape_do_result(html=wrong_page, source=HSREPLAY_SOURCE)

    def firecrawl_challenge(*args, **kwargs):
        calls.append("firecrawl")
        return firecrawl_result(html=challenge, source=HSREPLAY_SOURCE)

    def brightdata_wrong(*args, **kwargs):
        calls.append("brightdata")
        return BrightDataScrape(
            html=wrong_page,
            status_code=200,
            final_url=HSREPLAY_SOURCE.url,
            billable_requests=1,
            request_id=None,
            rendered=True,
            budget_remaining=9,
        )

    def scrapfly_valid(*args, **kwargs):
        calls.append("scrapfly")
        return ScrapflyScrape(
            html=valid_html("valid arena cards page", identity="hsreplay.net"),
            status_code=200,
            final_url=HSREPLAY_SOURCE.url,
            request_cost=5,
            credits_remaining=900,
            asp=False,
            render_js=True,
        )

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.firecrawl_backend.scrape_url_sync", side_effect=scrape_do_wrong),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            return_value=firecrawl_lease(),
        ),
        patch("app.firecrawl_backend._scrape_once", side_effect=firecrawl_challenge),
        patch(
            "app.firecrawl_backend.record_firecrawl_credits",
            return_value={"ok": True},
        ),
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=True,
        ),
        patch(
            "app.firecrawl_backend.brightdata_scrape_url_sync",
            side_effect=brightdata_wrong,
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch(
            "app.firecrawl_backend.scrapfly_scrape_url_sync",
            side_effect=scrapfly_valid,
        ),
    ):
        result = _scrape_sync(HSREPLAY_SOURCE, formats=["html"])

    assert calls == ["scrape_do", "firecrawl", "brightdata", "scrapfly"]
    assert result.backend == "scrapfly"


def test_short_hsguru_meta_shell_does_not_stop_provider_fallback() -> None:
    from app.scrapfly_backend import ScrapflyScrape

    shell = (
        '<html><head><meta name="robots" content="noindex"></head>'
        "<body>Not found</body></html>"
    )
    scrapfly_result = ScrapflyScrape(
        html=valid_small_hsguru_html(),
        status_code=200,
        final_url=HSGURU_SOURCE.url,
        request_cost=5,
        credits_remaining=900,
        asp=True,
        render_js=True,
    )
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(html=shell, source=HSGURU_SOURCE),
        ),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            side_effect=RuntimeError("Firecrawl unavailable"),
        ),
        patch(
            "app.firecrawl_backend.brightdata_configured_for_source",
            return_value=False,
        ),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch(
            "app.firecrawl_backend.scrapfly_scrape_url_sync",
            return_value=scrapfly_result,
        ) as scrapfly,
    ):
        result = _scrape_sync(HSGURU_SOURCE, formats=["html"])

    assert result.backend == "scrapfly"
    assert len(result.html.encode()) < 2_000
    scrapfly.assert_called_once()


def test_small_structural_hsguru_html_remains_accepted() -> None:
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(
                html=valid_small_hsguru_html(),
                source=HSGURU_SOURCE,
            ),
        ),
        patch("app.firecrawl_backend.acquire_firecrawl_key") as firecrawl,
    ):
        result = _scrape_sync(HSGURU_SOURCE, formats=["html"])

    assert result.backend.startswith("scrape_do")
    assert 256 <= len(result.html.encode()) < 2_000
    firecrawl.assert_not_called()


def test_short_hsreplay_login_page_does_not_stop_provider_fallback() -> None:
    login = (
        "<html><head><title>Log in</title></head><body>"
        "Sign in to hsreplay.net to continue.</body></html>"
    )
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(html=login, source=HSREPLAY_SOURCE),
        ),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            return_value=firecrawl_lease(),
        ),
        patch(
            "app.firecrawl_backend._scrape_once",
            return_value=firecrawl_result(
                html=valid_html("arena cards", identity="hsreplay.net"),
                source=HSREPLAY_SOURCE,
            ),
        ),
        patch(
            "app.firecrawl_backend.record_firecrawl_credits",
            return_value={"ok": True},
        ),
    ):
        result = _scrape_sync(HSREPLAY_SOURCE, formats=["html"])

    assert result.backend == "firecrawl"


def test_scrape_source_accept_result_callback_continues_provider_fallback() -> None:
    first = scrape_do_result(source=SOURCE)
    second = firecrawl_result(source=SOURCE)

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.firecrawl_backend.scrape_url_sync", return_value=first),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            return_value=firecrawl_lease(),
        ),
        patch("app.firecrawl_backend._scrape_once", return_value=second),
        patch(
            "app.firecrawl_backend.record_firecrawl_credits",
            return_value={"ok": True},
        ),
    ):
        result = asyncio.run(
            scrape_source(
                SOURCE,
                accept_result=lambda candidate: candidate.backend == "firecrawl",
            )
        )

    assert result.backend == "firecrawl"


def test_custom_json_acceptance_is_shared_by_the_provider_chain() -> None:
    from app.hsreplay_card_periods import _accept_card_period_json

    payload = '{"series":{"data":[{"dbfId":1,"includedPopularity":0.1}]}}'
    source = Source(
        id="hsreplay_cards_wild_legend_patch",
        url="https://hsreplay.net/analytics/query/card_list/",
        site="hsreplay",
        category="ranked_cards",
    )

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(html=payload, source=source),
        ),
        patch("app.firecrawl_backend.acquire_firecrawl_key") as firecrawl,
    ):
        result = _scrape_sync(
            source,
            formats=["rawHtml"],
            brightdata_accept_html=_accept_card_period_json,
            brightdata_render=False,
        )

    assert result.backend == "scrape_do"
    assert result.html == payload
    firecrawl.assert_not_called()


def test_screenshot_only_result_is_accepted_without_html_padding() -> None:
    screenshot = valid_png_data_uri()
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(html="", screenshot=screenshot),
        ),
        patch("app.firecrawl_backend.acquire_firecrawl_key") as firecrawl,
    ):
        result = _scrape_sync(
            SOURCE,
            formats=[{"type": "screenshot", "fullPage": True}],
        )

    assert result.screenshot == screenshot
    firecrawl.assert_not_called()


@pytest.mark.parametrize(
    "invalid_screenshot",
    [
        "not-an-image",
        "http://screenshots.example.test/capture.png",
        "https://127.0.0.1/capture.png",
        "https://user:password@example.com/capture.png",
        "data:image/png;base64,c25hcHNob3Q=",
        "data:image/png;base64,%%%not-base64%%%",
    ],
)
def test_invalid_screenshot_does_not_stop_provider_fallback(
    invalid_screenshot: str,
) -> None:
    valid_screenshot = valid_png_data_uri()
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(
                html=valid_html("first"),
                screenshot=invalid_screenshot,
            ),
        ),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            return_value=firecrawl_lease(),
        ),
        patch(
            "app.firecrawl_backend._scrape_once",
            return_value=type(
                "Scraped",
                (),
                {
                    "html": "",
                    "markdown": "",
                    "screenshot": valid_screenshot,
                    "metadata": {"backend": "firecrawl", "creditsUsed": 1},
                    "status_code": 200,
                    "final_url": SOURCE.url,
                },
            )(),
        ),
        patch(
            "app.firecrawl_backend.record_firecrawl_credits",
            return_value={"ok": True},
        ),
    ):
        result = _scrape_sync(
            SOURCE,
            formats=[{"type": "screenshot", "fullPage": True}],
        )

    assert result.backend == "firecrawl"
    assert result.screenshot == valid_screenshot


def test_screenshot_request_without_image_continues_provider_fallback() -> None:
    valid_screenshot = valid_png_data_uri()
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(html=valid_html("html only")),
        ),
        patch(
            "app.firecrawl_backend.acquire_firecrawl_key",
            return_value=firecrawl_lease(),
        ),
        patch(
            "app.firecrawl_backend._scrape_once",
            return_value=type(
                "Scraped",
                (),
                {
                    "html": "",
                    "markdown": "",
                    "screenshot": valid_screenshot,
                    "metadata": {"backend": "firecrawl", "creditsUsed": 1},
                    "status_code": 200,
                    "final_url": SOURCE.url,
                },
            )(),
        ),
        patch(
            "app.firecrawl_backend.record_firecrawl_credits",
            return_value={"ok": True},
        ),
    ):
        result = _scrape_sync(
            SOURCE,
            formats=[{"type": "screenshot", "fullPage": True}],
        )

    assert result.backend == "firecrawl"
    assert result.screenshot == valid_screenshot


def test_https_screenshot_is_downloaded_validated_and_returned_inline() -> None:
    screenshot_bytes = valid_png_bytes()
    screenshot_url = "https://screenshots.example.test/capture.png"
    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch(
            "app.firecrawl_backend.scrape_url_sync",
            return_value=scrape_do_result(html="", screenshot=screenshot_url),
        ),
        patch(
            "app.firecrawl_backend._download_https_screenshot",
            return_value=screenshot_bytes,
        ) as download,
        patch("app.firecrawl_backend.acquire_firecrawl_key") as firecrawl,
    ):
        result = _scrape_sync(
            SOURCE,
            formats=[{"type": "screenshot", "fullPage": True}],
        )

    assert result.screenshot == valid_png_data_uri()
    download.assert_called_once_with(screenshot_url)
    firecrawl.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        {"headers": {"Cookie": "session=secret"}},
        {"headers": {"Authorization": "Bearer secret"}},
        {"headers": {}},
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
                    html=valid_html("real page"),
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
