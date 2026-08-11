from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.proxy_errors import ProxyPaymentRequiredError
from app.vicious_syndicate import _ViciousProxyCircuit, fetch_with_retry


class _FakeAsyncClient:
    calls = 0
    last_kwargs: dict[str, object] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).last_kwargs = dict(kwargs)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).calls += 1
        return httpx.Response(
            404,
            text="<html>missing optional radar</html>",
            request=httpx.Request("GET", url, headers=headers),
        )


class _ProxyFailureAsyncClient(_FakeAsyncClient):
    status_code = 402

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).calls += 1
        raise httpx.ProxyError(
            f"Connect tunnel failed, response {type(self).status_code}"
        )


class _TransientAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).calls += 1
        if type(self).calls == 1:
            raise httpx.ConnectError("temporary connection reset")
        return httpx.Response(
            200,
            text="<html>recovered</html>",
            request=httpx.Request("GET", url, headers=headers),
        )


class ViciousSyndicateFetchTest(unittest.TestCase):
    def setUp(self) -> None:
        _FakeAsyncClient.calls = 0
        _FakeAsyncClient.last_kwargs = {}
        _ProxyFailureAsyncClient.calls = 0
        _ProxyFailureAsyncClient.last_kwargs = {}
        _TransientAsyncClient.calls = 0
        _TransientAsyncClient.last_kwargs = {}

    def test_optional_fetch_404_does_not_use_error_logger(self) -> None:
        async def run() -> None:
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _FakeAsyncClient),
                patch("app.vicious_syndicate.httpx_client_kwargs", return_value={}),
                patch("app.vicious_syndicate.log_http_error") as log_http_error,
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
                self.assertLogs("app.vicious_syndicate", level="INFO") as logs,
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/wp-content/datareaper/radars/missing/index.html",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=1,
                    optional=True,
                    optional_context="radar_html",
                )

            self.assertIsNone(response)
            log_http_error.assert_not_called()
            self.assertTrue(
                any("Optional Vicious fetch failed" in message for message in logs.output)
            )

        asyncio.run(run())

    def test_fetch_uses_saved_vicious_cookies(self) -> None:
        async def run() -> None:
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _FakeAsyncClient),
                patch("app.vicious_syndicate.httpx_client_kwargs", return_value={}),
                patch(
                    "app.vicious_syndicate.vicious_syndicate_cookies_for_fetch",
                    return_value={"wordpress_logged_in_test": "secret"},
                ),
                patch("app.vicious_syndicate.log_http_error"),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
                self.assertLogs("app.vicious_syndicate", level="WARNING"),
            ):
                await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/deck-library/mage-decks/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=1,
                    optional=True,
                )

            self.assertEqual(
                _FakeAsyncClient.last_kwargs.get("cookies"),
                {"wordpress_logged_in_test": "secret"},
            )

        asyncio.run(run())

    def test_optional_fetch_caps_retries_to_reduce_traffic(self) -> None:
        async def run() -> None:
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _FakeAsyncClient),
                patch("app.vicious_syndicate.httpx_client_kwargs", return_value={}),
                patch("app.vicious_syndicate.log_http_error"),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock) as sleep,
                self.assertLogs("app.vicious_syndicate", level="WARNING"),
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/wp-content/datareaper/radars/missing/index.html",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=5,
                    optional=True,
                    optional_context="radar_html",
                )

            self.assertIsNone(response)
            self.assertEqual(_FakeAsyncClient.calls, 2)
            self.assertGreaterEqual(sleep.await_count, 1)

        asyncio.run(run())

    def test_proxy_connect_payment_failures_open_source_circuit_without_retry(self) -> None:
        async def run(status_code: int) -> None:
            _ProxyFailureAsyncClient.calls = 0
            _ProxyFailureAsyncClient.status_code = status_code
            circuit = _ViciousProxyCircuit()
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _ProxyFailureAsyncClient),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={"proxy": "http://proxy.invalid:8080"},
                ),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
            ):
                with self.assertRaises(ProxyPaymentRequiredError) as first:
                    await fetch_with_retry(
                        _client=object(),  # type: ignore[arg-type]
                        url="https://www.vicioussyndicate.com/tag/data-reaper-report/",
                        semaphore=asyncio.Semaphore(1),
                        max_retries=5,
                        proxy_circuit=circuit,
                    )
                with self.assertRaises(ProxyPaymentRequiredError) as second:
                    await fetch_with_retry(
                        _client=object(),  # type: ignore[arg-type]
                        url="https://www.vicioussyndicate.com/deck-library/mage-decks/",
                        semaphore=asyncio.Semaphore(1),
                        max_retries=5,
                        optional=True,
                        proxy_circuit=circuit,
                    )

            self.assertEqual(first.exception.status_code, status_code)
            self.assertIs(second.exception, first.exception)
            self.assertEqual(_ProxyFailureAsyncClient.calls, 1)

        for status_code in (402, 407):
            with self.subTest(status_code=status_code):
                asyncio.run(run(status_code))

    def test_transient_proxy_transport_error_keeps_normal_retry(self) -> None:
        async def run() -> None:
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _TransientAsyncClient),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={"proxy": "http://proxy.invalid:8080"},
                ),
                patch("app.vicious_syndicate.log_http_error"),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/tag/data-reaper-report/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=2,
                )

            self.assertIsNotNone(response)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(_TransientAsyncClient.calls, 2)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
