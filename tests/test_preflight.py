from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from app.preflight import (
    PreflightResult,
    ensure_refresh_preflight,
    run_refresh_preflight,
    selection_needs_flaresolverr_preflight,
    selection_needs_proxy_preflight,
)
from app.sources import SOURCE_BY_ID


class PreflightTest(unittest.IsolatedAsyncioTestCase):
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_mixed_refresh_does_not_block_on_flaresolverr(
        self, _direct: object
    ) -> None:
        selected = [
            SOURCE_BY_ID["hsguru_meta_standard_legend"],
            SOURCE_BY_ID["vicious_syndicate_live_beta"],
        ]

        self.assertFalse(
            selection_needs_flaresolverr_preflight(
                selected,
                configured_backends=["flaresolverr", "patchright"],
            )
        )

    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_flaresolverr_is_not_a_global_gate_when_another_backend_exists(
        self, _direct: object
    ) -> None:
        selected = [SOURCE_BY_ID["hsreplay_cards_legend_included_winrate"]]

        self.assertFalse(
            selection_needs_flaresolverr_preflight(
                selected,
                configured_backends=["flaresolverr", "patchright"],
            )
        )

    @patch("app.fetch_routes.firecrawl_fallback_source_ids", return_value=set())
    @patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set())
    @patch("app.fetch_routes.hsguru_fetch_backends", return_value=["flaresolverr"])
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_site_specific_flaresolverr_only_route_is_gated(
        self,
        _direct: object,
        _hsguru_backends: object,
        _primary: object,
        _fallback: object,
    ) -> None:
        selected = [SOURCE_BY_ID["hsguru_meta_standard_legend"]]

        self.assertTrue(
            selection_needs_flaresolverr_preflight(
                selected,
                configured_backends=["patchright"],
            )
        )

    @patch("app.fetch_routes.firecrawl_fallback_source_ids", return_value=set())
    @patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set())
    @patch("app.fetch_routes.hsguru_fetch_backends", return_value=["patchright"])
    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_proxy_gate_uses_site_specific_backend_order(
        self,
        _direct: object,
        _required: object,
        _hsguru_backends: object,
        _primary: object,
        _fallback: object,
    ) -> None:
        selected = [SOURCE_BY_ID["hsguru_meta_standard_legend"]]

        self.assertTrue(
            selection_needs_proxy_preflight(
                selected,
                configured_backends=["flaresolverr"],
            )
        )

    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_api_only_selection_does_not_require_residential_proxy(
        self, _direct: object, _required: object
    ) -> None:
        selected = [
            SOURCE_BY_ID["vicious_syndicate_live_beta"],
            SOURCE_BY_ID["vicious_syndicate_radars"],
            SOURCE_BY_ID["hsreplay_cards_legend_1d"],
        ]

        self.assertFalse(
            selection_needs_proxy_preflight(
                selected,
                configured_backends=["flaresolverr", "patchright"],
            )
        )

    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_local_browser_selection_requires_proxy_without_safe_backend(
        self, _direct: object, _required: object
    ) -> None:
        selected = [SOURCE_BY_ID["hsreplay_cards_legend_included_winrate"]]

        self.assertTrue(
            selection_needs_proxy_preflight(
                selected,
                configured_backends=["patchright"],
            )
        )

    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_mixed_selection_does_not_make_proxy_a_global_gate(
        self, _direct: object, _required: object
    ) -> None:
        selected = [
            SOURCE_BY_ID["hsreplay_cards_legend_included_winrate"],
            SOURCE_BY_ID["vicious_syndicate_live_beta"],
        ]

        self.assertFalse(
            selection_needs_proxy_preflight(
                selected,
                configured_backends=["patchright"],
            )
        )

    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.fetch_direct_enabled", return_value=False)
    def test_firecrawl_primary_source_is_not_blocked_by_proxy_preflight(
        self, _direct: object, _required: object
    ) -> None:
        selected = [SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]]

        self.assertFalse(
            selection_needs_proxy_preflight(
                selected,
                configured_backends=["patchright"],
            )
        )

    @patch("app.preflight.refresh_preflight_strict", return_value=True)
    @patch(
        "app.preflight.run_refresh_preflight",
        new_callable=AsyncMock,
        return_value=PreflightResult(ok=False, errors=["flaresolverr unavailable"]),
    )
    async def test_strict_browser_only_preflight_failure_is_blocking(
        self,
        _run_preflight: AsyncMock,
        _strict: object,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "flaresolverr unavailable"):
            await ensure_refresh_preflight(
                full_refresh=False,
                needs_proxy=False,
                needs_flaresolverr=True,
            )

    @patch("app.preflight.refresh_preflight_strict", return_value=True)
    @patch(
        "app.preflight.check_proxy_health",
        new_callable=AsyncMock,
        side_effect=RuntimeError("HTTP 402 Payment Required"),
    )
    async def test_route_without_proxy_dependency_skips_broken_global_proxy(
        self,
        proxy_health: AsyncMock,
        _strict: object,
    ) -> None:
        result = await ensure_refresh_preflight(
            full_refresh=False,
            needs_proxy=False,
            needs_flaresolverr=False,
        )

        self.assertEqual(result, {})
        proxy_health.assert_not_awaited()

    @patch("app.preflight.fetch_proxy_url", return_value="http://proxy:1@example.com:1")
    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.check_proxy_health", new_callable=AsyncMock)
    @patch("app.preflight.refresh_preflight_probe_hsreplay", return_value=False)
    async def test_proxy_ok(
        self, _probe: object, mock_proxy: AsyncMock, _req: object, _url: object
    ) -> None:
        mock_proxy.return_value = {"egress_ip": "1.2.3.4", "rotation_ok": "True"}
        with TemporaryDirectory() as td, patch("app.storage.data_dir", return_value=Path(td)):
            result = await run_refresh_preflight(needs_proxy=True, needs_flaresolverr=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.proxy_info.get("egress_ip"), "1.2.3.4")

    @patch("app.preflight.fetch_proxy_url", return_value="http://proxy:1@example.com:1")
    @patch("app.preflight.fetch_require_proxy", return_value=True)
    @patch("app.preflight.check_proxy_health", new_callable=AsyncMock, side_effect=RuntimeError("fail"))
    @patch("app.preflight.refresh_preflight_probe_hsreplay", return_value=False)
    async def test_proxy_fail(
        self, _probe: object, _mock_proxy: AsyncMock, _req: object, _url: object
    ) -> None:
        with TemporaryDirectory() as td, patch("app.storage.data_dir", return_value=Path(td)):
            result = await run_refresh_preflight(needs_proxy=True, needs_flaresolverr=False)
        self.assertFalse(result.ok)
        self.assertTrue(any("proxy" in e for e in result.errors))

    @patch("app.preflight.fetch_require_proxy", return_value=False)
    @patch("app.preflight.refresh_preflight_probe_hsreplay", return_value=False)
    async def test_proxy_skipped_when_not_required(
        self, _probe: object, _req: object
    ) -> None:
        with TemporaryDirectory() as td, patch("app.storage.data_dir", return_value=Path(td)):
            result = await run_refresh_preflight(needs_proxy=True, needs_flaresolverr=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.checks[0]["name"], "proxy")
        self.assertTrue(result.checks[0]["skipped"])


class PreflightResultTest(unittest.TestCase):
    def test_to_dict(self) -> None:
        pf = PreflightResult(ok=True, warnings=["w"])
        d = pf.to_dict()
        self.assertTrue(d["ok"])
        self.assertEqual(d["warnings"], ["w"])


class FlaresolverrCheckTest(unittest.IsolatedAsyncioTestCase):
    @patch("app.preflight.httpx.AsyncClient")
    async def test_check_flaresolverr_functional_ok(self, mock_client: AsyncMock) -> None:
        # sessions.list ok + functional probe ok
        inst = mock_client.return_value.__aenter__.return_value
        inst.post.side_effect = [
            # sessions
            type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"status": "ok", "version": "3.5.0", "sessions": []}})(),
            # probe
            type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"status": "ok", "solution": {"status": 200, "response": '{"ip":"1.2.3.4"}' }}})(),
        ]
        from app.preflight import check_flaresolverr

        res = await check_flaresolverr(probe_functional=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("functional"))

    @patch("app.preflight.httpx.AsyncClient")
    async def test_check_flaresolverr_functional_fail_still_basic_ok(self, mock_client: AsyncMock) -> None:
        inst = mock_client.return_value.__aenter__.return_value
        inst.post.side_effect = [
            type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"status": "ok", "version": "3.5.0", "sessions": [1]}})(),
            type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"status": "ok", "solution": {"status": 403}}})(),
        ]
        from app.preflight import check_flaresolverr

        res = await check_flaresolverr(probe_functional=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res.get("functional"))


if __name__ == "__main__":
    unittest.main()
