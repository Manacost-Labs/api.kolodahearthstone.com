"""HSGuru catalog pages ask the cheapest Scrape.do profile that works."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import hsguru_decks
from app.sources import Source


def _source(url: str) -> Source:
    return Source(
        id="hsguru_deck_catalog_standard_all",
        url=url,
        site="hsguru",
        category="deck_catalog",
    )


def _result(url: str, html: str, credits: int) -> SimpleNamespace:
    return SimpleNamespace(
        html=html,
        status_code=200,
        final_url=url,
        backend="scrape_do",
        request_credits=credits,
    )


DECKS_URL = "https://www.hsguru.com/decks?format=2&rank=all&period=black_empire"
REPLAYS_URL = "https://www.hsguru.com/replays?format=2&period=black_empire"


def test_deck_page_asks_plain_first_and_stops_when_it_is_valid() -> None:
    hsguru_decks.reset_hsguru_catalog_provider_state()
    scrape = AsyncMock(
        return_value=_result(DECKS_URL, '<div id="deck_stats_viewport"></div>', 1)
    )
    with patch.object(hsguru_decks, "scrape_source_with_options", scrape):
        page = asyncio.run(
            hsguru_decks._fetch_catalog_page(
                _source(DECKS_URL), max_age_ms=0, wait_ms=0, timeout_ms=90_000
            )
        )

    assert page.backend == "scrape_do"
    assert scrape.await_count == 1
    options = scrape.await_args.kwargs
    assert options["scrape_do_profiles"] == ((False, False),)
    # A plain page that hangs is given up after 30 s, not the page timeout.
    assert options["timeout_ms"] == 30_000
    assert {"decodo", "brightdata", "firecrawl", "scrapfly"} <= options[
        "skip_providers"
    ]


def test_plain_step_never_outlasts_a_shorter_caller_timeout() -> None:
    hsguru_decks.reset_hsguru_catalog_provider_state()
    scrape = AsyncMock(
        return_value=_result(DECKS_URL, '<div id="deck_stats_viewport"></div>', 1)
    )
    with patch.object(hsguru_decks, "scrape_source_with_options", scrape):
        asyncio.run(
            hsguru_decks._fetch_catalog_page(
                _source(DECKS_URL), max_age_ms=0, wait_ms=0, timeout_ms=25_000
            )
        )

    assert scrape.await_args.kwargs["timeout_ms"] == 25_000


def test_replay_page_goes_straight_to_one_rendered_request() -> None:
    hsguru_decks.reset_hsguru_catalog_provider_state()
    async def scrape(source, **_kwargs):
        return _result(source.url, '<table id="replays_table"></table>', 5)

    scrape = AsyncMock(side_effect=scrape)
    with patch.object(hsguru_decks, "scrape_source_with_options", scrape):
        asyncio.run(
            hsguru_decks._fetch_replay_decks(
                "standard",
                "Tyrande Priest",
                period="black_empire",
                windows=("black_empire",),
            )
        )

    # The plain page only says "Loading replays..."; its table needs a browser.
    assert [call.kwargs["scrape_do_profiles"] for call in scrape.await_args_list] == [
        ((True, False),)
    ]


def test_failed_page_waits_with_its_slot_released_then_retries(monkeypatch) -> None:
    monkeypatch.setenv("HS_HSGURU_DECK_CATALOG_CONCURRENCY", "1")
    hsguru_decks.reset_hsguru_catalog_provider_state()
    page = hsguru_decks._CatalogPage(html="", backend="scrape_do", request_credits=1)
    fetch = AsyncMock(side_effect=[RuntimeError("all providers failed"), page])
    slot_free_during_pause: list[bool] = []

    async def pause(_delay: float) -> None:
        slot_free_during_pause.append(not hsguru_decks._slots("catalog", 1).locked())

    with (
        patch.object(hsguru_decks, "_fetch_catalog_page", fetch),
        patch.object(hsguru_decks.asyncio, "sleep", side_effect=pause),
    ):
        result = asyncio.run(
            hsguru_decks._fetch_catalog_page_with_backoff(_source(DECKS_URL))
        )

    assert result is page
    assert fetch.await_count == 2
    assert slot_free_during_pause == [True]


def test_page_failing_after_every_pause_raises() -> None:
    hsguru_decks.reset_hsguru_catalog_provider_state()
    fetch = AsyncMock(side_effect=RuntimeError("all providers failed"))
    sleep = AsyncMock()
    with (
        patch.object(hsguru_decks, "_fetch_catalog_page", fetch),
        patch.object(hsguru_decks.asyncio, "sleep", sleep),
        pytest.raises(RuntimeError, match="all providers failed"),
    ):
        asyncio.run(hsguru_decks._fetch_catalog_page_with_backoff(_source(DECKS_URL)))

    assert fetch.await_count == len(hsguru_decks._PAGE_RETRY_DELAYS_SECONDS) + 1
    assert [call.args[0] for call in sleep.await_args_list] == list(
        hsguru_decks._PAGE_RETRY_DELAYS_SECONDS
    )


def _rejected(url: str, credits: int) -> SimpleNamespace:
    return _result(url, "<html>dead render without the table</html>", credits)


def _observing_scrape(results: list[SimpleNamespace]) -> AsyncMock:
    """Scrape.do that reports each billed response as rejected, then fails."""
    pending = list(results)

    async def scrape(_source, *, attempt_observer, accept_result, **_kwargs):
        result = pending.pop(0)
        attempt_observer(result, accept_result(result))
        raise RuntimeError("Scrape.do response failed content validation")

    return AsyncMock(side_effect=scrape)


def test_billed_rejections_stop_the_page_after_one_paid_retry(monkeypatch) -> None:
    monkeypatch.setattr(hsguru_decks, "fetch_via_flaresolverr", AsyncMock(
        side_effect=RuntimeError("no local solver")
    ))
    hsguru_decks.reset_hsguru_catalog_provider_state()
    scrape = _observing_scrape([_rejected(DECKS_URL, 1), _rejected(DECKS_URL, 5)])
    sleep = AsyncMock()
    with (
        patch.object(hsguru_decks, "scrape_source_with_options", scrape),
        patch.object(hsguru_decks.asyncio, "sleep", sleep),
        pytest.raises(RuntimeError),
    ):
        asyncio.run(
            hsguru_decks._fetch_catalog_page_with_backoff(
                _source(DECKS_URL), max_age_ms=0, wait_ms=0, timeout_ms=90_000
            )
        )

    # Plain (1 credit) and its rendered retry (5): no further paid round.
    assert scrape.await_count == 2
    sleep.assert_not_awaited()


def test_replay_page_gets_one_paid_retry_after_a_pause(monkeypatch) -> None:
    monkeypatch.setattr(hsguru_decks, "fetch_via_flaresolverr", AsyncMock(
        side_effect=RuntimeError("no local solver")
    ))
    hsguru_decks.reset_hsguru_catalog_provider_state()
    scrape = _observing_scrape(
        [_rejected(REPLAYS_URL, 5), _rejected(REPLAYS_URL, 5)]
    )
    sleep = AsyncMock()
    with (
        patch.object(hsguru_decks, "scrape_source_with_options", scrape),
        patch.object(hsguru_decks.asyncio, "sleep", sleep),
        pytest.raises(RuntimeError),
    ):
        asyncio.run(
            hsguru_decks._fetch_catalog_page_with_backoff(
                _source(REPLAYS_URL),
                max_age_ms=0,
                wait_ms=0,
                timeout_ms=90_000,
                marker=hsguru_decks._REPLAYS_PAGE_MARKER,
                scrape_do_steps=hsguru_decks._REPLAYS_SCRAPE_DO_STEPS,
            )
        )

    assert scrape.await_count == 2
    assert sleep.await_count == 1


def test_unbilled_failures_keep_every_pause(monkeypatch) -> None:
    monkeypatch.setattr(hsguru_decks, "fetch_via_flaresolverr", AsyncMock(
        side_effect=RuntimeError("no local solver")
    ))
    hsguru_decks.reset_hsguru_catalog_provider_state()
    scrape = AsyncMock(side_effect=RuntimeError("Scrape.do HTTP 502"))
    sleep = AsyncMock()
    with (
        patch.object(hsguru_decks, "scrape_source_with_options", scrape),
        patch.object(hsguru_decks.asyncio, "sleep", sleep),
        patch.object(hsguru_decks, "_scrape_do_circuit_open", return_value=False),
        pytest.raises(RuntimeError),
    ):
        asyncio.run(
            hsguru_decks._fetch_catalog_page_with_backoff(
                _source(REPLAYS_URL),
                max_age_ms=0,
                wait_ms=0,
                timeout_ms=90_000,
                marker=hsguru_decks._REPLAYS_PAGE_MARKER,
                scrape_do_steps=hsguru_decks._REPLAYS_SCRAPE_DO_STEPS,
            )
        )

    assert scrape.await_count == len(hsguru_decks._PAGE_RETRY_DELAYS_SECONDS) + 1
