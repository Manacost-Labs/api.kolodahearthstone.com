from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.hsreplay_client import (
    HsReplayScrapeDoBudgetError,
    _channel_urls,
    _fetch_text_via_curl_cffi_sync,
    _fetch_text_via_scrape_do,
    consume_hsreplay_json_transport_backend,
    fetch_hsreplay_json,
    fetch_text_via_curl_cffi,
    hsreplay_proxy_circuit_is_open,
    reset_hsreplay_refresh_state,
)
from app.proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from app.scrape_do_backend import ScrapeDoRequestError, ScrapeDoScrape
from app.scrapers.rotator import (
    reset_backend_circuits,
    residential_proxy_circuit_error,
)


@pytest.fixture(autouse=True)
def _reset_transport_state() -> None:
    reset_hsreplay_refresh_state()
    reset_backend_circuits()
    yield
    reset_hsreplay_refresh_state()
    reset_backend_circuits()


@pytest.mark.parametrize("status", [402, 407])
def test_only_actual_proxy_connect_failures_are_typed(status: int) -> None:
    message = f"CONNECT tunnel failed, response {status}"

    detected = proxy_tunnel_error(httpx.ProxyError(message), proxy_used=True)

    assert isinstance(detected, ProxyPaymentRequiredError)
    assert detected.status_code == status
    assert proxy_tunnel_error(RuntimeError(message), proxy_used=False) is None
    assert (
        proxy_tunnel_error(RuntimeError(f"origin returned {status}"), proxy_used=True)
        is None
    )


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("402 Payment Required", 402),
        ("407 Proxy Authentication Required", 407),
    ],
)
def test_httpcore_canonical_proxy_tunnel_errors_are_typed(
    message: str,
    status: int,
) -> None:
    detected = proxy_tunnel_error(httpx.ProxyError(message), proxy_used=True)

    assert isinstance(detected, ProxyPaymentRequiredError)
    assert detected.status_code == status
    assert proxy_tunnel_error(httpx.ProxyError(message), proxy_used=False) is None
    assert (
        proxy_tunnel_error(
            httpx.ProxyError(f"origin returned {message}"),
            proxy_used=True,
        )
        is None
    )
    assert proxy_tunnel_error(RuntimeError(message), proxy_used=True) is None


def test_proxy_failure_skips_proxy_channels_then_uses_scrape_do() -> None:
    calls: list[str] = []

    async def fetch(label: str, _url: str, *, source_id: str) -> str:
        del source_id
        calls.append(label)
        if label == "curl_cffi":
            raise ProxyPaymentRequiredError(
                "Residential proxy CONNECT rejected the request",
                status_code=402,
            )
        if label == "scrape_do":
            return '{"series": {"data": [1]}}'
        raise AssertionError("proxy-backed fallback must be skipped")

    with (
        patch(
            "app.hsreplay_client._channel_urls",
            return_value=[
                ("curl_cffi", "https://hsreplay.net/api/test"),
                ("flaresolverr", "https://hsreplay.net/api/test"),
                ("scrape_do", "https://hsreplay.net/api/test"),
            ],
        ),
        patch("app.hsreplay_client._fetch_body_for_channel", side_effect=fetch),
        patch(
            "app.hsreplay_client._channel_uses_residential_proxy",
            side_effect=lambda label, *_: label != "scrape_do",
        ),
        patch("app.hsreplay_client.api_json_retry_delay_seconds", return_value=0),
    ):
        result = asyncio.run(
            fetch_hsreplay_json(
                "https://hsreplay.net/api/test",
                source_id="hsreplay_cards_legend_patch",
            )
        )

    assert result == {"series": {"data": [1]}}
    assert calls == ["curl_cffi", "scrape_do"]
    assert hsreplay_proxy_circuit_is_open()


def test_independent_provider_failure_is_not_misreported_as_proxy_failure() -> None:
    async def fetch(label: str, _url: str, *, source_id: str) -> str:
        del source_id
        if label == "curl_cffi":
            raise ProxyPaymentRequiredError(
                "Residential proxy CONNECT rejected the request",
                status_code=402,
            )
        raise RuntimeError("independent provider unavailable")

    with (
        patch(
            "app.hsreplay_client._channel_urls",
            return_value=[
                ("curl_cffi", "https://hsreplay.net/api/test"),
                ("scrape_do", "https://hsreplay.net/api/test"),
            ],
        ),
        patch("app.hsreplay_client._fetch_body_for_channel", side_effect=fetch),
        patch(
            "app.hsreplay_client._channel_uses_residential_proxy",
            side_effect=lambda label, *_: label != "scrape_do",
        ),
        patch("app.hsreplay_client.api_json_retry_delay_seconds", return_value=0),
        pytest.raises(RuntimeError, match="RuntimeError") as caught,
    ):
        asyncio.run(
            fetch_hsreplay_json(
                "https://hsreplay.net/api/test",
                source_id="hsreplay_cards_legend_patch",
            )
        )

    assert not isinstance(caught.value, ProxyPaymentRequiredError)


@pytest.mark.parametrize("status", [402, 407])
def test_curl_connect_failure_has_no_retry_and_opens_circuit(status: int) -> None:
    calls = 0

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"CONNECT tunnel failed, response {status}")

    curl_module = ModuleType("curl_cffi")
    curl_module.requests = SimpleNamespace(get=get)  # type: ignore[attr-defined]
    with (
        patch.dict(sys.modules, {"curl_cffi": curl_module}),
        patch("app.hsreplay_client.assert_proxy_configured"),
        patch("app.hsreplay_client.http_retry_attempts", return_value=4),
        patch(
            "app.hsreplay_client.proxy_url_for_source",
            return_value="http://proxy.invalid:1234",
        ),
    ):
        with pytest.raises(ProxyPaymentRequiredError) as caught:
            _fetch_text_via_curl_cffi_sync(
                "https://hsreplay.net/api/test",
                "hsreplay_cards_legend_patch",
            )

    assert caught.value.status_code == status
    assert calls == 1
    assert hsreplay_proxy_circuit_is_open()


def test_reset_closes_proxy_circuit() -> None:
    from app.hsreplay_client import record_hsreplay_proxy_failure

    record_hsreplay_proxy_failure(
        ProxyPaymentRequiredError("proxy unavailable", status_code=407)
    )
    assert hsreplay_proxy_circuit_is_open()

    reset_hsreplay_refresh_state()

    assert not hsreplay_proxy_circuit_is_open()


def test_hsreplay_proxy_failure_opens_refresh_wide_circuit() -> None:
    from app.hsreplay_client import record_hsreplay_proxy_failure

    error = ProxyPaymentRequiredError("proxy unavailable", status_code=402)
    record_hsreplay_proxy_failure(error)

    assert residential_proxy_circuit_error() is error


def test_curl_thread_proxy_failure_opens_parent_refresh_circuit() -> None:
    error = ProxyPaymentRequiredError("proxy unavailable", status_code=402)
    with (
        patch(
            "app.hsreplay_client._fetch_text_via_curl_cffi_sync",
            side_effect=error,
        ),
        pytest.raises(ProxyPaymentRequiredError),
    ):
        asyncio.run(
            fetch_text_via_curl_cffi(
                "https://hsreplay.net/api/test",
                source_id="test",
            )
        )

    assert residential_proxy_circuit_error() is error


def test_archetype_dictionary_uses_json_channel_cascade() -> None:
    from app.hsreplay_meta_api import ARCHETYPE_DICT_URL, _archetype_name_map

    payload = {
        "data": [
            {"id": 42, "name": "Test Archetype", "url": "/archetypes/42/"},
            {"name": "missing id"},
        ]
    }
    fetch = AsyncMock(return_value=payload)
    with patch("app.hsreplay_meta_api.fetch_hsreplay_json", fetch):
        result = asyncio.run(_archetype_name_map("dictionary-test"))

    assert result == {
        42: {"id": 42, "name": "Test Archetype", "url": "/archetypes/42/"}
    }
    fetch.assert_awaited_once_with(
        ARCHETYPE_DICT_URL,
        source_id="dictionary-test",
        cache_key="hsreplay:archetype-dictionary:ru",
    )


def test_scrape_do_retries_temporary_target_rejection_then_succeeds() -> None:
    scrape = AsyncMock(
        side_effect=[
            ScrapeDoRequestError("temporary target rejection", status_code=403),
            ScrapeDoScrape(
                html='{"ok": true}',
                status_code=200,
                final_url="https://hsreplay.net/api/test",
                request_cost=1,
                credits_remaining=100,
                super_proxy=False,
            ),
        ]
    )
    with (
        patch("app.hsreplay_client.scrape_url", scrape),
        patch("app.hsreplay_client.api_json_attempts_per_channel", return_value=2),
        patch("app.hsreplay_client.api_json_retry_delay_seconds", return_value=0),
        patch("app.hsreplay_client.hsreplay_cookies_for_fetch", return_value=[]),
    ):
        body = asyncio.run(
            _fetch_text_via_scrape_do(
                "https://hsreplay.net/api/test",
                source_id="retry-test",
            )
        )

    assert body == '{"ok": true}'
    assert scrape.await_count == 2


def test_scrape_do_retry_after_is_bounded() -> None:
    scrape = AsyncMock(
        side_effect=[
            ScrapeDoRequestError(
                "temporary throttling",
                status_code=429,
                retry_after_seconds=3600,
            ),
            ScrapeDoScrape(
                html='{"ok": true}',
                status_code=200,
                final_url="https://hsreplay.net/api/test",
                request_cost=1,
                credits_remaining=100,
                super_proxy=False,
            ),
        ]
    )
    sleep = AsyncMock()
    with (
        patch("app.hsreplay_client.scrape_url", scrape),
        patch("app.hsreplay_client.api_json_attempts_per_channel", return_value=2),
        patch("app.hsreplay_client.asyncio.sleep", sleep),
        patch("app.hsreplay_client.hsreplay_cookies_for_fetch", return_value=[]),
    ):
        asyncio.run(
            _fetch_text_via_scrape_do(
                "https://hsreplay.net/api/test",
                source_id="bounded-retry-test",
            )
        )

    sleep.assert_awaited_once_with(30.0)


def test_hsreplay_json_records_successful_scrape_do_transport() -> None:
    with (
        patch(
            "app.hsreplay_client._channel_urls",
            return_value=[("scrape_do", "https://hsreplay.net/api/test")],
        ),
        patch(
            "app.hsreplay_client._fetch_body_for_channel",
            new=AsyncMock(return_value='{"ok": true}'),
        ),
        patch("app.hsreplay_client.get_cached_hsreplay_json", return_value=None),
        patch("app.hsreplay_client.set_cached_hsreplay_json"),
    ):
        asyncio.run(
            fetch_hsreplay_json(
                "https://hsreplay.net/api/test",
                source_id="transport-test",
            )
        )

    assert consume_hsreplay_json_transport_backend("transport-test") == "scrape_do"


def test_scrape_do_does_not_retry_account_failure() -> None:
    scrape = AsyncMock(
        side_effect=ScrapeDoRequestError("account rejected", status_code=401)
    )
    with (
        patch("app.hsreplay_client.scrape_url", scrape),
        patch("app.hsreplay_client.api_json_attempts_per_channel", return_value=3),
        patch("app.hsreplay_client.hsreplay_cookies_for_fetch", return_value=[]),
        pytest.raises(ScrapeDoRequestError),
    ):
        asyncio.run(
            _fetch_text_via_scrape_do(
                "https://hsreplay.net/api/test",
                source_id="no-retry-test",
            )
        )

    scrape.assert_awaited_once()


def test_concurrent_refresh_contexts_do_not_reset_each_other() -> None:
    from app.hsreplay_client import record_hsreplay_proxy_failure

    async def run() -> tuple[bool, bool, bool]:
        first_ready = asyncio.Event()
        second_done = asyncio.Event()

        async def first_refresh() -> tuple[bool, bool]:
            reset_hsreplay_refresh_state()
            record_hsreplay_proxy_failure(
                ProxyPaymentRequiredError("first proxy failed", status_code=402)
            )
            first_ready.set()
            await second_done.wait()
            return True, hsreplay_proxy_circuit_is_open()

        async def second_refresh() -> bool:
            await first_ready.wait()
            reset_hsreplay_refresh_state()
            isolated = not hsreplay_proxy_circuit_is_open()
            second_done.set()
            return isolated

        first, second = await asyncio.gather(first_refresh(), second_refresh())
        return first[0], first[1], second

    first_started, first_still_open, second_isolated = asyncio.run(run())

    assert first_started
    assert first_still_open
    assert second_isolated


def test_refresh_start_resets_hsreplay_transport_state() -> None:
    from app.fetcher import _refresh_sources_unlocked

    with (
        patch("app.fetcher.validate_tier_registry"),
        patch("app.fetcher.SOURCES", []),
        patch("app.fetcher.log_action"),
        patch("app.fetcher._record_reliability_results_best_effort"),
        patch("app.refresh_context.begin_refresh_run"),
        patch("app.refresh_context.end_refresh_run"),
        patch("app.ai_review.reset_ai_review_budget"),
        patch("app.scrapers.rotator.reset_backend_circuits"),
        patch("app.hsreplay_client.reset_hsreplay_refresh_state") as reset,
        patch(
            "app.preflight.ensure_refresh_preflight",
            new=AsyncMock(side_effect=RuntimeError("stop after reset")),
        ),
        patch("app.preflight.selection_needs_proxy_preflight", return_value=False),
        patch(
            "app.preflight.selection_needs_flaresolverr_preflight", return_value=False
        ),
    ):
        with pytest.raises(RuntimeError, match="stop after reset"):
            asyncio.run(_refresh_sources_unlocked())

    reset.assert_called_once_with()


def test_scrape_do_json_is_non_rendered_and_forwards_only_scoped_cookie() -> None:
    scrape = AsyncMock(
        return_value=SimpleNamespace(
            html='{"ok": true}',
            request_cost=1,
            status_code=200,
            content_length=12,
            final_url="https://hsreplay.net/analytics/query/card_list/",
        )
    )
    cookies = [
        {"name": "sessionid", "value": "private-value", "domain": ".hsreplay.net"},
        {"name": "foreign", "value": "must-not-leak", "domain": ".example.com"},
    ]

    with (
        patch("app.hsreplay_client.scrape_url", scrape),
        patch("app.hsreplay_client.hsreplay_cookies_for_fetch", return_value=cookies),
    ):
        body = asyncio.run(
            _fetch_text_via_scrape_do(
                "https://hsreplay.net/analytics/query/card_list/",
                source_id="hsreplay_cards_legend_patch",
            )
        )

    assert body == '{"ok": true}'
    kwargs = scrape.await_args.kwargs
    assert kwargs["render"] is False
    assert kwargs["super_proxy"] is False
    assert kwargs["forward_headers"] is False
    assert kwargs["headers"] == {"Cookie": "sessionid=private-value"}


@pytest.mark.parametrize(
    "url",
    [
        "http://hsreplay.net/api/test",
        "https://www.hsreplay.net/api/test",
        "https://hsreplay.net.evil.invalid/api/test",
        "https://user@hsreplay.net/api/test",
    ],
)
def test_scrape_do_host_guard_rejects_non_exact_https_target(url: str) -> None:
    scrape = AsyncMock()
    with patch("app.hsreplay_client.scrape_url", scrape):
        with pytest.raises(ValueError, match="HSReplay HTTPS"):
            asyncio.run(_fetch_text_via_scrape_do(url, source_id="test"))
    scrape.assert_not_awaited()


@pytest.mark.parametrize(
    "limit_name",
    [
        "HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS",
        "HS_HSREPLAY_SCRAPE_DO_MAX_CREDITS",
    ],
)
def test_scrape_do_budget_exhaustion_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
) -> None:
    monkeypatch.setenv(limit_name, "0")
    reset_hsreplay_refresh_state()
    scrape = AsyncMock()

    with patch("app.hsreplay_client.scrape_url", scrape):
        with pytest.raises(HsReplayScrapeDoBudgetError):
            asyncio.run(
                _fetch_text_via_scrape_do(
                    "https://hsreplay.net/api/test",
                    source_id="test",
                )
            )

    scrape.assert_not_awaited()


def test_scrape_do_concurrency_cap_is_refresh_scoped_and_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_HSREPLAY_SCRAPE_DO_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS", "10")
    monkeypatch.setenv("HS_HSREPLAY_SCRAPE_DO_MAX_CREDITS", "10")
    reset_hsreplay_refresh_state()
    active = 0
    maximum_active = 0

    async def scrape(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(
            html='{"ok": true}',
            request_cost=1,
            status_code=200,
            content_length=12,
            final_url="https://hsreplay.net/api/test",
        )

    async def run() -> None:
        await asyncio.gather(
            *(
                _fetch_text_via_scrape_do(
                    f"https://hsreplay.net/api/test/{index}",
                    source_id=f"test_{index}",
                )
                for index in range(5)
            )
        )

    with patch("app.hsreplay_client.scrape_url", side_effect=scrape):
        asyncio.run(run())

    assert maximum_active == 2


def test_failed_scrape_do_call_keeps_its_budget_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS", "1")
    reset_hsreplay_refresh_state()
    scrape = AsyncMock(side_effect=RuntimeError("provider rejected request"))

    async def run() -> None:
        with pytest.raises(RuntimeError, match="provider rejected"):
            await _fetch_text_via_scrape_do(
                "https://hsreplay.net/api/first",
                source_id="test",
            )
        with pytest.raises(HsReplayScrapeDoBudgetError):
            await _fetch_text_via_scrape_do(
                "https://hsreplay.net/api/second",
                source_id="test",
            )

    with patch("app.hsreplay_client.scrape_url", scrape):
        asyncio.run(run())

    assert scrape.await_count == 1


def test_invalid_scrape_do_payload_does_not_become_success() -> None:
    with (
        patch(
            "app.hsreplay_client._channel_urls",
            return_value=[("scrape_do", "https://hsreplay.net/api/test")],
        ),
        patch(
            "app.hsreplay_client._fetch_body_for_channel",
            new=AsyncMock(return_value="<html>login</html>"),
        ),
        patch("app.hsreplay_client.api_json_retry_delay_seconds", return_value=0),
    ):
        with pytest.raises(RuntimeError, match="payload is not JSON"):
            asyncio.run(
                fetch_hsreplay_json(
                    "https://hsreplay.net/api/test",
                    source_id="test",
                )
            )


def test_preferred_and_configured_channels_are_merged_without_duplicates() -> None:
    with (
        patch(
            "app.source_contracts.preferred_channels_for_source",
            return_value=("curl_cffi", "flaresolverr"),
        ),
        patch(
            "app.hsreplay_client.hsreplay_json_channels",
            return_value=["flaresolverr", "scrape_do", "direct"],
        ),
    ):
        labels = [
            label
            for label, _ in _channel_urls(
                "https://hsreplay.net/api/test",
                source_id="hsreplay_cards_legend_patch",
            )
        ]

    assert labels == ["curl_cffi", "flaresolverr", "scrape_do", "direct"]


def test_scrape_do_logs_never_include_cookie_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "cookie-secret-that-must-not-appear"
    scrape = AsyncMock(side_effect=RuntimeError("provider failed"))
    with (
        patch("app.hsreplay_client.scrape_url", scrape),
        patch(
            "app.hsreplay_client._channel_urls",
            return_value=[("scrape_do", "https://hsreplay.net/api/test")],
        ),
        patch(
            "app.hsreplay_client.hsreplay_cookies_for_fetch",
            return_value=[
                {"name": "sessionid", "value": secret, "domain": "hsreplay.net"}
            ],
        ),
        caplog.at_level(logging.WARNING),
    ):
        with pytest.raises(RuntimeError):
            asyncio.run(
                fetch_hsreplay_json(
                    "https://hsreplay.net/api/test",
                    source_id="test",
                )
            )

    assert secret not in caplog.text
