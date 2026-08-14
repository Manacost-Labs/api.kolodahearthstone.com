from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import httpx

from app.proxy_errors import ProxyPaymentRequiredError
from app.vicious_syndicate import (
    ViciousUpstreamPublicationPending,
    _valid_vicious_response,
    _ViciousProxyCircuit,
    classify_radar_publication,
    fetch_with_retry,
    preflight_known_pending_publication,
    sanitize_upstream_readiness,
    upstream_publication_metadata,
    verified_upstream_pending_readiness,
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


class _PendingRadarAsyncClient(_FakeAsyncClient):
    requested_urls: ClassVar[list[str]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).requested_urls.append(url)
        if "Egg%20Death%20Knight" in url or "Egg Death Knight" in url:
            graph = """
                <title>Data Reaper's Radar - Issue #354</title>
                <script>function setup(canvas) {
                var n = {"Card A": {radius: 1}, "Card B": {radius: 1}};
                var e = [["Card A", "Card B", {weight: 1}]];
                }</script>
            """
            return httpx.Response(
                200,
                text=graph,
                request=httpx.Request("GET", url, headers=headers),
            )
        raise AssertionError("readiness preflight must stop at known blockers")


class _RemovedThenReadyRadarAsyncClient(_FakeAsyncClient):
    requested_urls: ClassVar[list[str]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        type(self).requested_urls.append(url)
        if "Egg%20Death%20Knight" in url or "Egg Death Knight" in url:
            return httpx.Response(
                404,
                text="<html>removed optional radar</html>",
                request=httpx.Request("GET", url, headers=headers),
            )
        graph = """
            <title>Data Reaper's Radar - Issue #355</title>
            <script>function setup(canvas) {
            var n = {"Card A": {radius: 1}, "Card B": {radius: 1}};
            var e = [["Card A", "Card B", {weight: 1}]];
            }</script>
        """
        return httpx.Response(
            200,
            text=graph,
            request=httpx.Request("GET", url, headers=headers),
        )


class _RadarReadinessAsyncClient(_FakeAsyncClient):
    response_text = ""
    status_code = 200

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(
            type(self).status_code,
            text=type(self).response_text,
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
        _PendingRadarAsyncClient.requested_urls = []
        _RemovedThenReadyRadarAsyncClient.requested_urls = []

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

    def test_known_pending_preflight_checks_blocker_first_without_paid_fallbacks(self) -> None:
        egg_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Egg%20Death%20Knight/index.html"
        )
        mage_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Mage/index.html"
        )
        status = {
            "last_refresh_at": datetime.now(UTC).isoformat(),
            "last_refresh_upstream_readiness": {
                "latest_report_issue": "355",
                "candidate_issue": "354",
                "full_discovery_at": datetime.now(UTC).isoformat(),
                "radar_urls": [mage_url, egg_url],
                "blocking_radar_urls": [egg_url],
            }
        }

        async def run() -> None:
            with (
                patch("app.vicious_syndicate.load_status", return_value=status),
                patch(
                    "app.vicious_syndicate.httpx.AsyncClient",
                    _PendingRadarAsyncClient,
                ),
                self.assertRaises(ViciousUpstreamPublicationPending) as pending,
            ):
                await preflight_known_pending_publication(
                    "vicious_syndicate_radars",
                    latest_issue="355",
                )

            self.assertEqual(pending.exception.failure_reason_code, "unavailable")
            self.assertTrue(pending.exception.skip_browser_fallback)
            self.assertEqual(_PendingRadarAsyncClient.requested_urls, [egg_url])

        asyncio.run(run())

    def test_readiness_200_blocks_only_on_valid_older_numeric_radar(self) -> None:
        radar_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Mage/index.html"
        )
        status = {
            "last_refresh_at": datetime.now(UTC).isoformat(),
            "last_refresh_upstream_readiness": {
                "latest_report_issue": "355",
                "candidate_issue": "354",
                "full_discovery_at": datetime.now(UTC).isoformat(),
                "radar_urls": [radar_url],
                "blocking_radar_urls": [radar_url],
            }
        }
        graph = """
            <script>function setup(canvas) {
            var n = {"Card A": {radius: 1}, "Card B": {radius: 1}};
            var e = [["Card A", "Card B", {weight: 1}]];
            }</script>
        """

        async def run_case(html: str, *, pending: bool) -> None:
            _RadarReadinessAsyncClient.response_text = html
            with (
                patch("app.vicious_syndicate.load_status", return_value=status),
                patch(
                    "app.vicious_syndicate.httpx.AsyncClient",
                    _RadarReadinessAsyncClient,
                ),
            ):
                if pending:
                    with self.assertRaises(ViciousUpstreamPublicationPending):
                        await preflight_known_pending_publication(
                            "vicious_syndicate_radars",
                            latest_issue="355",
                        )
                else:
                    await preflight_known_pending_publication(
                        "vicious_syndicate_radars",
                        latest_issue="355",
                    )

        asyncio.run(
            run_case(
                "<html><title>Just a moment...</title>cf-chl</html>",
                pending=False,
            )
        )
        asyncio.run(run_case(graph, pending=False))
        asyncio.run(
            run_case(
                f"<title>Data Reaper's Radar - Issue #354</title>{graph}",
                pending=True,
            )
        )
        asyncio.run(
            run_case(
                f"<title>Data Reaper's Radar - Issue #356</title>{graph}",
                pending=False,
            )
        )

    def test_readiness_404_does_not_delay_recovery_after_optional_radar_removal(
        self,
    ) -> None:
        egg_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Egg%20Death%20Knight/index.html"
        )
        mage_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Mage/index.html"
        )
        status = {
            "last_refresh_upstream_readiness": {
                "latest_report_issue": "355",
                "candidate_issue": "354",
                "full_discovery_at": datetime.now(UTC).isoformat(),
                "radar_urls": [egg_url, mage_url],
                "blocking_radar_urls": [egg_url],
            }
        }

        async def run() -> None:
            with (
                patch("app.vicious_syndicate.load_status", return_value=status),
                patch(
                    "app.vicious_syndicate.httpx.AsyncClient",
                    _RemovedThenReadyRadarAsyncClient,
                ),
            ):
                await preflight_known_pending_publication(
                    "vicious_syndicate_radars",
                    latest_issue="355",
                )

        asyncio.run(run())
        self.assertEqual(
            _RemovedThenReadyRadarAsyncClient.requested_urls,
            [egg_url, mage_url],
        )

    def test_old_pending_status_forces_periodic_full_rediscovery(self) -> None:
        radar_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Mage/index.html"
        )
        status = {
            # A cheap pending attempt may have refreshed this mutable field.
            "last_refresh_at": datetime.now(UTC).isoformat(),
            "last_refresh_upstream_readiness": {
                "latest_report_issue": "355",
                "candidate_issue": "354",
                "full_discovery_at": (
                    datetime.now(UTC) - timedelta(hours=7)
                ).isoformat(),
                "radar_urls": [radar_url],
                "blocking_radar_urls": [radar_url],
            },
        }

        async def run() -> None:
            with (
                patch("app.vicious_syndicate.load_status", return_value=status),
                patch(
                    "app.vicious_syndicate.httpx.AsyncClient",
                    side_effect=AssertionError("stale readiness must not be requested"),
                ) as client,
            ):
                await preflight_known_pending_publication(
                    "vicious_syndicate_radars",
                    latest_issue="355",
                )
            client.assert_not_called()

        asyncio.run(run())

    def test_readiness_sanitizer_accepts_only_canonical_official_radar_urls(self) -> None:
        valid = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Egg%20Death%20Knight/index.html"
        )
        invalid = [
            "https://user@www.vicioussyndicate.com/wp-content/datareaper/radars/Mage/index.html",
            "https://www.vicioussyndicate.com:444/wp-content/datareaper/radars/Mage/index.html",
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/Mage/index.html?q=1",
            "https://www.vicioussyndicate.com/wp-json/wp/v2/users",
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/../index.html",
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/%2e%2e/index.html",
            "https://[invalid",
            "https://www.vicioussyndicate.com/" + "x" * 2050,
        ]

        readiness = sanitize_upstream_readiness(
            {
                "latest_report_issue": "355",
                "candidate_issue": "354",
                "full_discovery_at": "2026-08-13T23:30:00Z",
                "radar_urls": [valid, *invalid],
                "blocking_radar_urls": [valid, *invalid],
            }
        )

        self.assertEqual(readiness["radar_urls"], [valid])
        self.assertEqual(readiness["blocking_radar_urls"], [valid])
        self.assertEqual(
            readiness["full_discovery_at"],
            "2026-08-13T23:30:00+00:00",
        )

    def test_verified_pending_readiness_requires_recent_bounded_evidence(self) -> None:
        valid = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Mage/index.html"
        )
        evidence = {
            "latest_report_issue": "355",
            "candidate_issue": "354",
            "full_discovery_at": datetime.now(UTC).isoformat(),
            "radar_urls": [valid],
            "blocking_radar_urls": [valid],
        }

        self.assertIsNotNone(verified_upstream_pending_readiness(evidence))
        self.assertIsNone(
            verified_upstream_pending_readiness(
                {**evidence, "blocking_radar_urls": [
                    "https://www.vicioussyndicate.com/wp-content/datareaper/"
                    "radars/Other/index.html"
                ]}
            )
        )
        self.assertIsNone(
            verified_upstream_pending_readiness(
                {
                    **evidence,
                    "full_discovery_at": (
                        datetime.now(UTC) - timedelta(hours=7)
                    ).isoformat(),
                }
            )
        )

    def test_mixed_radar_issue_classification_is_order_independent(self) -> None:
        self.assertEqual(
            classify_radar_publication([], latest_issue="355"),
            ("Unknown", "upstream_unavailable"),
        )
        for row_issues in (["355", "354"], ["354", "355"]):
            with self.subTest(row_issues=row_issues):
                self.assertEqual(
                    classify_radar_publication(row_issues, latest_issue="355"),
                    ("354", "upstream_publication_pending"),
                )
        for row_issues in (["354", "356"], ["356", "354"]):
            with self.subTest(row_issues=row_issues):
                self.assertEqual(
                    classify_radar_publication(row_issues, latest_issue="355"),
                    ("Mixed", "upstream_unavailable"),
                )

    def test_pending_metadata_is_bounded_and_requires_only_upstream_issues(self) -> None:
        egg_url = (
            "https://www.vicioussyndicate.com/wp-content/datareaper/radars/"
            "Egg%20Death%20Knight/index.html"
        )
        structured = {
            "type": "vicious_syndicate_radars",
            "issue": "354",
            "latest_report_issue": "355",
            "upstream_state": "upstream_publication_pending",
            "diagnostics": {
                "radar_urls": [egg_url, "https://example.com/not-allowed"],
                "broken_radar_urls": [egg_url],
                "active_radar_urls": 22,
                "parsed_radars": 21,
                "ready_latest_issue_radars": 0,
            },
        }
        issues = [
            {"code": "vicious_radars.outdated_issue"},
            {"code": "vicious_radars.incomplete_active_coverage"},
            {"code": "vicious_radars.row_issue_mismatch"},
        ]

        metadata = upstream_publication_metadata(
            structured,
            semantic_issues=issues,
        )

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata["failure_reason_code"], "unavailable")
        self.assertEqual(metadata["upstream_state"], "upstream_publication_pending")
        self.assertEqual(metadata["upstream_readiness"]["radar_urls"], [egg_url])
        discovery_at = datetime.fromisoformat(
            metadata["upstream_readiness"]["full_discovery_at"]
        )
        self.assertLessEqual(
            abs((datetime.now(UTC) - discovery_at).total_seconds()),
            2,
        )

        issues.append({"code": "vicious_radars.invalid_radar_url"})
        self.assertIsNone(
            upstream_publication_metadata(structured, semantic_issues=issues)
        )

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
