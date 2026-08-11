from __future__ import annotations

import asyncio
import os
import sys
from types import ModuleType, SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.fetch_routes import source_has_hsreplay_scrape_do_json_route
from app.fetcher import (
    _RefreshProxyCircuit,
    _run_tier_parallel,
    _run_tier_serial_browser,
)
from app.proxy_errors import ProxyPaymentRequiredError
from app.scrapers.curl_impersonate import _fetch_sync
from app.scrapers.http_resilience import resilient_http_get
from app.scrapers.proxy import check_proxy_health
from app.scrapers.rotator import fetch_html, reset_backend_circuits
from app.sources import SOURCE_BY_ID, Source


@pytest.fixture(autouse=True)
def _reset_generic_proxy_circuit() -> None:
    reset_backend_circuits()
    yield
    reset_backend_circuits()


class _FailingAsyncClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> object:
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("status", [402, 407])
def test_generic_curl_connect_rejection_stops_after_one_attempt(status: int) -> None:
    calls = 0

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"CONNECT tunnel failed, response {status}")

    curl_module = ModuleType("curl_cffi")
    curl_module.requests = SimpleNamespace(get=get)  # type: ignore[attr-defined]
    source = Source(
        id="generic-connect-rejection",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    with (
        patch.dict(sys.modules, {"curl_cffi": curl_module}),
        patch("app.scrapers.curl_impersonate.assert_proxy_configured"),
        patch("app.scrapers.curl_impersonate.http_retry_attempts", return_value=4),
        patch(
            "app.scrapers.curl_impersonate.proxy_url_for_source",
            return_value="http://proxy.invalid:1234",
        ),
        pytest.raises(ProxyPaymentRequiredError) as caught,
    ):
        _fetch_sync(source)

    assert caught.value.status_code == status
    assert calls == 1


@pytest.mark.parametrize("status", [402, 407])
def test_origin_status_text_does_not_open_proxy_fail_fast(status: int) -> None:
    calls = 0

    class OriginResponse:
        status_code = status
        text = "ordinary origin response"
        url = "https://example.test/data"

        @staticmethod
        def raise_for_status() -> None:
            raise RuntimeError(f"origin returned HTTP {status}")

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return OriginResponse()

    curl_module = ModuleType("curl_cffi")
    curl_module.requests = SimpleNamespace(get=get)  # type: ignore[attr-defined]
    source = Source(
        id="origin-status",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    with (
        patch.dict(sys.modules, {"curl_cffi": curl_module}),
        patch("app.scrapers.curl_impersonate.assert_proxy_configured"),
        patch("app.scrapers.curl_impersonate.http_retry_attempts", return_value=2),
        patch(
            "app.scrapers.curl_impersonate.proxy_url_for_source",
            return_value="http://proxy.invalid:1234",
        ),
        patch("app.scrapers.curl_impersonate.log_http_error"),
        patch("app.scrapers.curl_impersonate.time.sleep"),
        pytest.raises(
            RuntimeError,
            match=f"origin returned HTTP {status}",
        ) as caught,
    ):
        _fetch_sync(source)

    assert not isinstance(caught.value, ProxyPaymentRequiredError)
    assert calls == 2


def test_httpx_connect_rejection_stops_resilient_retries() -> None:
    client = _FailingAsyncClient(httpx.ProxyError("402 Payment Required"))
    with (
        patch(
            "app.scrapers.http_resilience.httpx.AsyncClient",
            return_value=client,
        ),
        pytest.raises(ProxyPaymentRequiredError) as caught,
    ):
        asyncio.run(
            resilient_http_get(
                "https://example.test/data",
                client_kwargs={"proxy": "http://proxy.invalid:1234"},
                max_attempts=4,
            )
        )

    assert caught.value.status_code == 402
    assert client.calls == 1


def test_proxy_health_does_not_repeat_connect_rejection() -> None:
    client = _FailingAsyncClient(httpx.ProxyError("407 Proxy Authentication Required"))
    with (
        patch("app.scrapers.proxy.assert_proxy_configured"),
        patch(
            "app.scrapers.proxy.proxy_url_for_source",
            return_value="http://proxy.invalid:1234",
        ),
        patch("app.scrapers.proxy.proxy_check_url", return_value="https://ip.test/"),
        patch("app.scrapers.proxy.httpx.AsyncClient", return_value=client),
        patch("app.scrapers.proxy.log_action"),
        pytest.raises(ProxyPaymentRequiredError) as caught,
    ):
        asyncio.run(check_proxy_health())

    assert caught.value.status_code == 407
    assert client.calls == 1


@pytest.mark.parametrize("status", [402, 407])
def test_bare_origin_status_is_not_typed_as_proxy_error(status: int) -> None:
    message = (
        "402 Payment Required" if status == 402 else "407 Proxy Authentication Required"
    )
    request = httpx.Request("GET", "https://origin.test/data")
    response = httpx.Response(status, request=request)
    origin_error = httpx.HTTPStatusError(message, request=request, response=response)

    from app.proxy_errors import proxy_tunnel_error

    assert proxy_tunnel_error(origin_error, proxy_used=True) is None
    assert proxy_tunnel_error(httpx.ProxyError(message), proxy_used=False) is None


def test_rotator_keeps_proxyless_recovery_after_connect_402() -> None:
    reset_backend_circuits()
    source = Source(
        id="rotator-connect-recovery",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    proxy_backend = AsyncMock(
        side_effect=RuntimeError("CONNECT tunnel failed, response 402")
    )
    recovered = SimpleNamespace(
        html="<html>" + ("valid data " * 300) + "</html>",
        final_url=source.url,
        backend="flaresolverr",
        http_status=200,
    )
    proxyless_backend = AsyncMock(return_value=recovered)

    with (
        patch("app.scrapers.rotator.assert_proxy_configured"),
        patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[
                ("curl_cffi", proxy_backend, lambda: True),
                ("flaresolverr", proxyless_backend, lambda: True),
            ],
        ),
        patch(
            "app.scrapers.rotator.browser_backend_uses_residential_proxy",
            side_effect=lambda _source, backend: backend != "flaresolverr",
        ),
        patch("app.scrapers.rotator.looks_like_real_page", return_value=True),
        patch("app.scrapers.rotator.log_action"),
    ):
        result = asyncio.run(fetch_html(source))

    assert result is recovered
    proxy_backend.assert_awaited_once_with(source)
    proxyless_backend.assert_awaited_once_with(source)
    reset_backend_circuits()


def test_refresh_circuit_crosses_phases_but_keeps_hsreplay_scrape_do() -> None:
    reset_backend_circuits()
    failing = SOURCE_BY_ID["metastats_decks"]
    hsreplay_json = SOURCE_BY_ID["hsreplay_cards_legend_1d"]
    metastats_cloud = SOURCE_BY_ID["metastats_matchups"]
    dependent = SOURCE_BY_ID["hearthstone_decks"]
    circuit = _RefreshProxyCircuit()

    async def fetch(_client: object, source: Source) -> dict[str, object]:
        if source.id == failing.id:
            raise ProxyPaymentRequiredError(
                "Residential proxy CONNECT tunnel unavailable",
                status_code=402,
            )
        return {"source_id": source.id, "state": "ok", "backend": "scrape_do"}

    with (
        patch.dict(
            os.environ,
            {
                "HS_SCRAPE_DO_TOKEN": "configured-for-test",
                "HS_HSREPLAY_JSON_CHANNELS": "curl_cffi,flaresolverr,scrape_do",
                "HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS": "60",
                "HS_HSREPLAY_SCRAPE_DO_MAX_CREDITS": "100",
            },
            clear=False,
        ),
        patch("app.fetch_routes.fetch_direct_enabled", return_value=False),
        patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set()),
        patch("app.fetch_routes.firecrawl_fallback_source_ids", return_value=set()),
        patch("app.fetcher.fetch_source", side_effect=fetch) as fetch_mock,
        patch("app.fetcher._parallel_stagger_delay", new=AsyncMock()),
        patch(
            "app.fetcher._save_failure_status",
            side_effect=lambda _source, status: status,
        ),
        patch("app.fetcher.log_action"),
    ):
        first = asyncio.run(
            _run_tier_parallel(
                [failing],
                phase="light_api",
                concurrency=1,
                client=None,
                proxy_info={},
                proxy_circuit=circuit,
            )
        )
        second = asyncio.run(
            _run_tier_parallel(
                [hsreplay_json, metastats_cloud],
                phase="medium_api",
                concurrency=1,
                client=None,
                proxy_info={},
                proxy_circuit=circuit,
            )
        )
        third = asyncio.run(
            _run_tier_serial_browser(
                [dependent],
                phase="browser_protected",
                client=None,
                proxy_info={},
                use_flaresolverr=False,
                apply_delay=False,
                proxy_circuit=circuit,
            )
        )

    assert first[0]["failure_class"] == "proxy_407"
    assert first[0]["proxy_status"] == 402
    assert second[0]["state"] == "ok"
    assert second[1]["state"] == "ok"
    assert third[0]["failure_class"] == "proxy_407"
    assert third[0]["proxy_status"] == 402
    assert [call.args[1].id for call in fetch_mock.call_args_list] == [
        failing.id,
        hsreplay_json.id,
        metastats_cloud.id,
    ]


def test_scrape_do_capability_is_limited_to_hsreplay_json_sources() -> None:
    json_source = SOURCE_BY_ID["hsreplay_cards_legend_1d"]
    html_source = SOURCE_BY_ID["hsreplay_decks_trending"]
    with patch.dict(
        os.environ,
        {
            "HS_SCRAPE_DO_TOKEN": "configured-for-test",
            "HS_HSREPLAY_JSON_CHANNELS": "scrape_do",
            "HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS": "60",
            "HS_HSREPLAY_SCRAPE_DO_MAX_CREDITS": "100",
        },
        clear=False,
    ):
        assert source_has_hsreplay_scrape_do_json_route(json_source)
        assert not source_has_hsreplay_scrape_do_json_route(html_source)
