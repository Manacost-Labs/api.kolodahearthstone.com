from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.proxy_errors import ProxyPaymentRequiredError
from app.vicious_syndicate import (
    _valid_vicious_response,
    _ViciousProxyCircuit,
    fetch_with_retry,
)


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
            text="""
                <article>
                  <a href="/vs-data-reaper-report-354/">Report 354</a>
                  <span class="entry-meta-date">July 30, 2026</span>
                </article>
            """,
            request=httpx.Request("GET", url, headers=headers),
        )


class _ProxyThenDirectAsyncClient(_FakeAsyncClient):
    proxy_calls = 0
    direct_calls = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.kwargs = dict(kwargs)

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).calls += 1
        if self.kwargs.get("proxy"):
            type(self).proxy_calls += 1
            raise httpx.ProxyError("Connect tunnel failed, response 402")

        type(self).direct_calls += 1
        if "/tag/data-reaper-report/" in url:
            text = """
                <html><script>const cf_clearance = true;</script><body>
                  <article>
                    <a href="/vs-data-reaper-report-354/">Report 354</a>
                    <span class="entry-meta-date">July 30, 2026</span>
                  </article>
                </body></html>
            """
        else:
            text = """
                <html><head><link href="https://www.vicioussyndicate.com/deck-library/" /></head>
                <body class="entry-content">
                  <a href="/deck-library/mage-decks/quest-mage/">Quest Mage</a>
                  <iframe src="/wp-content/datareaper/radars/Mage/index.html"></iframe>
                </body></html>
            """
        return httpx.Response(
            200,
            text=text,
            request=httpx.Request("GET", url, headers=headers),
        )


class _RedirectAsyncClient(_FakeAsyncClient):
    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).calls += 1
        return httpx.Response(
            302,
            headers={"location": "https://example.com/redirected"},
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
        _ProxyThenDirectAsyncClient.calls = 0
        _ProxyThenDirectAsyncClient.last_kwargs = {}
        _ProxyThenDirectAsyncClient.proxy_calls = 0
        _ProxyThenDirectAsyncClient.direct_calls = 0
        _RedirectAsyncClient.calls = 0
        _RedirectAsyncClient.last_kwargs = {}

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

    def test_proxy_connect_payment_failure_recovers_direct_and_reuses_circuit(self) -> None:
        async def run() -> None:
            circuit = _ViciousProxyCircuit()
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _ProxyThenDirectAsyncClient),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={"proxy": "http://proxy.invalid:8080"},
                ),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
            ):
                report = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/tag/data-reaper-report/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=2,
                    proxy_circuit=circuit,
                )
                class_page = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/deck-library/mage-decks/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=2,
                    optional=True,
                    proxy_circuit=circuit,
                )

            self.assertIsNotNone(report)
            self.assertIsNotNone(class_page)
            self.assertIsNotNone(circuit.error)
            self.assertEqual(circuit.error.status_code, 402)
            self.assertEqual(circuit.transport_backend, "proxyless_direct")
            self.assertEqual(_ProxyThenDirectAsyncClient.proxy_calls, 1)
            self.assertEqual(_ProxyThenDirectAsyncClient.direct_calls, 2)
            self.assertNotIn("proxy", _ProxyThenDirectAsyncClient.last_kwargs)
            self.assertFalse(_ProxyThenDirectAsyncClient.last_kwargs["trust_env"])
            self.assertFalse(_ProxyThenDirectAsyncClient.last_kwargs["follow_redirects"])
            self.assertNotIn("cookies", _ProxyThenDirectAsyncClient.last_kwargs)

        asyncio.run(run())

    def test_proxyless_recovery_does_not_follow_redirects_or_send_cookies(self) -> None:
        async def run() -> None:
            circuit = _ViciousProxyCircuit()
            circuit.open(
                ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            )
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _RedirectAsyncClient),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={
                        "proxy": "http://proxy.invalid:8080",
                        "follow_redirects": True,
                    },
                ),
                patch(
                    "app.vicious_syndicate.vicious_syndicate_cookies_for_fetch",
                    return_value={"wordpress_logged_in_test": "secret"},
                ),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
                self.assertLogs("app.vicious_syndicate", level="WARNING"),
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/deck-library/mage-decks/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=1,
                    optional=True,
                    proxy_circuit=circuit,
                )

            self.assertIsNone(response)
            self.assertEqual(_RedirectAsyncClient.calls, 1)
            self.assertFalse(_RedirectAsyncClient.last_kwargs["follow_redirects"])
            self.assertFalse(_RedirectAsyncClient.last_kwargs["trust_env"])
            self.assertNotIn("proxy", _RedirectAsyncClient.last_kwargs)
            self.assertNotIn("cookies", _RedirectAsyncClient.last_kwargs)

        asyncio.run(run())

    def test_proxy_transport_does_not_follow_external_redirect_with_cookies(self) -> None:
        async def run() -> None:
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _RedirectAsyncClient),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={
                        "proxy": "http://proxy.invalid:8080",
                        "follow_redirects": True,
                    },
                ),
                patch(
                    "app.vicious_syndicate.vicious_syndicate_cookies_for_fetch",
                    return_value={"wordpress_logged_in_test": "secret"},
                ),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
                self.assertLogs("app.vicious_syndicate", level="WARNING"),
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/deck-library/mage-decks/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=1,
                    optional=True,
                    proxy_circuit=_ViciousProxyCircuit(),
                )

            self.assertIsNone(response)
            self.assertEqual(_RedirectAsyncClient.calls, 1)
            self.assertFalse(_RedirectAsyncClient.last_kwargs["follow_redirects"])
            self.assertIn("cookies", _RedirectAsyncClient.last_kwargs)

        asyncio.run(run())

    def test_proxyless_recovery_reraises_original_proxy_error_when_invalid(self) -> None:
        async def run() -> None:
            _ProxyFailureAsyncClient.status_code = 407
            circuit = _ViciousProxyCircuit()
            with (
                patch("app.vicious_syndicate.httpx.AsyncClient", _ProxyFailureAsyncClient),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={"proxy": "http://proxy.invalid:8080"},
                ),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
                patch("app.vicious_syndicate.log_http_error"),
                self.assertRaises(ProxyPaymentRequiredError) as failed,
            ):
                await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://www.vicioussyndicate.com/tag/data-reaper-report/",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=2,
                    proxy_circuit=circuit,
                )

            self.assertEqual(failed.exception.status_code, 407)
            self.assertEqual(_ProxyFailureAsyncClient.calls, 2)

        asyncio.run(run())

    def test_url_specific_validator_accepts_valid_content_not_challenge_shells(self) -> None:
        report = """
          <script>const cf_clearance = true;</script>
          <article><a href="/vs-data-reaper-report-354/">Report</a>
          <span class="entry-meta-date">July 30, 2026</span></article>
        """
        radar = """
          <script>function setup(canvas) {
          var n = {"Card A": {radius: 1}, "Card B": {radius: 1}};
          var e = [["Card A", "Card B", {weight: 1}]];
          }</script>
        """

        self.assertTrue(
            _valid_vicious_response(
                "https://www.vicioussyndicate.com/tag/data-reaper-report/",
                report,
            )
        )
        self.assertTrue(
            _valid_vicious_response(
                "https://www.vicioussyndicate.com/wp-content/datareaper/radars/Mage/index.html",
                radar,
            )
        )
        self.assertTrue(
            _valid_vicious_response(
                "https://www.vicioussyndicate.com/datareaper/radars/Mage/index.html",
                radar,
            )
        )
        self.assertFalse(
            _valid_vicious_response(
                "https://www.vicioussyndicate.com/tag/data-reaper-report/",
                "<html>Just a moment... cf_clearance</html>",
            )
        )
        self.assertFalse(
            _valid_vicious_response("https://example.com/report", report)
        )

    def test_proxyless_recovery_never_requests_a_discovered_external_host(self) -> None:
        async def run() -> None:
            circuit = _ViciousProxyCircuit()
            circuit.open(
                ProxyPaymentRequiredError(
                    "Residential proxy CONNECT rejected",
                    status_code=402,
                )
            )
            with (
                patch(
                    "app.vicious_syndicate.httpx.AsyncClient",
                    _ProxyThenDirectAsyncClient,
                ),
                patch(
                    "app.vicious_syndicate.httpx_client_kwargs",
                    return_value={"proxy": "http://proxy.invalid:8080"},
                ),
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://example.com/untrusted-radar/index.html",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=2,
                    optional=True,
                    proxy_circuit=circuit,
                )

            self.assertIsNone(response)
            self.assertEqual(_ProxyThenDirectAsyncClient.calls, 0)

        asyncio.run(run())

    def test_external_host_is_rejected_before_any_initial_transport(self) -> None:
        async def run() -> None:
            with (
                patch(
                    "app.vicious_syndicate.httpx.AsyncClient",
                    _ProxyThenDirectAsyncClient,
                ),
                patch("app.vicious_syndicate.httpx_client_kwargs") as client_kwargs,
                patch("app.vicious_syndicate.asyncio.sleep", new_callable=AsyncMock),
            ):
                response = await fetch_with_retry(
                    _client=object(),  # type: ignore[arg-type]
                    url="https://example.com/untrusted-radar/index.html",
                    semaphore=asyncio.Semaphore(1),
                    max_retries=2,
                    optional=True,
                    proxy_circuit=_ViciousProxyCircuit(),
                )

            self.assertIsNone(response)
            client_kwargs.assert_not_called()
            self.assertEqual(_ProxyThenDirectAsyncClient.calls, 0)

        asyncio.run(run())

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
