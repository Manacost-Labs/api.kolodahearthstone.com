from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
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
    fetch_source,
)
from app.proxy_errors import ProxyPaymentRequiredError
from app.scrapers.curl_impersonate import _fetch_sync
from app.scrapers.http_resilience import resilient_http_get
from app.scrapers.proxy import check_proxy_health
from app.scrapers.rotator import (
    fetch_html,
    record_residential_proxy_failure,
    reset_backend_circuits,
    residential_proxy_circuit_error,
)
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


def test_api_proxy_402_opens_rotator_circuit_before_browser_fallback() -> None:
    source = SOURCE_BY_ID["vicious_syndicate_radars"]
    proxy_error = ProxyPaymentRequiredError(
        "Residential proxy CONNECT tunnel rejected the request (HTTP 402)",
        status_code=402,
    )

    async def browser_fallback(*_args: object, **_kwargs: object) -> object:
        assert residential_proxy_circuit_error() is proxy_error
        raise proxy_error

    with (
        TemporaryDirectory() as td,
        patch("app.storage.data_dir", return_value=Path(td)),
        patch(
            "app.fetcher._fetch_hsreplay_api_source",
            new=AsyncMock(side_effect=proxy_error),
        ),
        patch("app.fetcher.fetch_html", side_effect=browser_fallback) as browser,
        patch("app.fetcher._try_firecrawl_html", new=AsyncMock(return_value=None)),
        patch("app.fetcher.send_telegram_alert", new=AsyncMock()),
        patch("app.fetcher._maybe_stale_data_alert", new=AsyncMock()),
        patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
        patch("app.fetcher.firecrawl_fallback_source_ids", return_value=set()),
        patch("app.fetcher.log_action"),
    ):
        status = asyncio.run(fetch_source(None, source))

    browser.assert_awaited_once()
    assert status["failure_class"] == "proxy_402"
    assert status["proxy_status"] == 402


def test_rotator_observes_proxy_circuit_opened_between_backends() -> None:
    source = Source(
        id="concurrent-circuit-refresh",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    proxy_error = ProxyPaymentRequiredError(
        "Residential proxy CONNECT tunnel rejected the request (HTTP 402)",
        status_code=402,
    )

    async def open_circuit_then_fail(_source: Source) -> object:
        record_residential_proxy_failure(proxy_error)
        raise RuntimeError("independent proxyless backend unavailable")

    first_proxyless = AsyncMock(side_effect=open_circuit_then_fail)
    paid_backend = AsyncMock()
    recovered = SimpleNamespace(
        html="<html>" + ("valid data " * 300) + "</html>",
        final_url=source.url,
        backend="proxyless_second",
        http_status=200,
    )
    second_proxyless = AsyncMock(return_value=recovered)

    with (
        patch("app.scrapers.rotator.assert_proxy_configured"),
        patch("app.scrapers.rotator.fetch_max_retries", return_value=1),
        patch("app.scrapers.rotator.fetch_backend_max_seconds", return_value=None),
        patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[
                ("proxyless_first", first_proxyless, lambda: True),
                ("patchright", paid_backend, lambda: True),
                ("proxyless_second", second_proxyless, lambda: True),
            ],
        ),
        patch(
            "app.scrapers.rotator.browser_backend_uses_residential_proxy",
            side_effect=lambda _source, backend: backend == "patchright",
        ),
        patch("app.scrapers.rotator.looks_like_real_page", return_value=True),
        patch("app.scrapers.rotator.log_action"),
    ):
        result = asyncio.run(fetch_html(source))

    assert result is recovered
    first_proxyless.assert_awaited_once_with(source)
    paid_backend.assert_not_awaited()
    second_proxyless.assert_awaited_once_with(source)


def test_deterministic_shell_candidate_is_not_retried_by_same_backend() -> None:
    source = Source(
        id="deterministic-shell",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    patchright = AsyncMock(
        return_value=SimpleNamespace(
            html="<html><body>Just a moment...</body></html>",
            final_url=source.url,
            backend="patchright",
            http_status=200,
        )
    )
    flaresolverr = AsyncMock(
        return_value=SimpleNamespace(
            html="<html><body>cf-chl empty shell</body></html>",
            final_url=source.url,
            backend="flaresolverr",
            http_status=200,
        )
    )

    with (
        patch("app.scrapers.rotator.assert_proxy_configured"),
        patch("app.scrapers.rotator.fetch_max_retries", return_value=3),
        patch("app.scrapers.rotator.fetch_backend_max_seconds", return_value=None),
        patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[
                ("patchright", patchright, lambda: True),
                ("flaresolverr", flaresolverr, lambda: True),
            ],
        ),
        patch(
            "app.scrapers.rotator.browser_backend_uses_residential_proxy",
            return_value=False,
        ),
        patch("app.scrapers.rotator.log_action"),
        patch("app.scrapers.rotator.asyncio.sleep", new=AsyncMock()) as sleep,
        pytest.raises(RuntimeError, match="deterministic Cloudflare challenge shell"),
    ):
        asyncio.run(fetch_html(source))

    patchright.assert_awaited_once_with(source)
    flaresolverr.assert_awaited_once_with(source)
    sleep.assert_not_awaited()


def test_deterministic_shell_skip_does_not_disable_transient_retry() -> None:
    source = Source(
        id="transient-timeout",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    recovered = SimpleNamespace(
        html="<html>" + ("valid data " * 300) + "</html>",
        final_url=source.url,
        backend="patchright",
        http_status=200,
    )
    blocked = AsyncMock(
        return_value=SimpleNamespace(
            html="<html><body>Just a moment...</body></html>",
            final_url=source.url,
            backend="patchright",
            http_status=200,
        )
    )
    transient = AsyncMock(side_effect=[TimeoutError(), recovered])

    with (
        patch("app.scrapers.rotator.assert_proxy_configured"),
        patch("app.scrapers.rotator.fetch_max_retries", return_value=2),
        patch("app.scrapers.rotator.fetch_backend_max_seconds", return_value=None),
        patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[
                ("patchright", blocked, lambda: True),
                ("flaresolverr", transient, lambda: True),
            ],
        ),
        patch(
            "app.scrapers.rotator.browser_backend_uses_residential_proxy",
            return_value=False,
        ),
        patch("app.scrapers.rotator.log_action"),
        patch("app.scrapers.rotator.asyncio.sleep", new=AsyncMock()),
    ):
        result = asyncio.run(fetch_html(source))

    assert result is recovered
    blocked.assert_awaited_once_with(source)
    assert transient.await_count == 2


def test_sparse_post_patch_candidate_can_retry_and_recover() -> None:
    source = Source(
        id="sparse-post-patch",
        url="https://example.test/data",
        site="example",
        category="test",
    )
    sparse = SimpleNamespace(
        html="<html><body>Small but non-challenge post-patch page</body></html>",
        final_url=source.url,
        backend="patchright",
        http_status=200,
    )
    recovered = SimpleNamespace(
        html="<html>" + ("valid data " * 300) + "</html>",
        final_url=source.url,
        backend="patchright",
        http_status=200,
    )
    patchright = AsyncMock(side_effect=[sparse, recovered])

    with (
        patch("app.scrapers.rotator.assert_proxy_configured"),
        patch("app.scrapers.rotator.fetch_max_retries", return_value=2),
        patch("app.scrapers.rotator.fetch_backend_max_seconds", return_value=None),
        patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[("patchright", patchright, lambda: True)],
        ),
        patch(
            "app.scrapers.rotator.browser_backend_uses_residential_proxy",
            return_value=False,
        ),
        patch("app.scrapers.rotator.log_action"),
        patch("app.scrapers.rotator.asyncio.sleep", new=AsyncMock()),
    ):
        result = asyncio.run(fetch_html(source))

    assert result is recovered
    assert patchright.await_count == 2


def test_deterministic_shell_exhaustion_preserves_cloud_fallback() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_trinkets_lesser"]
    recovered = {
        "source_id": source.id,
        "state": "ok",
        "backend": "scrape_do",
    }
    cloud = AsyncMock(return_value=recovered)

    with (
        TemporaryDirectory() as temp_dir,
        patch("app.storage.data_dir", return_value=Path(temp_dir)),
        patch(
            "app.fetcher._fetch_hsreplay_api_source",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.fetcher.fetch_html",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "page is a deterministic Cloudflare challenge shell"
                )
            ),
        ),
        patch("app.fetcher._try_firecrawl_html", new=cloud),
        patch("app.fetcher.log_action"),
    ):
        result = asyncio.run(fetch_source(None, source))

    assert result is recovered
    cloud.assert_awaited_once()
    await_call = cloud.await_args
    assert await_call is not None
    assert await_call.args == (source,)
    assert await_call.kwargs["reason"] == "browser_exception:RuntimeError"


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

    assert first[0]["failure_class"] == "proxy_402"
    assert first[0]["proxy_status"] == 402
    assert second[0]["state"] == "ok"
    assert second[1]["state"] == "ok"
    assert third[0]["state"] == "ok"
    assert [call.args[1].id for call in fetch_mock.call_args_list] == [
        failing.id,
        hsreplay_json.id,
        metastats_cloud.id,
        dependent.id,
    ]


def test_scrape_do_capability_is_limited_to_hsreplay_json_sources() -> None:
    json_source = SOURCE_BY_ID["hsreplay_cards_legend_1d"]
    trending_api_source = SOURCE_BY_ID["hsreplay_decks_trending"]
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
        assert source_has_hsreplay_scrape_do_json_route(trending_api_source)
