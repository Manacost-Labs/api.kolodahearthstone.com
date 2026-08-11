from __future__ import annotations

import asyncio
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api_only_sources import blocks_browser_fallback
from app.fetch_routes import source_can_run_without_residential_proxy
from app.fetcher import (
    _attach_proxy_egress,
    _preserve_cached_ok_status,
    _refresh_sources_unlocked,
    _refresh_traffic_summary,
    _run_tier_parallel,
    _save_dataset_with_checks,
    _source_uses_residential_proxy,
    _status_payload,
    fetch_source,
)
from app.hsreplay_arena_api import normalize_arena_card_row
from app.hsreplay_bg_heroes import (
    merge_hero_stats,
    parse_hsreplay_bg_hero_stats_text,
    parse_hsreplay_bg_heroes_html,
)
from app.hsreplay_bg_stats import (
    _composition_names_from_text,
    _composition_row,
    _minion_stats,
)
from app.hsreplay_cards_api import (
    _analytics_card_list_url,
    _api_payload_diagnostics,
    parse_cards_from_api_payloads,
)
from app.hsreplay_meta_api import _meta_archetypes_url, normalize_meta_archetypes
from app.proxy_errors import ProxyPaymentRequiredError
from app.refresh_log import runtime_version_info
from app.scrapers.proxy import source_can_use_flaresolverr_without_proxy
from app.scrapers.quality import validate_parsed_data
from app.scrapers.rotator import (
    _backend_failures,
    _open_circuit,
    _ordered_backends,
    classify_backend_error,
    fetch_html,
    reset_backend_circuits,
)
from app.source_tiers import SourceTier, tier_for
from app.sources import SOURCES, Source
from app.storage import load_dataset, load_status, save_dataset, save_status
from app.vicious_live import build_ladder_view, build_power_tier_list, build_table_view
from app.vicious_syndicate import find_radar_url, looks_like_vicious_deck_library


class RefreshStabilityTest(unittest.TestCase):
    def test_rotator_preserves_typed_proxy_407_for_tier_circuit(self) -> None:
        source = Source(
            id="proxy_407_integration",
            site="hsreplay",
            category="ranked",
            url="https://hsreplay.net/cards/",
        )
        backend = AsyncMock(
            side_effect=ProxyPaymentRequiredError("upstream proxy returned 407")
        )

        with (
            patch("app.scrapers.rotator.assert_proxy_configured"),
            patch(
                "app.scrapers.rotator._ordered_backends",
                return_value=[("patchright", backend, lambda: True)],
            ),
            patch("app.scrapers.rotator.log_action"),
            self.assertRaises(ProxyPaymentRequiredError),
        ):
            asyncio.run(fetch_html(source))

        backend.assert_awaited_once_with(source)

    def test_rotator_tries_proxyless_flaresolverr_after_proxy_407(self) -> None:
        source = Source(
            id="hsreplay_proxyless_recovery",
            site="hsreplay",
            category="ranked",
            url="https://hsreplay.net/cards/",
        )
        paid_proxy = AsyncMock(
            side_effect=ProxyPaymentRequiredError("upstream proxy returned 407")
        )
        recovered = SimpleNamespace(
            html="<html>hsreplay.net" + (" usable data" * 300) + "</html>",
            final_url=source.url,
            backend="flaresolverr",
            http_status=200,
        )
        proxyless = AsyncMock(return_value=recovered)

        with (
            patch("app.scrapers.rotator.assert_proxy_configured"),
            patch(
                "app.scrapers.rotator._ordered_backends",
                return_value=[
                    ("patchright", paid_proxy, lambda: True),
                    ("flaresolverr", proxyless, lambda: True),
                ],
            ),
            patch("app.scrapers.rotator.log_action"),
        ):
            result = asyncio.run(fetch_html(source))

        self.assertIs(result, recovered)
        paid_proxy.assert_awaited_once_with(source)
        proxyless.assert_awaited_once_with(source)

    def test_proxyless_capability_uses_configured_source_routes(self) -> None:
        proxy_source = next(item for item in SOURCES if item.id == "metastats_decks")
        api_source = next(
            item for item in SOURCES if item.id == "firestone_battlegrounds_comps"
        )
        cloud_source = next(
            item for item in SOURCES if item.id == "heartharena_tierlist"
        )

        with (
            patch("app.fetch_routes.fetch_direct_enabled", return_value=False),
            patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set()),
            patch(
                "app.fetch_routes.firecrawl_fallback_source_ids",
                return_value={cloud_source.id},
            ),
        ):
            self.assertFalse(
                source_can_run_without_residential_proxy(
                    proxy_source,
                    default_backends=["patchright"],
                )
            )
            self.assertTrue(
                source_can_run_without_residential_proxy(
                    api_source,
                    default_backends=["patchright"],
                )
            )
            self.assertTrue(
                source_can_run_without_residential_proxy(
                    cloud_source,
                    default_backends=["patchright"],
                )
            )

    def test_proxy_407_skips_dependent_sources_and_preserves_lkg(self) -> None:
        failing_source = next(item for item in SOURCES if item.id == "metastats_decks")
        cached_source = next(
            item for item in SOURCES if item.id == "metastats_matchups"
        )
        cold_source = next(item for item in SOURCES if item.id == "hearthstone_decks")
        cloud_source = next(
            item for item in SOURCES if item.id == "heartharena_tierlist"
        )
        api_source = next(
            item for item in SOURCES if item.id == "firestone_battlegrounds_comps"
        )

        async def fetch(_client, source):
            if source.id == failing_source.id:
                raise ProxyPaymentRequiredError("proxy returned 407")
            return {"source_id": source.id, "state": "ok", "backend": "cloud_or_api"}

        with (
            TemporaryDirectory() as td,
            patch("app.storage.data_dir", return_value=Path(td)),
            patch("app.fetcher.validate_candidate_for_publish") as validate,
            patch("app.fetcher.fetch_source", side_effect=fetch) as fetch_mock,
            patch("app.fetcher._parallel_stagger_delay", new=AsyncMock()),
            patch("app.fetcher.log_action"),
            patch("app.fetch_routes.fetch_direct_enabled", return_value=False),
            patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set()),
            patch(
                "app.fetch_routes.firecrawl_fallback_source_ids",
                return_value={cloud_source.id},
            ),
        ):
            validate.return_value = SimpleNamespace(ok=True, reason="ok")
            save_dataset(
                cached_source.id,
                {
                    "fetched_at": "2026-08-10T00:00:00+00:00",
                    "backend": "metastats_api",
                    "data": {"structured": {"type": "matchups", "rows": [{"id": 1}]}},
                },
            )
            results = asyncio.run(
                _run_tier_parallel(
                    [
                        failing_source,
                        cached_source,
                        cold_source,
                        cloud_source,
                        api_source,
                    ],
                    phase="light_api",
                    concurrency=1,
                    client=None,
                    proxy_info={},
                )
            )

        called_ids = [call.args[1].id for call in fetch_mock.call_args_list]
        self.assertEqual(
            called_ids,
            [failing_source.id, cloud_source.id, api_source.id],
        )
        self.assertEqual(results[0]["state"], "fetch_error")
        self.assertEqual(results[0]["failure_class"], "proxy_407")
        self.assertEqual(results[1]["state"], "ok")
        self.assertTrue(results[1]["serving_cached_dataset"])
        self.assertEqual(results[1]["last_refresh_failure_class"], "proxy_407")
        self.assertEqual(results[2]["state"], "fetch_error")
        self.assertEqual(results[2]["failure_class"], "proxy_407")
        self.assertEqual(results[3]["state"], "ok")
        self.assertEqual(results[4]["state"], "ok")

    def test_returned_proxy_407_status_opens_tier_circuit(self) -> None:
        failing_source = next(item for item in SOURCES if item.id == "metastats_decks")
        skipped_source = next(
            item for item in SOURCES if item.id == "metastats_matchups"
        )
        independent_source = next(
            item for item in SOURCES if item.id == "firestone_battlegrounds_comps"
        )

        async def fetch(_client, source):
            if source.id == failing_source.id:
                return {
                    "source_id": source.id,
                    "state": "fetch_error",
                    "error": "ProxyPaymentRequiredError",
                    "failure_class": "proxy_407",
                }
            return {"source_id": source.id, "state": "ok", "backend": "firestone_api"}

        with (
            TemporaryDirectory() as td,
            patch("app.storage.data_dir", return_value=Path(td)),
            patch("app.fetcher.fetch_source", side_effect=fetch) as fetch_mock,
            patch("app.fetcher._parallel_stagger_delay", new=AsyncMock()),
            patch("app.fetcher.log_action"),
            patch("app.fetch_routes.fetch_direct_enabled", return_value=False),
            patch("app.fetch_routes.firecrawl_primary_source_ids", return_value=set()),
            patch("app.fetch_routes.firecrawl_fallback_source_ids", return_value=set()),
        ):
            results = asyncio.run(
                _run_tier_parallel(
                    [failing_source, skipped_source, independent_source],
                    phase="light_api",
                    concurrency=1,
                    client=None,
                    proxy_info={},
                )
            )

        called_ids = [call.args[1].id for call in fetch_mock.call_args_list]
        self.assertEqual(called_ids, [failing_source.id, independent_source.id])
        self.assertEqual(results[1]["failure_class"], "proxy_407")
        self.assertEqual(results[2]["state"], "ok")

    def test_fetch_source_retains_proxy_407_failure_class_in_status(self) -> None:
        source = next(item for item in SOURCES if item.id == "metastats_decks")

        with (
            TemporaryDirectory() as td,
            patch("app.storage.data_dir", return_value=Path(td)),
            patch(
                "app.fetcher._fetch_hsreplay_api_source",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.fetcher.fetch_html",
                new=AsyncMock(
                    side_effect=ProxyPaymentRequiredError("proxy returned 407")
                ),
            ),
            patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
            patch("app.fetcher.firecrawl_fallback_source_ids", return_value=set()),
            patch("app.fetcher.send_telegram_alert", new=AsyncMock()),
            patch("app.fetcher._maybe_stale_data_alert", new=AsyncMock()),
            patch("app.fetcher.log_action"),
        ):
            result = asyncio.run(fetch_source(None, source))

        self.assertEqual(result["state"], "fetch_error")
        self.assertEqual(result["failure_class"], "proxy_407")

    def test_preflight_failure_writes_terminal_refresh_event(self) -> None:
        with (
            patch("app.refresh_context.begin_refresh_run") as begin_run,
            patch("app.refresh_context.end_refresh_run") as end_run,
            patch(
                "app.preflight.ensure_refresh_preflight",
                new=AsyncMock(side_effect=RuntimeError("FlareSolverr unavailable")),
            ),
            patch("app.fetcher.log_action") as log_action,
        ):
            with self.assertRaisesRegex(RuntimeError, "FlareSolverr unavailable"):
                asyncio.run(
                    _refresh_sources_unlocked(
                        ["hsguru_meta_standard_legend"],
                    )
                )

        begin_run.assert_called_once_with()
        end_run.assert_called_once_with()
        terminal_events = [
            call
            for call in log_action.call_args_list
            if call.args and call.args[0] == "refresh.end"
        ]
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(terminal_events[0].kwargs["state"], "fetch_error")

    def test_api_source_is_not_blocked_by_missing_global_proxy(self) -> None:
        source = next(item for item in SOURCES if item.id == "firestone_battlegrounds_comps")
        parsed = {
            "source_id": source.id,
            "site": source.site,
            "category": source.category,
            "url": source.url,
            "structured": {"type": "bg_comps", "compositions": [{"name": "Mechs"}]},
            "counts": {"api_bytes": 128},
            "_backend": "firestone_api",
        }

        with (
            TemporaryDirectory() as td,
            patch("app.storage.data_dir", return_value=Path(td)),
            patch("app.fetcher.fetch_proxy_url", return_value=""),
            patch("app.fetcher._fetch_hsreplay_api_source", new=AsyncMock(return_value=parsed)),
            patch(
                "app.fetcher.validate_candidate_for_publish",
                return_value=SimpleNamespace(ok=True, reason="ok", extra={}),
            ),
            patch("app.fetcher.quality_metrics", return_value={"quality_score": 1.0}),
            patch("app.fetcher._save_dataset_with_checks", return_value=(False, None, {})),
        ):
            result = asyncio.run(fetch_source(None, source))

        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["backend"], "firestone_api")

    def test_test_process_refuses_default_production_writes(self) -> None:
        with patch("app.storage.data_dir", return_value=Path("/var/lib/hs-data-api")):
            with self.assertRaisesRegex(RuntimeError, "Refusing to write production parser data"):
                save_status("test_should_not_touch_prod", {"state": "ok"})

    def test_save_dataset_keeps_backup_before_overwrite(self) -> None:
        with TemporaryDirectory() as td:
            with patch("app.storage.data_dir", return_value=Path(td)):
                save_dataset("example_source", {"data": {"version": 1}})
                save_dataset("example_source", {"data": {"version": 2}})

                backups = sorted((Path(td) / "backups" / "datasets").glob("example_source.*.json"))

                self.assertEqual(load_dataset("example_source"), {"data": {"version": 2}})
                self.assertEqual(len(backups), 1)
                self.assertIn('"version": 1', backups[0].read_text(encoding="utf-8"))

    def test_backup_retention_is_capped_per_file(self) -> None:
        with TemporaryDirectory() as td:
            with patch("app.storage.data_dir", return_value=Path(td)):
                for version in range(8):
                    save_dataset("example_source", {"data": {"version": version}})

                backups = sorted((Path(td) / "backups" / "datasets").glob("example_source.*.json"))

                self.assertLessEqual(len(backups), 5)

    def test_status_payload_includes_runtime_metadata(self) -> None:
        source = Source(
            id="example_source",
            site="example",
            category="test",
            url="https://example.invalid",
        )
        with patch("app.fetcher.runtime_version_info", return_value={"build_id": "test"}):
            status = _status_payload(source, "ok", fetched_at="2026-06-07T00:00:00+00:00")

        self.assertEqual(status["runtime"], {"build_id": "test"})

    def test_saved_dataset_includes_runtime_metadata(self) -> None:
        source = Source(
            id="example_source",
            site="example",
            category="test",
            url="https://example.invalid",
        )
        with TemporaryDirectory() as td:
            with (
                patch("app.storage.data_dir", return_value=Path(td)),
                patch("app.fetcher.runtime_version_info", return_value={"build_id": "test"}),
            ):
                _save_dataset_with_checks(
                    source,
                    {"data": {"text_preview": ["ok"] * 10}},
                    fetched_at="2026-06-07T00:00:00+00:00",
                )
                saved = load_dataset(source.id)

        assert saved is not None
        self.assertEqual(saved["runtime"], {"build_id": "test"})

    def test_runtime_version_info_is_cached(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="abc123\n",
            stderr="",
        )
        runtime_version_info.cache_clear()
        try:
            with patch("app.refresh_log.subprocess.run", return_value=completed) as run:
                first = runtime_version_info()
                second = runtime_version_info()
        finally:
            runtime_version_info.cache_clear()

        self.assertEqual(first, second)
        self.assertEqual(first["git_commit"], "abc123")
        self.assertIn("app", first)
        self.assertNotIn("app_root", first)
        run.assert_called_once()

    def test_refresh_traffic_summary_counts_body_bytes(self) -> None:
        summary = _refresh_traffic_summary(
            [
                {
                    "source_id": "hsreplay_cards_legend_included_winrate",
                    "site": "hsreplay",
                    "backend": "hsreplay_cards_api",
                    "content_length": 1024 * 1024,
                    "used_residential_proxy": True,
                },
                {
                    "source_id": "vicious_syndicate_radars",
                    "site": "vicious-syndicate",
                    "backend": "vicious_syndicate_api",
                    "content_length": 512 * 1024,
                },
                {
                    "source_id": "hsguru_meta_standard_legend",
                    "site": "hsguru",
                    "backend": "flaresolverr",
                    "content_length": 0,
                },
                {
                    "source_id": "hsreplay_arena_winning_decks",
                    "site": "hsreplay",
                    "backend": "hsreplay_api",
                    "content_length": 8 * 1024 * 1024,
                    "used_residential_proxy": True,
                    "serving_cached_dataset": True,
                },
            ]
        )

        self.assertEqual(summary["body_bytes_lower_bound"], 1572864)
        self.assertEqual(summary["body_mb_lower_bound"], 1.5)
        self.assertEqual(summary["iproyal_body_bytes_estimate"], 1048576)
        self.assertEqual(summary["iproyal_body_mb_estimate"], 1.0)
        self.assertEqual(summary["by_tier_mb"]["browser_patchright"], 1.0)
        self.assertEqual(summary["by_tier_mb"]["medium_api"], 0.5)
        self.assertEqual(summary["by_backend_mb"]["hsreplay_cards_api"], 1.0)
        self.assertEqual(summary["skipped_cached_sources"], 1)
        self.assertFalse(summary["billing_exact"])

    def test_firestone_api_is_not_marked_as_residential_proxy(self) -> None:
        source = Source(
            id="firestone_arena_cards_normal",
            site="firestone",
            category="arena",
            url="https://www.firestoneapp.com/",
        )
        with patch.dict("os.environ", {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"}):
            self.assertFalse(_source_uses_residential_proxy(source, "firestone_api"))

    def test_hsreplay_premium_flaresolverr_is_not_marked_as_residential_proxy(self) -> None:
        source = Source(
            id="hsreplay_battlegrounds_heroes",
            site="hsreplay",
            category="battlegrounds",
            url="https://hsreplay.net/battlegrounds/heroes/",
        )
        with patch.dict("os.environ", {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"}):
            self.assertFalse(_source_uses_residential_proxy(source, "hsreplay_premium_flaresolverr"))

    def test_proxy_egress_is_only_attached_to_residential_fetches(self) -> None:
        status = {"state": "ok", "used_residential_proxy": False}

        attached = _attach_proxy_egress(status, {"egress_ip": "203.0.113.1"})

        self.assertNotIn("proxy_egress_ip", attached)

    def test_hsreplay_card_payload_diagnostics_counts_rows(self) -> None:
        diagnostics = _api_payload_diagnostics(
            [
                (
                    "https://hsreplay.net/analytics/query/card_list/",
                    {"data": [{"dbfId": 1, "includedWinrate": 55.0}]},
                )
            ]
        )

        self.assertEqual(diagnostics["api_payloads"], 1)
        self.assertEqual(diagnostics["api_payload_rows_total"], 1)

    def test_vicious_live_ladder_view_builds_class_and_deck_distribution(self) -> None:
        games_per_rank = [100] * 51
        class_rank_data = [[0] * 11 for _ in range(51)]
        rank_data = [[0] * 2 for _ in range(51)]
        for row in range(51):
            class_rank_data[row][0] = 70
            class_rank_data[row][1] = 30
            rank_data[row][0] = 70
            rank_data[row][1] = 30

        view = build_ladder_view(
            {
                "archetypes": [["DeathKnight", "Alpha"], ["DemonHunter", "Beta"]],
                "gamesPerRank": games_per_rank,
                "classRankData": class_rank_data,
                "rankData": rank_data,
            }
        )

        self.assertEqual(view["games"], 5100)
        self.assertEqual(view["class_distribution"][0]["class"], "DeathKnight")
        self.assertEqual(view["class_distribution"][0]["frequency"], "70.00%")
        self.assertEqual(view["deck_distribution"][0]["deck"], "Alpha DeathKnight")
        self.assertEqual(view["deck_distribution"][0]["frequency"], "70.00%")

    def test_vicious_live_power_tier_list_uses_matchup_weighted_winrate(self) -> None:
        ladder_view = {
            "ladder_archetypes": [
                {"name": "Alpha DeathKnight", "fr_ranks": [0.6] * 20},
                {"name": "Beta DemonHunter", "fr_ranks": [0.4] * 20},
            ]
        }
        table_view = build_table_view(
            {
                "archetypes": [["DeathKnight", "Alpha"], ["DemonHunter", "Beta"]],
                "frequency": [0.6, 0.4],
                "table": [
                    [[50, 50], [60, 40]],
                    [[40, 60], [50, 50]],
                ],
            },
            num_arch=2,
        )

        tier_list = build_power_tier_list(ladder_view, table_view, limit=2)

        self.assertEqual(tier_list[0]["rank_bracket"], "All ranks")
        self.assertEqual(tier_list[0]["decks"][0]["deck"], "Alpha DeathKnight")
        self.assertEqual(tier_list[0]["decks"][0]["winrate"], "54.00%")
        self.assertEqual(tier_list[0]["decks"][1]["deck"], "Beta DemonHunter")
        self.assertEqual(tier_list[0]["decks"][1]["winrate"], "44.00%")

    def test_hsreplay_meta_archetypes_url_uses_requested_filters(self) -> None:
        source = Source(
            id="hsreplay_meta_archetypes_legend_eu_1d",
            site="hsreplay",
            category="ranked",
            url=(
                "https://hsreplay.net/meta/#rankRange=LEGEND&tab=archetypes&region=REGION_EU"
                "&timeFrame=LAST_1_DAY&popularitySortBy=rank51"
            ),
        )

        self.assertEqual(
            _meta_archetypes_url(source),
            "https://hsreplay.net/analytics/query/archetype_popularity_distribution_stats_v2/"
            "?GameType=RANKED_STANDARD&LeagueRankRange=LEGEND&Region=REGION_EU&TimeRange=LAST_1_DAY",
        )
        self.assertEqual(tier_for(source.id), SourceTier.MEDIUM_API)

    def test_hsreplay_meta_archetypes_normalizes_class_groups(self) -> None:
        payload = {
            "series": {
                "data": {
                    "DRUID": [
                        {
                            "archetype_id": 52,
                            "total_games": 1200,
                            "pct_of_class": 60.0,
                            "pct_of_total": 6.5,
                            "win_rate": 51.2,
                        },
                        {
                            "archetype_id": -2,
                            "total_games": 100,
                            "pct_of_class": 5.0,
                            "pct_of_total": 0.5,
                            "win_rate": 44.0,
                        },
                    ],
                    "MAGE": [
                        {
                            "archetype_id": 142,
                            "total_games": 900,
                            "pct_of_class": 90.0,
                            "pct_of_total": 4.8,
                            "win_rate": 50.0,
                        }
                    ],
                }
            }
        }

        classes = normalize_meta_archetypes(
            payload,
            {
                52: {"name": "Токен друид", "url": "/archetypes/52/token-druid"},
                142: {"name": "Бёрн маг", "url": "/archetypes/142/burn-mage"},
            },
        )

        self.assertEqual(classes[0]["class"], "DRUID")
        self.assertEqual(classes[0]["class_name"], "Друид")
        self.assertEqual(classes[0]["games"], 1300)
        self.assertEqual(classes[0]["archetypes"][0]["archetype"], "Токен друид")
        self.assertEqual(classes[0]["archetypes"][0]["winrate"], "51.20%")
        self.assertEqual(classes[0]["archetypes"][0]["popularity"], "6.50%")
        self.assertEqual(classes[0]["archetypes"][0]["class_popularity"], "60.00%")
        self.assertEqual(classes[0]["archetypes"][1]["archetype"], "Другое (Друид)")

    def test_hsreplay_card_api_preserves_legend_1d_metrics(self) -> None:
        cards = parse_cards_from_api_payloads(
            [
                (
                    "https://hsreplay.net/analytics/query/card_list/",
                    {
                        "series": {
                            "data": {
                                "rows": [
                                    {
                                        "dbf_id": 69545,
                                        "included_popularity": 24.9,
                                        "included_count": 2.0,
                                        "included_winrate": 52.01,
                                        "times_played": 6209,
                                        "winrate_when_played": 52.64,
                                        "winrate_when_drawn": 53.5,
                                        "keep_percentage": 56.8,
                                        "avg_turns_in_hand": 0.75,
                                        "avg_turn_played_on": 4.53,
                                    }
                                ]
                            }
                        }
                    },
                )
            ],
            sort_mode="popularity",
        )

        self.assertEqual(cards[0]["deck_popularity"], "24.90%")
        self.assertEqual(cards[0]["avg_copies"], 2.0)
        self.assertEqual(cards[0]["deck_winrate"], "52.01%")
        self.assertEqual(cards[0]["times_played"], 6209)
        self.assertEqual(cards[0]["winrate_when_played"], "52.64%")
        self.assertEqual(cards[0]["winrate_when_drawn"], "53.50%")
        self.assertEqual(cards[0]["keep_percentage"], "56.80%")
        self.assertEqual(cards[0]["avg_turns_in_hand"], 0.75)
        self.assertEqual(cards[0]["avg_turn_played_on"], 4.53)

    def test_hsreplay_cards_legend_1d_source_is_api_first(self) -> None:
        source = next(s for s in SOURCES if s.id == "hsreplay_cards_legend_1d")

        self.assertEqual(
            source.fragment,
            "rankRange=LEGEND&sortBy=includedPopularity"
            "&timeRange=LAST_1_DAY&gameType=RANKED_STANDARD",
        )
        self.assertEqual(tier_for(source.id), SourceTier.MEDIUM_API)
        self.assertTrue(blocks_browser_fallback(source.id))

    def test_hsreplay_cards_wild_legend_1d_source_uses_wild_api(self) -> None:
        source = next(s for s in SOURCES if s.id == "hsreplay_cards_wild_legend_1d")

        self.assertEqual(
            source.fragment,
            "rankRange=LEGEND&sortBy=includedPopularity"
            "&timeRange=LAST_1_DAY&gameType=RANKED_WILD",
        )
        self.assertEqual(
            _analytics_card_list_url(source),
            "https://hsreplay.net/analytics/query/card_list/"
            "?GameType=RANKED_WILD&TimeRange=LAST_1_DAY&LeagueRankRange=LEGEND",
        )
        self.assertEqual(tier_for(source.id), SourceTier.MEDIUM_API)
        self.assertTrue(blocks_browser_fallback(source.id))

    def test_hsreplay_arena_card_row_preserves_arenasmith_metrics(self) -> None:
        card = normalize_arena_card_row(
            {
                "card_id": "CATA_488",
                "win_rate": 62.6,
                "drawn_winrate": 72.6,
                "played_winrate": 76.9,
                "popularity": 0.4,
                "avg_copies_in_deck": 1.0,
                "num_games": 970,
                "score": 52.0,
                "pick_rate": 67.8,
            }
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card["id"], "CATA_488")
        self.assertEqual(card["deck_winrate"], "62.60%")
        self.assertEqual(card["winrate_when_drawn"], "72.60%")
        self.assertEqual(card["winrate_when_played"], "76.90%")
        self.assertEqual(card["in_runs"], "0.40%")
        self.assertEqual(card["avg_copies"], 1.0)
        self.assertEqual(card["times_played"], 970)
        self.assertEqual(card["score"], 52.0)
        self.assertEqual(card["pick_rate"], 67.8)

    def test_hsreplay_bg_minion_stats_calculates_requested_metrics(self) -> None:
        item = _minion_stats(
            {
                "minion_dbf_id": 59670,
                "minion_tier": 1,
                "normal_aggregates": [
                    {
                        "combat_round": 1,
                        "sum_of_placements_for_players_without_minion": 500,
                        "count_of_games_without_minion": 100,
                        "sum_of_placements_for_players_with_minion": 80,
                        "count_of_games_with_minion": 20,
                        "total_wins": 30,
                        "total_losses": 10,
                    }
                ],
            }
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["minion_dbf_id"], 59670)
        self.assertEqual(item["impact"], 1.0)
        self.assertEqual(item["win_share"], "75.00%")
        self.assertEqual(item["popularity"], "16.67%")

    def test_hsreplay_bg_composition_row_calculates_first_place_share(self) -> None:
        from app.hsreplay_bg_stats import _first_place_share

        rows = [
            {
                "friendly_composition": 20,
                "num_games": 119526,
                "avg_final_placement": 4.272,
                "final_placement_distribution": [15.92],
                "popularity": 14.59,
            },
            {
                "friendly_composition": 8,
                "num_games": 95367,
                "avg_final_placement": 4.137,
                "final_placement_distribution": [16.85],
                "popularity": 11.64,
            },
        ]
        shares = _first_place_share(rows)
        item = _composition_row(
            rows[0],
            {20: "Драконы", 8: "Механизмы"},
            shares,
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["type"], "Драконы")
        self.assertEqual(item["first_place"], "54.22%")
        self.assertEqual(item["avg_placement"], 4.27)
        self.assertEqual(item["popularity"], "14.59%")
        self.assertEqual(item["placement_distribution"][0], "15.92%")

    def test_hsreplay_bg_composition_names_parse_api_pre_payload(self) -> None:
        names = _composition_names_from_text(
            '<html><body><pre>[{"id":20,"name":"Dragons"},{"friendly_composition":8,"comp_name":"Mechs"}]</pre></body></html>'
        )

        self.assertEqual(names, {20: "Драконы", 8: "Механизмы"})

    def test_hsreplay_bg_heroes_html_extracts_premium_rows(self) -> None:
        html = """
        <html><body>
            <div class="tier-block">s
            <div class="hero-card">
              <a href="/battlegrounds/heroes/57946/-?mmrPercentile=TOP_50_PERCENT&timeRange=LAST_7_DAYS">
                Миллифисент Манашторм
              </a>
              1 Вы раскапываете механизм с «Магнетизмом».
              37.1% Механизмы 3,68
              1 место: 20.76% 2 место: 18.92% 4. 8.
            </div>
          </div>
        </body></html>
        """

        heroes = parse_hsreplay_bg_heroes_html(html)

        self.assertEqual(
            heroes,
            [
                {
                    "hero": "Миллифисент Манашторм",
                    "dbfId": 57946,
                    "pick_rate": "37.1%",
                    "best_comp": "Механизмы",
                    "avg_placement": "3,68",
                    "tier": "S",
                }
            ],
        )

    def test_hsreplay_bg_heroes_stats_extracts_placement_distribution(self) -> None:
        stats = parse_hsreplay_bg_hero_stats_text(
            """
            <html><body><pre>HTTP 200 OK

            [
              {
                "hero_dbf_id": 57946,
                "pick_rate": 37.12,
                "avg_final_placement": 3.681,
                "final_placement_distribution": [20.76, 18.92, 14.5, 12.1, 10.0, 9.0, 8.0, 6.72],
                "tier_v2": "s",
                "best_composition": 8
              }
            ]</pre></body></html>
            """
        )

        self.assertEqual(
            stats[57946]["placement_distribution"],
            ["20.76%", "18.92%", "14.50%", "12.10%", "10.00%", "9.00%", "8.00%", "6.72%"],
        )
        self.assertEqual(stats[57946]["tier_v2"], "S")
        self.assertEqual(stats[57946]["best_composition_id"], 8)

    def test_hsreplay_bg_heroes_stats_extracts_object_payload(self) -> None:
        stats = parse_hsreplay_bg_hero_stats_text(
            """
            <html><body><pre>HTTP 200 OK

            {"results": [
              {
                "hero_dbf_id": 57946,
                "final_placement_distribution": [20.76, 18.92],
                "tier_v2": "s"
              }
            ]}</pre></body></html>
            """
        )

        self.assertEqual(stats[57946]["placement_distribution"], ["20.76%", "18.92%"])

    def test_hsreplay_bg_heroes_merge_stats_adds_distribution(self) -> None:
        heroes = [{"hero": "Миллифисент Манашторм", "dbfId": 57946, "tier": "A"}]
        merged = merge_hero_stats(
            heroes,
            {
                57946: {
                    "placement_distribution": ["20.76%", "18.92%"],
                    "tier_v2": "S",
                    "best_composition_id": 8,
                }
            },
        )

        self.assertEqual(merged[0]["placement_distribution"], ["20.76%", "18.92%"])
        self.assertEqual(merged[0]["tier"], "S")
        self.assertEqual(merged[0]["best_composition_id"], 8)

    def test_hsreplay_bg_heroes_quality_accepts_premium_rows_without_game_counts(self) -> None:
        source = Source(
            id="hsreplay_battlegrounds_heroes",
            site="hsreplay",
            category="battlegrounds",
            url="https://hsreplay.net/battlegrounds/heroes/",
        )
        # The fixture models real premium rows WITHOUT game counts but must
        # otherwise be realistic: the semantic validator _validate_bg_heroes
        # (app/source_validators.py:66-175) requires unique dbfIds (>=70% of
        # rows), avg_placement diversity (>=10 distinct values in 1.0..8.0),
        # tier diversity (>=2 tiers), and per-row placement_distribution as a
        # LIST of 8 percent buckets summing to ~100 (98..102) for >=70% of rows.
        parsed = {
            "title": "HSReplay Battlegrounds heroes",
            "structured": {
                "type": "bg_heroes",
                "blocked": False,
                "heroes": [
                    {
                        "hero": f"Hero {idx}",
                        "dbfId": 100000 + idx,
                        "pick_rate": f"{3.0 + idx * 0.1:.1f}%",
                        # locale format with comma is accepted by _parse_decimal
                        # (app/source_validators.py:52-59)
                        "avg_placement": f"{4.0 + idx * 0.05:.2f}".replace(".", ","),
                        "best_comp": "Механизмы",
                        "tier": ["S", "A", "B", "C", "D"][idx % 5],
                        # 8 buckets, sum = 100%
                        "placement_distribution": ["12.5%"] * 8,
                    }
                    for idx in range(30)
                ],
            }
        }

        self.assertEqual(validate_parsed_data(source, parsed), (True, "ok"))

    def test_vicious_radar_url_discovery_uses_alternate_links(self) -> None:
        html = '<html><body><a href="/radar/some-class/index.html">Radar</a></body></html>'

        self.assertEqual(
            find_radar_url(html, base_url="https://www.vicioussyndicate.com/deck-library/mage-decks/"),
            "https://www.vicioussyndicate.com/radar/some-class/index.html",
        )

    def test_vicious_deck_library_html_is_usable_despite_ad_scripts(self) -> None:
        html = """
        <html><head><script src="https://btloader.com/tag"></script></head>
        <body class="vicioussyndicate.com"><main class="mh-content">
        <a href="/deck-library/druid-decks/ramp-druid/">Ramp Druid</a>
        <a href="/decks/example-deck/">Deck</a>
        </main></body></html>
        """

        self.assertTrue(looks_like_vicious_deck_library(html))

    def test_backend_error_classification(self) -> None:
        self.assertEqual(
            classify_backend_error("Error", "net::ERR_NAME_NOT_RESOLVED"),
            "dns_error",
        )
        self.assertEqual(
            classify_backend_error("RuntimeError", "quality check failed: card stats too few (0)"),
            "quality_empty",
        )

    def test_backend_circuit_opens_after_repeated_classification(self) -> None:
        source = Source(
            id="hsguru_meta_standard_legend",
            site="hsguru",
            category="meta",
            url="https://www.hsguru.com/meta",
        )
        _backend_failures.clear()
        try:
            _backend_failures[(f"site:{source.site}", "patchright", "dns_error")] = 2
            self.assertEqual(_open_circuit(source, "patchright"), ("dns_error", 2))
            self.assertIsNone(_open_circuit(source, "flaresolverr"))
        finally:
            _backend_failures.clear()

    def test_quality_circuit_is_source_scoped(self) -> None:
        source = Source(
            id="hsreplay_cards_legend_included_winrate",
            site="hsreplay",
            category="ranked",
            url="https://hsreplay.net/cards/",
        )
        other = Source(
            id="hsreplay_cards_legend_included_popularity",
            site="hsreplay",
            category="ranked",
            url="https://hsreplay.net/cards/",
        )
        _backend_failures.clear()
        try:
            _backend_failures[(f"source:{source.id}", "flaresolverr", "quality_empty")] = 2
            self.assertEqual(_open_circuit(source, "flaresolverr"), ("quality_empty", 2))
            self.assertIsNone(_open_circuit(other, "flaresolverr"))
        finally:
            _backend_failures.clear()

    def test_backend_circuit_reset_clears_failures(self) -> None:
        _backend_failures[(("hsreplay", "patchright", "dns_error"))] = 2
        reset_backend_circuits()

        self.assertEqual(_backend_failures, {})

    def test_hsguru_and_hsreplay_flaresolverr_can_skip_proxy_requirement(self) -> None:
        self.assertTrue(
            source_can_use_flaresolverr_without_proxy(
                Source(
                    id="hsguru_meta_standard_legend",
                    site="hsguru",
                    category="meta",
                    url="https://www.hsguru.com/meta",
                )
            )
        )
        self.assertTrue(
            source_can_use_flaresolverr_without_proxy(
                Source(
                    id="hsreplay_decks_trending",
                    site="hsreplay",
                    category="decks",
                    url="https://hsreplay.net/decks/trending/",
                )
            )
        )

    def test_preserve_cached_ok_status_when_live_refresh_fails(self) -> None:
        source = Source(
            id="hsguru_meta_standard_legend",
            site="hsguru",
            category="meta",
            url="https://www.hsguru.com/meta",
        )
        with TemporaryDirectory() as td:
            with patch("app.storage.data_dir", return_value=Path(td)):
                save_dataset(
                    source.id,
                    {
                        "fetched_at": "2026-06-04T10:00:00+00:00",
                        "backend": "flaresolverr",
                        "content_length": 1234,
                        "data": {
                            "title": "HSGuru Meta",
                            "tables": [
                                {
                                    "objects": [
                                        {"deck": "A"},
                                        {"deck": "B"},
                                        {"deck": "C"},
                                        {"deck": "D"},
                                        {"deck": "E"},
                                    ]
                                }
                            ],
                        },
                    },
                )
                failed = {
                    "source_id": source.id,
                    "state": "blocked_by_protection",
                    "fetched_at": "2026-06-04T20:00:00+00:00",
                    "detail": "Cloudflare challenge after all backends.",
                    "backend": "direct",
                    "content_length": 5500,
                }

                out = _preserve_cached_ok_status(source, failed)

                self.assertIsNotNone(out)
                assert out is not None
                self.assertEqual(out["state"], "ok")
                self.assertEqual(out["backend"], "flaresolverr")
                self.assertEqual(out["content_length"], 1234)
                self.assertEqual(out["last_refresh_state"], "blocked_by_protection")
                self.assertIn("Cloudflare challenge", out["last_refresh_error"])
                self.assertEqual(load_status(source.id), out)

    def test_preserve_cached_status_uses_resolved_stable_publication(self) -> None:
        source = Source(
            id="hsguru_meta_standard_legend",
            site="hsguru",
            category="meta",
            url="https://www.hsguru.com/meta",
        )
        stable = {
            "fetched_at": "2026-06-04T10:00:00+00:00",
            "backend": "stable-lkg",
            "data": {
                "title": "HSGuru Meta",
                "tables": [
                    {
                        "objects": [
                            {"deck": name}
                            for name in ("A", "B", "C", "D", "E")
                        ]
                    }
                ]
            },
        }
        failed = {
            "state": "fetch_error",
            "fetched_at": "2026-06-05T10:00:00+00:00",
            "detail": "temporary upstream failure",
        }

        with TemporaryDirectory() as td, patch(
            "app.storage.data_dir", return_value=Path(td)
        ), patch(
            "app.parser_control.load_resolved_public_dataset", return_value=stable
        ) as resolved:
            out = _preserve_cached_ok_status(source, failed)

        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["backend"], "stable-lkg")
        self.assertTrue(out["serving_cached_dataset"])
        resolved.assert_called_once_with(source.id)

    def test_hsreplay_with_auth_keeps_fallback_backends_after_patchright(self) -> None:
        source = Source(
            id="hsreplay_battlegrounds_trinkets_lesser",
            site="hsreplay",
            category="battlegrounds",
            url="https://hsreplay.net/battlegrounds/trinkets/",
        )
        with TemporaryDirectory() as td:
            storage = Path(td) / "hsreplay-auth.json"
            storage.write_text("{}", encoding="utf-8")
            with (
                patch(
                    "app.config.fetch_backends",
                    return_value=["flaresolverr", "scrapling", "patchright", "curl_cffi"],
                ),
                patch("app.config.hsreplay_storage_path", return_value=storage),
            ):
                names = [name for name, _fn, _available in _ordered_backends(source)]

        self.assertEqual(names[0], "patchright")
        self.assertIn("flaresolverr", names)
        self.assertIn("scrapling", names)


class HSReplayCardsApiFirstTest(unittest.IsolatedAsyncioTestCase):
    async def test_ranked_cards_use_api_before_browser(self) -> None:
        source = Source(
            id="hsreplay_cards_legend_included_popularity",
            url="https://hsreplay.net/cards/#rankRange=GOLD&sortBy=includedPopularity&timeRange=LAST_14_DAYS",
            site="hsreplay",
            category="ranked",
        )
        cards = [{"dbfId": i, "deck_popularity": "1%"} for i in range(40)]
        with (
            patch("app.hsreplay_client.fetch_hsreplay_json", new_callable=AsyncMock) as fetch_json,
            patch("app.hsreplay_cards_api.parse_cards_from_api_payloads", return_value=cards),
            patch("app.scrapers.browser_pool.PatchrightPool.get", new_callable=AsyncMock) as pool_get,
        ):
            fetch_json.return_value = {"series": {"data": []}}
            from app.hsreplay_cards_api import fetch_hsreplay_ranked_cards

            structured = await fetch_hsreplay_ranked_cards(source)

        self.assertEqual(structured["source"]["backend"], "hsreplay_cards_api")
        self.assertEqual(len(structured["cards"]), 40)
        pool_get.assert_not_called()

    async def test_sparse_ranked_cards_api_does_not_use_browser_fallback(self) -> None:
        source = Source(
            id="hsreplay_cards_legend_included_popularity",
            url="https://hsreplay.net/cards/#rankRange=GOLD&sortBy=includedPopularity&timeRange=LAST_14_DAYS",
            site="hsreplay",
            category="ranked",
        )
        cards = [{"dbfId": 1}]
        with (
            patch("app.hsreplay_client.fetch_hsreplay_json", new_callable=AsyncMock) as fetch_json,
            patch("app.hsreplay_cards_api.parse_cards_from_api_payloads", return_value=cards),
            patch("app.scrapers.browser_pool.PatchrightPool.get", new_callable=AsyncMock) as pool_get,
            patch("app.refresh_log.log_action"),
        ):
            fetch_json.return_value = {"series": {"data": []}}
            from app.hsreplay_cards_api import fetch_hsreplay_ranked_cards

            with self.assertRaisesRegex(RuntimeError, "HSReplay cards API sparse"):
                await fetch_hsreplay_ranked_cards(source)

        pool_get.assert_not_called()

    def test_hsreplay_cards_block_browser_fallback(self) -> None:
        self.assertTrue(blocks_browser_fallback("hsreplay_cards_legend_included_winrate"))
        self.assertTrue(blocks_browser_fallback("hsreplay_cards_legend_included_popularity"))
        self.assertTrue(blocks_browser_fallback("hsreplay_cards_legend_1d"))
        self.assertTrue(blocks_browser_fallback("hsreplay_cards_wild_legend_1d"))
        self.assertTrue(blocks_browser_fallback("hsreplay_arena_cards_advanced"))


if __name__ == "__main__":
    unittest.main()
