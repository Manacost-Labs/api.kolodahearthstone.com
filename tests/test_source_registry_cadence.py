"""Phase 5: single source registry with per-source freshness cadence.

Covers:
- Source dataclass defaults (kind="scrape", stale_hours=None) and registered
  pipeline entries with their timer-derived cadences;
- find_stale_sources honoring per-source stale_hours;
- freshness.ok in build_summary no longer special-casing orphan statuses;
- tier registry validation passing with pipeline sources present;
- refresh planning (partition_sources) excluding pipeline sources.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.source_tiers import (
    BROWSER_PATCHRIGHT_IDS,
    BROWSER_PROTECTED_IDS,
    LIGHT_API_IDS,
    MEDIUM_API_IDS,
    partition_sources,
    validate_tier_registry,
)
from app.sources import SOURCE_BY_ID, SOURCES
from app.stale_monitor import find_stale_sources

PIPELINE_IDS = (
    "hsguru_archetype_analysis",
    "hsguru_fun_decks",
    "hsguru_meta_matrix",
    "hsreplay_battlegrounds_compositions_screenshot",
    "hsreplay_battlegrounds_hero_details",
    "hsreplay_archetypes",
)


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


class SourceRegistryDefaultsTest(unittest.TestCase):
    def test_existing_source_defaults(self) -> None:
        source = SOURCE_BY_ID["heartharena_tierlist"]
        self.assertEqual(source.kind, "scrape")
        self.assertIsNone(source.stale_hours)

    def test_pipeline_entries_registered_with_cadence(self) -> None:
        hero_details = SOURCE_BY_ID["hsreplay_battlegrounds_hero_details"]
        self.assertEqual(hero_details.kind, "pipeline")
        # Mon/Thu timer: 96h maximum gap + 24h slack.
        self.assertEqual(hero_details.stale_hours, 120)
        self.assertEqual(hero_details.site, "hsreplay")
        self.assertEqual(hero_details.category, "battlegrounds")
        self.assertEqual(hero_details.url, "https://hsreplay.net/battlegrounds/heroes/")

        archetypes = SOURCE_BY_ID["hsreplay_archetypes"]
        self.assertEqual(archetypes.kind, "pipeline")
        # Mon,Thu timer: 96h max gap + 24h slack.
        self.assertEqual(archetypes.stale_hours, 120)
        self.assertEqual(archetypes.site, "hsreplay")
        self.assertEqual(archetypes.category, "meta")
        self.assertEqual(archetypes.url, "https://hsreplay.net/meta/")

    def test_registry_has_all_pipeline_sources(self) -> None:
        pipeline = [s for s in SOURCES if s.kind == "pipeline"]
        self.assertEqual(sorted(s.id for s in pipeline), sorted(PIPELINE_IDS))
        self.assertEqual(len(SOURCES), len({s.id for s in SOURCES}))

    def test_pipeline_ids_match_module_constants(self) -> None:
        from app.hsguru_archetype_analysis import SOURCE_ID as hsguru_analysis_source
        from app.hsguru_meta_matrix import SOURCE_ID as hsguru_matrix_source
        from app.hsreplay_archetypes_db import SOURCE as archetypes_source
        from app.hsreplay_bg_hero_details import SOURCE_ID as hero_details_source
        from app.hsreplay_bg_screenshots import (
            SCREENSHOT_SOURCE_ID as compositions_screenshot_source,
        )

        self.assertIn(hsguru_analysis_source, SOURCE_BY_ID)
        self.assertIn(hsguru_matrix_source, SOURCE_BY_ID)
        self.assertIn(archetypes_source, SOURCE_BY_ID)
        self.assertIn(hero_details_source, SOURCE_BY_ID)
        self.assertIn(compositions_screenshot_source, SOURCE_BY_ID)


class PerSourceStaleHoursTest(unittest.TestCase):
    """find_stale_sources uses source.stale_hours over the global threshold."""

    def _run(self, age_hours: float) -> list[dict]:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_hero_details"]  # stale_hours=120
        fetched_at = _iso_hours_ago(age_hours)
        status = {
            "source_id": source.id,
            "state": "ok",
            "fetched_at": fetched_at,
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HS_API_DATA_DIR": tmp}
        ), patch("app.stale_monitor.load_status", return_value=status), patch(
            "app.stale_monitor.load_dataset", return_value={"fetched_at": fetched_at}
        ), patch(
            "app.stale_monitor.stale_dataset_hours", return_value=12.0
        ), patch(
            "app.stale_monitor.SOURCES", [source]
        ):
            return find_stale_sources(include_ok=True)

    def test_age_100h_below_pipeline_cadence_not_stale(self) -> None:
        # Global threshold is 12h; the per-source 120h wins.
        self.assertEqual(self._run(100), [])

    def test_age_121h_above_pipeline_cadence_stale(self) -> None:
        found = self._run(121)
        self.assertEqual(
            [item["source_id"] for item in found],
            ["hsreplay_battlegrounds_hero_details"],
        )
        self.assertEqual(found[0]["reason"], "ok_but_stale")

    def test_global_threshold_still_applies_without_stale_hours(self) -> None:
        source = SOURCE_BY_ID["heartharena_tierlist"]  # stale_hours=None
        fetched_at = _iso_hours_ago(20)
        status = {"source_id": source.id, "state": "ok", "fetched_at": fetched_at}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HS_API_DATA_DIR": tmp}
        ), patch("app.stale_monitor.load_status", return_value=status), patch(
            "app.stale_monitor.load_dataset", return_value={"fetched_at": fetched_at}
        ), patch(
            "app.stale_monitor.stale_dataset_hours", return_value=12.0
        ), patch(
            "app.stale_monitor.SOURCES", [source]
        ):
            found = find_stale_sources(include_ok=True)
        self.assertEqual([item["source_id"] for item in found], [source.id])


class FreshnessOkComputationTest(unittest.TestCase):
    """freshness.ok = not stale and not cached; no orphan_status carve-out.

    The registered pipeline source runs through the same real
    find_stale_sources path as everything else (storage loaders patched).
    """

    def _build_summary(self, hero_details_age_hours: float) -> dict:
        from app import refresh_log

        def fake_status(source_id: str) -> dict:
            age = hero_details_age_hours if source_id == "hsreplay_battlegrounds_hero_details" else 1.0
            return {
                "source_id": source_id,
                "state": "ok",
                "fetched_at": _iso_hours_ago(age),
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HS_API_DATA_DIR": tmp}
        ), patch.object(refresh_log, "read_events", return_value=[]), patch.object(
            refresh_log, "hsreplay_auth_status", return_value={"ok": True}
        ), patch.object(
            refresh_log, "load_status", side_effect=fake_status
        ), patch(
            "app.stale_monitor.load_status", side_effect=fake_status
        ), patch(
            "app.stale_monitor.load_dataset", return_value=None
        ), patch(
            "app.stale_monitor.stale_dataset_hours", return_value=12.0
        ):
            return refresh_log.build_summary(since_hours=24.0)

    def test_stale_pipeline_source_fails_freshness(self) -> None:
        summary = self._build_summary(hero_details_age_hours=200)
        stale_ids = [item["source_id"] for item in summary["stale_datasets"]]
        self.assertIn("hsreplay_battlegrounds_hero_details", stale_ids)
        self.assertFalse(summary["freshness"]["ok"])

    def test_fresh_pipeline_source_passes_freshness(self) -> None:
        summary = self._build_summary(hero_details_age_hours=100)
        self.assertEqual(summary["stale_datasets"], [])
        self.assertTrue(summary["freshness"]["ok"])

    def test_orphan_status_now_fails_freshness(self) -> None:
        """An orphan stale entry counts against ok (hack removed)."""
        from app import refresh_log

        orphan = [
            {
                "source_id": "some_forgotten_pipeline",
                "state": "ok",
                "dataset_age_hours": 99.0,
                "reason": "orphan_status",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"HS_API_DATA_DIR": tmp}
        ), patch.object(refresh_log, "read_events", return_value=[]), patch.object(
            refresh_log, "hsreplay_auth_status", return_value={"ok": True}
        ), patch.object(
            refresh_log, "load_status", return_value=None
        ), patch.object(
            refresh_log, "_stale_sources", return_value=orphan
        ):
            summary = refresh_log.build_summary(since_hours=24.0)
        self.assertFalse(summary["freshness"]["ok"])


class PipelinePlanningExclusionTest(unittest.TestCase):
    def test_tier_registry_validates_with_pipeline_sources(self) -> None:
        validate_tier_registry()  # must not raise

    def test_partition_excludes_pipeline_ids(self) -> None:
        parts = partition_sources(list(SOURCES))
        partitioned_ids = {s.id for bucket in parts for s in bucket}
        for pipeline_id in PIPELINE_IDS:
            self.assertNotIn(pipeline_id, partitioned_ids)
        self.assertEqual(
            partitioned_ids,
            LIGHT_API_IDS | MEDIUM_API_IDS | BROWSER_PATCHRIGHT_IDS | BROWSER_PROTECTED_IDS,
        )


if __name__ == "__main__":
    unittest.main()
