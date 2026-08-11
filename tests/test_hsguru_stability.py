from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.config import DEFAULT_BACKENDS, DEFAULT_HSGURU_BACKENDS
from app.publish_gate import PublishGateResult
from app.scrapers import rotator
from app.scrapers.base import FetchResult
from app.sources import SOURCE_BY_ID


class HSGuruStabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        rotator.reset_backend_circuits()

    def test_default_backend_orders_keep_proxyless_browsers_first(self) -> None:
        expected = "flaresolverr,patchright,scrapling,curl_cffi,cloudscraper"

        self.assertEqual(DEFAULT_BACKENDS, expected)
        self.assertEqual(DEFAULT_HSGURU_BACKENDS, expected)

    def test_hsguru_uses_site_specific_backend_order(self) -> None:
        source = SOURCE_BY_ID["hsguru_meta_wild_top_legend"]

        with patch(
            "app.config.fetch_backends",
            return_value=[
                "flaresolverr",
                "patchright",
                "scrapling",
                "curl_cffi",
                "cloudscraper",
            ],
        ), patch(
            "app.config.hsguru_fetch_backends",
            return_value=[
                "flaresolverr",
                "patchright",
                "scrapling",
                "curl_cffi",
                "cloudscraper",
            ],
        ):
            order = rotator._site_backend_order(source)

        self.assertEqual(
            order,
            [
                "flaresolverr",
                "patchright",
                "scrapling",
                "curl_cffi",
                "cloudscraper",
            ],
        )

    def test_timeout_circuit_is_source_scoped_for_hsguru(self) -> None:
        source = SOURCE_BY_ID["hsguru_meta_wild_top_legend"]

        self.assertEqual(rotator._circuit_scope(source, "timeout"), f"source:{source.id}")
        self.assertEqual(rotator._circuit_scope(source, "quality_empty"), f"source:{source.id}")
        self.assertEqual(rotator._circuit_scope(source, "blocked_403"), "site:hsguru")

    def test_blocked_hsguru_backend_burns_proxy_session(self) -> None:
        source = SOURCE_BY_ID["hsguru_meta_wild_top_legend"]

        async def blocked_backend(_source):
            raise RuntimeError("403 Client Error: Forbidden")

        with patch("app.scrapers.rotator.fetch_max_retries", return_value=1), patch(
            "app.scrapers.rotator.source_can_use_flaresolverr_without_proxy",
            return_value=True,
        ), patch(
            "app.scrapers.rotator.log_action",
        ), patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[("curl_cffi", blocked_backend, lambda: True)],
        ), patch("app.scrapers.rotator.burn_proxy_session") as burn:
            with self.assertRaises(RuntimeError):
                asyncio.run(rotator.fetch_html(source))

        burn.assert_called_once()

    def test_timeout_does_not_burn_proxy_session(self) -> None:
        source = SOURCE_BY_ID["hsguru_meta_wild_top_legend"]

        async def timeout_backend(_source):
            raise TimeoutError()

        with patch("app.scrapers.rotator.fetch_max_retries", return_value=1), patch(
            "app.scrapers.rotator.fetch_backend_max_seconds",
            return_value=120,
        ), patch(
            "app.scrapers.rotator.log_action",
        ), patch(
            "app.scrapers.rotator.source_can_use_flaresolverr_without_proxy",
            return_value=True,
        ), patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[("scrapling", timeout_backend, lambda: True)],
        ), patch("app.scrapers.rotator.burn_proxy_session") as burn:
            with self.assertRaises(RuntimeError):
                asyncio.run(rotator.fetch_html(source))

        burn.assert_not_called()

    def test_rotator_preview_uses_publish_gate_with_backend_identity(self) -> None:
        source = SOURCE_BY_ID["hsguru_meta_standard_legend"]

        async def backend(_source):
            return FetchResult(
                html="hsguru.com".ljust(25_000, "x"),
                final_url=source.url,
                backend="test",
            )

        with patch("app.scrapers.rotator.fetch_max_retries", return_value=1), patch(
            "app.scrapers.rotator.source_can_use_flaresolverr_without_proxy",
            return_value=True,
        ), patch("app.scrapers.rotator.log_action"), patch(
            "app.scrapers.rotator._ordered_backends",
            return_value=[("firecrawl", backend, lambda: True)],
        ), patch(
            "app.scrapers.rotator.validate_candidate_for_publish",
            return_value=PublishGateResult(False, "backend policy rejected", {"backend": "firecrawl"}),
        ) as gate:
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    rotator.fetch_html(
                        source,
                        parse_preview=lambda _html: {"title": "HSGuru", "structured": {}},
                    )
                )

        gate.assert_called_once()
        self.assertEqual(gate.call_args.kwargs["backend"], "firecrawl")


if __name__ == "__main__":
    unittest.main()
