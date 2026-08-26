from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.firecrawl_backend import FirecrawlScrape
from app.hearthstone_decks import extract_deck_code_from_html
from app.hsguru_api import discover_hsguru_api_candidates
from app.sources import Source


class HsGuruDecksImprovementsTest(unittest.TestCase):
    def test_extract_deck_code_from_copy_button(self) -> None:
        code = "AAECAf0GBMABCD1234567890abcdefghijklmnop=="
        html = f'<button data-clipboard-text="{code}">Copy deck</button>'

        self.assertEqual(extract_deck_code_from_html(html), code)

    def test_extract_deck_code_from_script_payload(self) -> None:
        code = "AAECAQcGXYZ1234567890abcdefghijklmnopqr=="
        html = f"<script>window.deck = {{ code: '{code}' }};</script>"

        self.assertEqual(extract_deck_code_from_html(html), code)

    def test_hsguru_recon_discovers_embedded_json_and_api_candidates(self) -> None:
        html = """
        <html>
          <head><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{}}}</script></head>
          <body><script>fetch("/api/meta?format=2&rank=legend")</script></body>
        </html>
        """

        result = discover_hsguru_api_candidates(
            html, page_url="https://www.hsguru.com/meta"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["embedded_json"][0]["id"], "__NEXT_DATA__")
        self.assertIn(
            "https://www.hsguru.com/api/meta?format=2&rank=legend",
            result["api_candidates"],
        )

    def test_catalog_refresh_attempts_every_dataset_after_one_failure(self) -> None:
        from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

        calls: list[tuple[str, str]] = []

        async def refresh(
            format_name: str,
            rank: str = "legend",
            *,
            period: str,
        ) -> list[dict[str, str]]:
            calls.append((format_name, rank))
            self.assertTrue(period)
            if (format_name, rank) == ("standard", "legend"):
                raise RuntimeError("temporary upstream failure")
            return [{"deck_code": f"{format_name}-{rank}"}]

        result = asyncio.run(
            refresh_all_deck_catalogs(refresh=refresh, join=lambda: {"ok": True})
        )

        self.assertEqual(
            calls,
            [
                ("standard", "legend"),
                ("wild", "legend"),
                ("standard", "all"),
                ("wild", "all"),
            ],
        )
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["datasets"]["wild_legend"]["decks"], 1)
        self.assertIn("standard_legend", result["errors"])
        self.assertEqual(result["archetype_join"], {"ok": True})

    def test_deck_period_follows_the_current_meta_matrix(self) -> None:
        from app import hsguru_decks

        matrix = {
            "data": {
                "structured": {
                    "current_catalog": {"criteria": {"period": "patch_36.2.0"}}
                }
            }
        }
        with (
            patch.object(
                hsguru_decks, "hsguru_current_patch_period", return_value=None
            ),
            patch.object(
                hsguru_decks,
                "load_resolved_public_dataset",
                return_value=matrix,
            ),
        ):
            hsguru_decks._matrix_period_memory = None
            try:
                self.assertEqual(
                    hsguru_decks._current_deck_period(),
                    "patch_36.2.0",
                )
            finally:
                hsguru_decks._matrix_period_memory = None

    def test_deck_period_accepts_named_release_from_current_meta_matrix(self) -> None:
        from app import hsguru_decks

        matrix = {
            "data": {
                "structured": {
                    "current_catalog": {"criteria": {"period": "most_wanted"}}
                }
            }
        }
        with (
            patch.object(
                hsguru_decks, "hsguru_current_patch_period", return_value=None
            ),
            patch.object(
                hsguru_decks,
                "load_resolved_public_dataset",
                return_value=matrix,
            ),
        ):
            hsguru_decks._matrix_period_memory = None
            try:
                self.assertEqual(
                    hsguru_decks._current_deck_period(),
                    "most_wanted",
                )
            finally:
                hsguru_decks._matrix_period_memory = None

    def test_catalog_uses_local_solver_after_scrape_do_rotation_failure(self) -> None:
        from app import hsguru_decks

        hsguru_decks.reset_hsguru_catalog_provider_state()
        source = Source(
            id="hsguru_deck_catalog_standard_legend",
            url="https://www.hsguru.com/decks?format=2&rank=legend",
            site="hsguru",
            category="deck_catalog",
        )
        primary = AsyncMock(side_effect=RuntimeError("rotation failed"))
        solver = AsyncMock(
            return_value=SimpleNamespace(
                html='<div class="deck_stats_viewport">complete</div>',
                http_status=200,
                backend="flaresolverr",
                final_url=source.url,
            )
        )
        with (
            patch.object(hsguru_decks, "scrape_source_with_options", primary),
            patch.object(hsguru_decks, "fetch_via_flaresolverr", solver),
        ):
            page = asyncio.run(
                hsguru_decks._fetch_catalog_page(
                    source,
                    max_age_ms=1,
                    wait_ms=2,
                    timeout_ms=3,
                )
            )

        self.assertEqual(page.backend, "flaresolverr")
        self.assertEqual(page.request_credits, 0)
        solver.assert_awaited_once_with(source, wait_ms=0)
        self.assertEqual(primary.await_count, 1)
        self.assertEqual(
            primary.await_args.kwargs["skip_providers"],
            {"brightdata", "firecrawl", "scrapfly"},
        )

    def test_all_rank_fanout_prefers_fast_local_solver(self) -> None:
        from app import hsguru_decks

        source = Source(
            id="hsguru_deck_catalog_standard_all",
            url=(
                "https://www.hsguru.com/decks?format=2&rank=all&"
                "period=patch_36.2.0&min_games=100&limit=200"
            ),
            site="hsguru",
            category="deck_catalog",
        )
        primary = AsyncMock()
        solver = AsyncMock(
            return_value=SimpleNamespace(
                html='<div class="deck_stats_viewport">complete</div>',
                http_status=200,
                backend="flaresolverr",
                final_url=source.url,
            )
        )
        with (
            patch.object(hsguru_decks, "scrape_source_with_options", primary),
            patch.object(hsguru_decks, "fetch_via_flaresolverr", solver),
        ):
            page = asyncio.run(
                hsguru_decks._fetch_catalog_page(
                    source,
                    max_age_ms=1,
                    wait_ms=2,
                    timeout_ms=3,
                    prefer_local_solver=True,
                )
            )

        self.assertEqual(page.backend, "flaresolverr")
        solver.assert_awaited_once_with(source, wait_ms=0)
        primary.assert_not_awaited()

    def test_all_rank_fanout_falls_back_to_subscribed_scrape_do(self) -> None:
        from app import hsguru_decks

        source = Source(
            id="hsguru_deck_catalog_standard_all",
            url=(
                "https://www.hsguru.com/decks?format=2&rank=all&"
                "period=patch_36.2.0&min_games=100&limit=200"
            ),
            site="hsguru",
            category="deck_catalog",
        )
        primary_result = FirecrawlScrape(
            html='<div class="deck_stats_viewport">complete</div>',
            markdown="",
            screenshot=None,
            metadata={"backend": "scrape_do_super", "scrapeDoCreditsUsed": 25},
            status_code=200,
            final_url=source.url,
        )

        async def primary(*_args, **kwargs):
            kwargs["attempt_observer"](primary_result, True)
            return primary_result

        with (
            patch.object(
                hsguru_decks,
                "fetch_via_flaresolverr",
                AsyncMock(side_effect=RuntimeError("local solver unavailable")),
            ),
            patch.object(hsguru_decks, "scrape_source_with_options", primary),
        ):
            page = asyncio.run(
                hsguru_decks._fetch_catalog_page(
                    source,
                    max_age_ms=1,
                    wait_ms=2,
                    timeout_ms=3,
                    prefer_local_solver=True,
                )
            )

        self.assertEqual(page.backend, "scrape_do_super")
        self.assertEqual(page.request_credits, 25)
        self.assertEqual(
            [attempt["backend"] for attempt in page.acquisition],
            ["flaresolverr", "scrape_do_super"],
        )

    def test_catalog_counts_rejected_scrape_do_response_before_solver(self) -> None:
        from app import hsguru_decks

        hsguru_decks.reset_hsguru_catalog_provider_state()
        source = Source(
            id="hsguru_deck_catalog_standard_legend",
            url="https://www.hsguru.com/decks?format=2&rank=legend",
            site="hsguru",
            category="deck_catalog",
        )
        rejected = FirecrawlScrape(
            html="<html><body>valid generic page, wrong dataset</body></html>",
            markdown="",
            screenshot=None,
            metadata={
                "backend": "scrape_do_super",
                "scrapeDoCreditsUsed": 25,
            },
            status_code=200,
            final_url=source.url,
        )

        async def primary(*_args, **kwargs):
            kwargs["attempt_observer"](rejected, False)
            kwargs["failure_observer"](
                {
                    "backend": "scrape_do",
                    "state": "failed",
                    "http_status": 200,
                    "request_credits": 25,
                    "error_type": "ScrapeDoContentError",
                    "error_code": "ScrapeDoContentError",
                    "profile_attempt": 1,
                    "provider_attempt": 1,
                    "super_proxy": True,
                }
            )
            raise RuntimeError("content validation failed")

        solver = AsyncMock(
            return_value=SimpleNamespace(
                html='<div class="deck_stats_viewport">complete</div>',
                http_status=200,
                backend="flaresolverr",
                final_url=source.url,
            )
        )
        with (
            patch.object(hsguru_decks, "scrape_source_with_options", primary),
            patch.object(hsguru_decks, "fetch_via_flaresolverr", solver),
        ):
            page = asyncio.run(
                hsguru_decks._fetch_catalog_page(
                    source,
                    max_age_ms=1,
                    wait_ms=2,
                    timeout_ms=3,
                )
            )

        self.assertEqual(page.backend, "flaresolverr")
        self.assertEqual(page.request_credits, 25)
        self.assertEqual(
            [
                (attempt["backend"], attempt["state"], attempt["request_credits"])
                for attempt in page.acquisition
            ],
            [
                ("scrape_do_super", "rejected", 25),
                ("flaresolverr", "accepted", 0),
            ],
        )
        self.assertEqual(len(page.acquisition), 2)
        self.assertEqual(
            page.acquisition[0]["error_type"],
            "ScrapeDoContentError",
        )

    def test_partial_catalog_is_reported_but_still_joined(self) -> None:
        from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs
        from app.hsguru_decks import HSGuruCatalogPartial

        async def refresh(
            format_name: str,
            rank: str = "legend",
            *,
            period: str,
        ) -> list[dict[str, str]]:
            rows = [{"deck_code": f"{format_name}-{rank}-{period}"}]
            if (format_name, rank) == ("standard", "all"):
                raise HSGuruCatalogPartial(
                    format_name,
                    rows,
                    missing_archetypes=["Quiet Mage"],
                    zero_sample_archetypes=["No Sample DK"],
                )
            return rows

        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                join=lambda: {"joined": True},
            )
        )

        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["datasets"]["standard_all"]["state"], "partial")
        self.assertEqual(
            result["datasets"]["standard_all"]["missing_archetypes"],
            1,
        )
        self.assertEqual(result["archetype_join"], {"joined": True})
        self.assertIn("standard_all", result["errors"])

    def test_partial_catalog_gets_one_continuation_and_recovers(self) -> None:
        from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs
        from app.hsguru_decks import HSGuruCatalogPartial

        calls: list[tuple[str, str]] = []
        periods: list[str] = []

        async def refresh(
            format_name: str,
            rank: str = "legend",
            *,
            period: str,
        ) -> list[dict[str, str]]:
            calls.append((format_name, rank))
            periods.append(period)
            if (format_name, rank) == ("standard", "all") and calls.count(
                (format_name, rank)
            ) == 1:
                raise HSGuruCatalogPartial(
                    format_name,
                    [{"deck_code": "partial-standard-all"}],
                    missing_archetypes=["Quiet Mage"],
                    zero_sample_archetypes=[],
                )
            if (format_name, rank) == ("standard", "all"):
                return [
                    {"deck_code": "complete-standard-all-1"},
                    {"deck_code": "complete-standard-all-2"},
                ]
            return [{"deck_code": f"{format_name}-{rank}-{period}"}]

        def load_persisted(source_id: str) -> dict[str, object]:
            format_name = "standard" if "standard" in source_id else "wild"
            rows = (
                [
                    {"deck_code": "complete-standard-all-1"},
                    {"deck_code": "complete-standard-all-2"},
                ]
                if format_name == "standard"
                else [{"deck_code": "wild-all"}]
            )
            return {
                "source_id": source_id,
                "state": "ok",
                "sample_state": (
                    "sparse_post_patch" if format_name == "standard" else "complete"
                ),
                "period": periods[-1],
                "criteria": {
                    "format": format_name,
                    "rank": "all",
                    "period": periods[-1],
                },
                "missing_archetypes": [],
                "zero_sample_archetypes": (
                    ["No Sample DK"] if format_name == "standard" else []
                ),
                "data": rows,
            }

        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                join=lambda: {"joined": True},
                load_persisted=load_persisted,
            )
        )

        self.assertEqual(
            calls,
            [
                ("standard", "legend"),
                ("wild", "legend"),
                ("standard", "all"),
                ("standard", "all"),
                ("wild", "all"),
            ],
        )
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["errors"], {})
        self.assertEqual(
            result["datasets"]["standard_all"],
            {
                "decks": 2,
                "state": "ok",
                "missing_archetypes": 0,
                "zero_sample_archetypes": 1,
                "sample_state": "sparse_post_patch",
            },
        )
        self.assertEqual(
            result["datasets"]["standard_legend"],
            {"decks": 1, "state": "ok"},
        )
        self.assertEqual(
            result["continuations"]["standard_all"]["state"],
            "ok",
        )
        self.assertIn(
            "HSGuruCatalogPartial",
            result["continuations"]["standard_all"]["initial_error"],
        )

    def test_partial_catalog_stops_after_one_continuation(self) -> None:
        from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs
        from app.hsguru_decks import HSGuruCatalogPartial

        calls: list[tuple[str, str]] = []

        async def refresh(
            format_name: str,
            rank: str = "legend",
            *,
            period: str,
        ) -> list[dict[str, str]]:
            calls.append((format_name, rank))
            if (format_name, rank) == ("standard", "all"):
                continuation = calls.count((format_name, rank)) == 2
                raise HSGuruCatalogPartial(
                    format_name,
                    [
                        {"deck_code": "partial-standard-all-1"},
                        *(
                            [{"deck_code": "partial-standard-all-2"}]
                            if continuation
                            else []
                        ),
                    ],
                    missing_archetypes=(
                        ["Still Quiet Mage"]
                        if continuation
                        else ["Quiet Mage", "Still Quiet Mage"]
                    ),
                    zero_sample_archetypes=["No Sample DK"],
                )
            return [{"deck_code": f"{format_name}-{rank}-{period}"}]

        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                join=lambda: {"joined": True},
            )
        )

        self.assertEqual(calls.count(("standard", "all")), 2)
        self.assertEqual(calls.count(("standard", "legend")), 1)
        self.assertEqual(calls.count(("wild", "legend")), 1)
        self.assertEqual(calls.count(("wild", "all")), 1)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(
            result["datasets"]["standard_all"],
            {
                "decks": 2,
                "state": "partial",
                "missing_archetypes": 1,
                "zero_sample_archetypes": 1,
            },
        )
        self.assertEqual(
            result["continuations"]["standard_all"]["state"],
            "partial",
        )
        self.assertIn(
            "1 archetypes remain unverified",
            result["errors"]["standard_all"],
        )
        self.assertEqual(result["archetype_join"], {"joined": True})

    def test_catalog_rejects_cross_site_solver_redirect(self) -> None:
        from app import hsguru_decks

        redirected = SimpleNamespace(
            html='<div class="deck_stats_viewport">forged</div>',
            http_status=200,
            backend="flaresolverr",
            final_url="https://hsguru.com.evil.test/decks",
        )

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            hsguru_decks._catalog_page(redirected)


if __name__ == "__main__":
    unittest.main()
