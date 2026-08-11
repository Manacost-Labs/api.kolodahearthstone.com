from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from app import main


class HealthEndpointTest(unittest.TestCase):
    def test_public_health_is_minimal_liveness(self) -> None:
        response = TestClient(main.app).get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertNotIn("data_dir", payload)
        self.assertNotIn("stale_sources", payload)

    def test_ops_health_reports_stale_cached_source(self) -> None:
        source = type("SourceStub", (), {"id": "src1"})()
        status = {
            "source_id": "src1",
            "state": "ok",
            "serving_cached_dataset": True,
            "last_refresh_state": "fetch_error",
            "fetched_at": "2026-06-04T00:00:00+00:00",
        }
        stale = [{"source_id": "src1", "reason": "ok_but_stale"}]

        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "SOURCES", [source]), patch.object(
            main, "load_status", return_value=status
        ), patch.object(main, "load_dataset", return_value=None), patch.object(
            main, "root_dir", return_value=Path(tmp)
        ), patch.object(
            main, "api_key", return_value="secret"
        ), patch(
            "app.stale_monitor.find_stale_sources", return_value=stale
        ):
            response = TestClient(main.app).get("/ops/health", headers={"X-API-Key": "secret"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["serving_ok"])
        self.assertFalse(payload["freshness_ok"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["cached_sources"], ["src1"])
        self.assertEqual(payload["cached_after_failure_sources"], ["src1"])
        self.assertEqual(payload["cached_after_failure_count"], 1)
        self.assertEqual(payload["stale_sources"], ["src1"])

    def test_ops_health_detects_semantically_invalid_cached_dataset(self) -> None:
        source = type("SourceStub", (), {"id": "vicious_syndicate_live_beta"})()
        status = {"source_id": source.id, "state": "ok", "fetched_at": "2026-07-12T00:00:00Z"}
        placeholders = [{"deck": f"Other Class{idx}"} for idx in range(11)]
        dataset = {
            "data": {
                "structured": {
                    "type": "vicious_live",
                    "deck_distribution": placeholders,
                    "tier_list": [{"rank_bracket": "All", "decks": placeholders}],
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmp, patch.object(main, "SOURCES", [source]), patch.object(
            main, "load_status", return_value=status
        ), patch.object(main, "load_dataset", return_value=dataset), patch.object(
            main, "root_dir", return_value=Path(tmp)
        ), patch.object(main, "api_key", return_value="secret"), patch(
            "app.stale_monitor.find_stale_sources", return_value=[]
        ):
            response = TestClient(main.app).get("/ops/health", headers={"X-API-Key": "secret"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["serving_ok"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["semantic_failed_sources"], [source.id])
        self.assertIn(
            "vicious_live.too_few_named_archetypes",
            {
                issue["code"]
                for issue in payload["semantic_failures"][0]["issues"]
            },
        )

    def test_ops_health_validates_resolved_stable_dataset_after_early_window(self) -> None:
        source = type("SourceStub", (), {"id": "hsguru_meta_standard_legend"})()
        status = {"source_id": source.id, "state": "ok", "fetched_at": "2026-08-02T00:00:00Z"}
        stable = {
            "data": {
                "structured": {
                    "type": "meta",
                    "strategies": [
                        {
                            "Archetype": f"Stable {index}",
                            "Winrate↓": "52%",
                            "Popularity": "2%",
                        }
                        for index in range(10)
                    ],
                }
            }
        }
        provisional = {
            "data": {
                "structured": {
                    "type": "meta",
                    "strategies": [
                        {
                            "Archetype": f"Early {index}",
                            "Winrate↓": "51%",
                            "Popularity": "1%",
                        }
                        for index in range(3)
                    ],
                    "provisional": True,
                }
            }
        }

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(main, "SOURCES", [source]),
            patch.object(main, "load_status", return_value=status),
            patch.object(main, "load_dataset", return_value=provisional),
            patch.object(main, "root_dir", return_value=Path(tmp)),
            patch(
                "app.parser_control.resolve_public_dataset",
                return_value=stable,
            ) as resolver,
            patch("app.stale_monitor.find_stale_sources", return_value=[]),
        ):
            payload = main.build_health_diagnostics()

        resolver.assert_called_once_with(source.id, provisional)
        self.assertTrue(payload["serving_ok"], payload)
        self.assertEqual(payload["semantic_failed_sources"], [])

    def test_cached_dataset_quality_includes_contract_failures(self) -> None:
        dataset = {
            "data": {
                "structured": {
                    "type": "metastats_decks",
                    "decks": [
                        {
                            "archetype_name": "Only deck",
                            "win_rate": "50%",
                            "games": 100,
                        }
                    ],
                }
            }
        }

        quality = main._semantic_dataset_quality("metastats_decks", dataset)

        self.assertIsNotNone(quality)
        self.assertFalse(quality["ok"])
        self.assertFalse(quality["contract"]["ok"])
        self.assertIn(
            "source_contract.failed",
            {issue["code"] for issue in quality["issues"]},
        )

    def test_health_polling_reuses_short_lived_diagnostics(self) -> None:
        original_payload = main._health_cache_payload
        original_at = main._health_cache_at
        main._health_cache_payload = None
        main._health_cache_at = 0.0
        try:
            with patch.object(main, "python_environment", return_value="production"), patch.object(
                main, "time"
            ) as clock, patch.object(
                main, "build_health_diagnostics", return_value={"ok": True}
            ) as build:
                clock.monotonic.side_effect = [100.0, 105.0]

                first = main.cached_health_diagnostics()
                second = main.cached_health_diagnostics()

            self.assertIs(first, second)
            build.assert_called_once_with()
        finally:
            main._health_cache_payload = original_payload
            main._health_cache_at = original_at


if __name__ == "__main__":
    unittest.main()
