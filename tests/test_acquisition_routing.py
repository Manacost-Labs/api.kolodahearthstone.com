import asyncio
from unittest.mock import Mock

import pytest

from app import firecrawl_backend as backend
from app.browser_acquisition import BrowserAction, BrowserPlan
from app.scrape_do_backend import ScrapeDoScrape
from app.sources import Source


def test_explicit_browser_plan_does_not_fall_back_to_ignoring_provider(monkeypatch):
    plan = BrowserPlan(
        wait_selector="#rows", actions=(BrowserAction("Click", selector="#next"),)
    )
    monkeypatch.setattr(backend, "scrape_do_token", lambda: "fixture-token")
    scrape = Mock(side_effect=RuntimeError("action_failed"))
    fallback = Mock()
    monkeypatch.setattr(backend, "_scrape_via_scrape_do", scrape)
    monkeypatch.setattr(backend, "_scrape_via_firecrawl", fallback)
    with pytest.raises(RuntimeError, match="action_failed"):
        asyncio.run(
            backend.scrape_source_with_options(
                Source("test", "https://example.com", "fixture", "test"),
                browser_plan=plan,
            )
        )
    assert scrape.call_args.kwargs["browser_plan"] is plan
    fallback.assert_not_called()


def test_plan_uses_single_attempt_and_preserves_billing(monkeypatch):
    plan = BrowserPlan(wait_selector="#rows")
    monkeypatch.setattr(backend, "scrape_do_token", lambda: "fixture-token")
    billed = ScrapeDoScrape(
        html="<html>loading</html>",
        status_code=200,
        final_url="https://example.com",
        request_cost=5,
        credits_remaining=100,
        super_proxy=False,
    )
    scrape = Mock(
        side_effect=backend.ScrapeDoContentError("readiness missing", scrape=billed)
    )
    observer = Mock()
    monkeypatch.setattr(backend, "scrape_url_sync", scrape)
    with pytest.raises(RuntimeError):
        backend._scrape_sync(
            Source("test", "https://example.com", "fixture", "test"),
            browser_plan=plan,
            attempt_observer=observer,
        )
    assert scrape.call_count == 1
    assert observer.call_count == 1
    assert observer.call_args.args[1] is False


def test_plan_cannot_silently_skip_scrape_do(monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(backend, "_scrape_via_firecrawl", fetch)
    with pytest.raises(RuntimeError, match="requires Scrape.do"):
        backend._scrape_sync(
            Source("test", "https://example.com", "fixture", "test"),
            browser_plan=BrowserPlan(wait_selector="#x"),
            skip_providers={"scrape_do"},
        )
    fetch.assert_not_called()
