from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from app import cli
from app.resource_locks import ResourceLocked
from app.sources import Source


class CliTest(unittest.TestCase):
    def test_brightdata_usage_bootstrap_uses_configured_limit(self) -> None:
        snapshot = type(
            "Snapshot",
            (),
            {"billed_requests": 4, "remaining_requests": 6},
        )()
        with (
            patch(
                "app.config.brightdata_monthly_billable_limit",
                return_value=10,
            ),
            patch(
                "app.brightdata_state.initialize_usage_state",
                return_value=snapshot,
            ) as initialize,
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(
                ["brightdata-init-usage", "--billed-requests", "4"]
            )

        self.assertEqual(exit_code, 0)
        initialize.assert_called_once_with(monthly_limit=10, billed_requests=4)

    def test_scheduled_bg_hero_details_fresh_result_is_success(self) -> None:
        result = {
            "ok": True,
            "published": True,
            "serving_cached_dataset": False,
        }
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
                new=AsyncMock(return_value=result),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["refresh-bg-hero-details", "--scheduled"])

        self.assertEqual(exit_code, 0)

    def test_scheduled_bg_hero_details_cached_result_is_degraded(self) -> None:
        result = {
            "ok": False,
            "published": False,
            "serving_cached_dataset": True,
        }
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
                new=AsyncMock(return_value=result),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["refresh-bg-hero-details", "--scheduled"])

        self.assertEqual(exit_code, 10)

    def test_scheduled_bg_hero_details_cold_failure_is_error(self) -> None:
        result = {
            "ok": False,
            "published": False,
            "serving_cached_dataset": False,
        }
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
                new=AsyncMock(return_value=result),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["refresh-bg-hero-details", "--scheduled"])

        self.assertEqual(exit_code, 1)

    def test_api_tier_aggregator_uses_error_then_degraded_then_success_precedence(
        self,
    ) -> None:
        results_by_exit_code = {
            0: [{"source_id": "fresh", "state": "ok"}],
            10: [
                {
                    "source_id": "cached",
                    "state": "partial",
                    "serving_cached_dataset": True,
                }
            ],
            1: [{"source_id": "cold", "state": "fetch_error"}],
        }
        cases = (
            ((0, 0), 0),
            ((0, 10), 10),
            ((10, 0), 10),
            ((10, 10), 10),
            ((0, 1), 1),
            ((1, 0), 1),
            ((10, 1), 1),
            ((1, 10), 1),
            ((1, 1), 1),
        )

        for tier_codes, expected in cases:
            with self.subTest(tier_codes=tier_codes):
                refresh = AsyncMock(
                    side_effect=[
                        results_by_exit_code[tier_codes[0]],
                        results_by_exit_code[tier_codes[1]],
                    ]
                )
                with (
                    patch("app.cli.refresh_sources", new=refresh),
                    redirect_stdout(io.StringIO()),
                ):
                    exit_code = cli.main(["refresh-api-tiers"])

                self.assertEqual(exit_code, expected)
                self.assertEqual(
                    [call.kwargs["tier"] for call in refresh.await_args_list],
                    ["light_api", "medium_api"],
                )
                self.assertTrue(
                    all(
                        call.kwargs["respect_section_controls"] is True
                        for call in refresh.await_args_list
                    )
                )

    def test_scheduled_publishable_matrix_with_cached_slices_is_degraded(self) -> None:
        result = {
            "ok": True,
            "complete": False,
            "state": "partial",
            "serving_cached_dataset": True,
        }
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsguru_meta_matrix.refresh_hsguru_meta_matrix",
                new=AsyncMock(return_value=result),
            ),
        ):
            exit_code = cli.main(["refresh-hsguru-meta-matrix", "--scheduled"])

        self.assertEqual(exit_code, 10)

    def test_scheduled_matrix_with_lkg_is_handled_degradation(self) -> None:
        result = {
            "ok": False,
            "state": "timed_out",
            "serving_cached_dataset": True,
        }
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsguru_meta_matrix.refresh_hsguru_meta_matrix",
                new=AsyncMock(return_value=result),
            ),
        ):
            exit_code = cli.main(["refresh-hsguru-meta-matrix", "--scheduled"])

        self.assertEqual(exit_code, 10)

    def test_scheduled_archetype_partial_with_rows_is_handled_degradation(self) -> None:
        result = {
            "ok": False,
            "state": "partial",
            "archetypes": 271,
            "errors": ["provider unavailable"],
        }
        with patch(
            "app.hsguru_archetype_analysis.refresh_hsguru_archetype_analysis",
            new=AsyncMock(return_value=result),
        ):
            exit_code = cli.main(
                ["refresh-hsguru-archetype-analysis", "--scheduled"]
            )

        self.assertEqual(exit_code, 10)

    def test_scheduled_archetype_retryable_failure_requests_restart(self) -> None:
        result = {
            "ok": False,
            "state": "partial",
            "archetypes": 146,
            "retryable": True,
            "serving_cached_dataset": True,
        }
        with patch(
            "app.hsguru_archetype_analysis.refresh_hsguru_archetype_analysis",
            new=AsyncMock(return_value=result),
        ):
            exit_code = cli.main(
                ["refresh-hsguru-archetype-analysis", "--scheduled"]
            )

        self.assertEqual(exit_code, 1)

    def test_scheduled_archetype_lock_is_handled_degradation(self) -> None:
        result = {
            "ok": True,
            "published": False,
            "state": "locked",
            "skipped": True,
            "reason": "resource_locked",
        }
        with patch(
            "app.hsguru_archetype_analysis.refresh_hsguru_archetype_analysis",
            new=AsyncMock(return_value=result),
        ):
            exit_code = cli.main(
                ["refresh-hsguru-archetype-analysis", "--scheduled"]
            )

        self.assertEqual(exit_code, 10)

    def test_scheduled_refresh_reports_handled_degradation_separately(self) -> None:
        results = [
            {
                "source_id": "hsreplay_arena_cards_advanced",
                "state": "ok",
                "serving_cached_dataset": True,
            }
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--scheduled",
                ]
            )

        self.assertEqual(exit_code, 10)

    def test_scheduled_refresh_without_usable_data_is_error(self) -> None:
        results = [
            {
                "source_id": "heartharena_tierlist",
                "state": "parse_error",
            }
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "heartharena_tierlist",
                    "--scheduled",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_scheduled_refresh_mixed_fresh_and_unusable_failure_is_error(self) -> None:
        results = [
            {
                "source_id": "hsreplay_arena_cards_advanced",
                "state": "ok",
            },
            {
                "source_id": "heartharena_tierlist",
                "state": "fetch_error",
            },
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--source",
                    "heartharena_tierlist",
                    "--scheduled",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_scheduled_refresh_lock_overlap_is_handled_degradation(self) -> None:
        results = [
            {
                "source_id": "heartharena_tierlist",
                "state": "locked",
                "skipped": True,
                "reason": "resource_locked",
            }
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "heartharena_tierlist",
                    "--scheduled",
                ]
            )

        self.assertEqual(exit_code, 10)

    def test_freshness_execution_mode_reports_degradation_not_crash(self) -> None:
        summary = {
            "freshness": {"ok": False, "stale_count": 2},
            "stale_datasets": [{"source_id": "one"}, {"source_id": "two"}],
            "cached_after_failure_sources": [],
            "stale_hours_threshold": 48.0,
        }
        with patch("app.refresh_log.build_summary", return_value=summary):
            exit_code = cli.main(
                ["freshness-check", "--since-hours", "48", "--exit-mode", "execution"]
            )

        self.assertEqual(exit_code, 10)

    def test_refresh_source_uses_global_source_map_after_freshness_imports(self) -> None:
        with patch("app.cli.refresh_sources", return_value=[]) as refresh:
            exit_code = cli.main(["refresh", "--source", "hsguru_meta_wild_top_legend"])

        self.assertEqual(exit_code, 0)
        refresh.assert_called_once_with(["hsguru_meta_wild_top_legend"], tier=None)

    def test_refresh_require_all_ok_returns_failure_for_rejected_source(self) -> None:
        results = [
            {"source_id": "hsreplay_arena_cards_advanced", "state": "ok"},
            {"source_id": "heartharena_tierlist", "state": "parse_error"},
            {"source_id": "firestone_arena_cards_normal", "state": "ok"},
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--source",
                    "heartharena_tierlist",
                    "--source",
                    "firestone_arena_cards_normal",
                    "--require-all-ok",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_refresh_require_all_ok_rejects_cached_success(self) -> None:
        results = [
            {
                "source_id": "hsreplay_arena_cards_advanced",
                "state": "ok",
                "serving_cached_dataset": True,
            }
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--require-all-ok",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_refresh_require_all_ok_accepts_fresh_successes(self) -> None:
        results = [
            {"source_id": "hsreplay_arena_cards_advanced", "state": "ok"},
            {"source_id": "heartharena_tierlist", "state": "ok"},
            {"source_id": "firestone_arena_cards_normal", "state": "ok"},
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--source",
                    "heartharena_tierlist",
                    "--source",
                    "firestone_arena_cards_normal",
                    "--require-all-ok",
                ]
            )

        self.assertEqual(exit_code, 0)

    def test_load_env_file_overrides_stale_hsguru_backend_export(self) -> None:
        with TemporaryDirectory() as td:
            env_path = Path(td) / "hs.env"
            env_path.write_text(
                "HS_HSGURU_FETCH_BACKENDS=flaresolverr,scrapling,curl_cffi\n"
                "HS_BRIGHTDATA_UNLOCKER_ENABLED=false\n"
                "VICIOUS_SYNDICATE_STORAGE_PATH=/tmp/vicious-session.json\n",
                encoding="utf-8",
            )
            old = os.environ.get("HS_HSGURU_FETCH_BACKENDS")
            old_brightdata = os.environ.get("HS_BRIGHTDATA_UNLOCKER_ENABLED")
            old_vicious = os.environ.get("VICIOUS_SYNDICATE_STORAGE_PATH")
            os.environ["HS_HSGURU_FETCH_BACKENDS"] = "patchright"
            os.environ["HS_BRIGHTDATA_UNLOCKER_ENABLED"] = "true"
            os.environ["VICIOUS_SYNDICATE_STORAGE_PATH"] = "/tmp/stale.json"
            try:
                cli.load_env_file(env_path)
                self.assertEqual(
                    os.environ["HS_HSGURU_FETCH_BACKENDS"],
                    "flaresolverr,scrapling,curl_cffi",
                )
                self.assertEqual(
                    os.environ["HS_BRIGHTDATA_UNLOCKER_ENABLED"],
                    "false",
                )
                self.assertEqual(
                    os.environ["VICIOUS_SYNDICATE_STORAGE_PATH"],
                    "/tmp/vicious-session.json",
                )
            finally:
                if old is None:
                    os.environ.pop("HS_HSGURU_FETCH_BACKENDS", None)
                else:
                    os.environ["HS_HSGURU_FETCH_BACKENDS"] = old
                if old_brightdata is None:
                    os.environ.pop("HS_BRIGHTDATA_UNLOCKER_ENABLED", None)
                else:
                    os.environ["HS_BRIGHTDATA_UNLOCKER_ENABLED"] = old_brightdata
                if old_vicious is None:
                    os.environ.pop("VICIOUS_SYNDICATE_STORAGE_PATH", None)
                else:
                    os.environ["VICIOUS_SYNDICATE_STORAGE_PATH"] = old_vicious

    def test_quality_check_returns_nonzero_for_invalid_cached_dataset(self) -> None:
        source = Source("bad_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"bad_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch("app.storage.load_dataset", return_value={"data": {}}):
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 1)

    def test_quality_check_passes_valid_cached_dataset(self) -> None:
        source = Source("ok_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"ok_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch(
            "app.storage.load_dataset",
            return_value={"data": {"title": "ok", "structured": {"type": "legacy_dataset", "rows": []}}},
        ):
            with patch("app.scrapers.quality.validate_parsed_data", return_value=(True, "ok")):
                exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 0)

    def test_quality_check_warn_band_does_not_fail(self) -> None:
        source = Source("warn_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"warn_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch(
            "app.storage.load_dataset",
            return_value={"data": {"title": "ok", "structured": {"type": "legacy_dataset", "rows": []}}},
        ), patch("app.scrapers.quality.validate_parsed_data", return_value=(True, "ok")), patch(
            "app.scrapers.quality.quality_metrics",
            return_value={"quality_score": 0.90},
        ):
            exit_code = cli.main(["quality-check", "--min-quality-score", "0.85", "--warn-quality-score", "0.95"])

        self.assertEqual(exit_code, 0)

    def test_quality_check_reports_validation_exception_as_bad_source(self) -> None:
        source = Source("raises_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"raises_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch(
            "app.storage.load_dataset",
            return_value={"data": {"title": "ok", "structured": {"type": "arena_class_matrix"}}},
        ), patch(
            "app.scrapers.quality.validate_parsed_data",
            side_effect=ValueError("bad deck_class"),
        ):
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 1)

    def test_quality_check_uses_pipeline_status_and_structured_payload(self) -> None:
        source = Source(
            "pipeline_source",
            "https://example.test",
            "hsreplay",
            "meta",
            kind="pipeline",
        )
        with patch("app.cli.SOURCE_BY_ID", {"pipeline_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "pipeline"},
        ), patch(
            "app.storage.load_dataset",
            return_value={
                "data": {
                    "structured": {
                        "type": "hsreplay_archetype_database",
                        "archetypes": [],
                    }
                }
            },
        ), patch("app.scrapers.quality.validate_parsed_data") as generic_validate:
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 0)
        generic_validate.assert_not_called()

    def test_quality_check_validates_resolved_publication(self) -> None:
        source = Source(
            "hsguru_meta_standard_legend",
            "https://www.hsguru.com/meta",
            "hsguru",
            "meta",
        )
        stable = {
            "data": {
                "title": "HSGuru Meta",
                "structured": {
                    "type": "meta",
                    "strategies": [
                        {
                            "Archetype": f"Deck {index}",
                            "Winrate↓": "50%",
                            "Popularity": "1%",
                        }
                        for index in range(10)
                    ],
                },
            }
        }
        with patch("app.cli.SOURCE_BY_ID", {source.id: source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "stable-lkg"},
        ), patch(
            "app.parser_control.load_resolved_public_dataset", return_value=stable
        ) as resolved, patch(
            "app.scrapers.quality.validate_parsed_data", return_value=(True, "ok")
        ):
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 0)
        resolved.assert_called_once_with(source.id)

    def test_rebuild_index_uses_derived_resource_lock(self) -> None:
        with patch("app.resource_locks.ResourceLockSet") as resource_locks, patch(
            "app.firecrawl_map.build_hsreplay_index",
            return_value={"ok": True},
        ) as build:
            exit_code = cli.main(["rebuild-hsreplay-index"])

        self.assertEqual(exit_code, 0)
        resource_locks.assert_called_once_with(["derived:hsreplay-index"])
        resource_locks.return_value.__enter__.assert_called_once_with()
        build.assert_called_once_with()

    def test_scrape_do_map_command_refreshes_map_and_index(self) -> None:
        with (
            patch("app.resource_locks.ResourceLockSet") as resource_locks,
            patch(
                "app.firecrawl_map.refresh_hsreplay_map_and_index",
                return_value={"ok": True, "provider": "scrape_do"},
            ) as refresh,
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["scrape-do-map-hsreplay"])

        self.assertEqual(exit_code, 0)
        resource_locks.assert_called_once_with(
            ["derived:hsreplay-index", "derived:hsreplay-map"]
        )
        refresh.assert_called_once_with()

    def test_rebuild_index_reports_expected_lock_overlap_as_skipped(self) -> None:
        output = io.StringIO()
        owner = {
            "pid": 321,
            "resource_id": "derived:hsreplay-index",
            "run_id": "owner-run",
        }
        with patch("app.resource_locks.ResourceLockSet") as resource_locks, patch(
            "app.firecrawl_map.build_hsreplay_index",
        ) as build:
            resource_locks.return_value.__enter__.side_effect = ResourceLocked(
                "derived:hsreplay-index",
                owner,
            )
            with redirect_stdout(output):
                exit_code = cli.main(["rebuild-hsreplay-index"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "ok": True,
                "state": "locked",
                "skipped": True,
                "reason": "resource_locked",
                "locked_resource": "derived:hsreplay-index",
                "owner": owner,
            },
        )
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
