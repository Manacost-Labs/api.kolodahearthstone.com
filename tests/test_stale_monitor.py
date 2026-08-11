from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.parser_control import ParserControlStore
from app.post_patch_policy import POST_PATCH_BASELINE_LABEL
from app.sources import SOURCE_BY_ID
from app.stale_monitor import _stale_alert_state, find_stale_sources
from app.storage import dataset_path, save_baseline_once, save_status, write_json


class StaleMonitorTest(unittest.TestCase):
    def test_stable_mode_measures_public_lkg_not_fresh_provisional_candidate(self) -> None:
        source = SOURCE_BY_ID["hsreplay_arena_cards_advanced"]
        fresh = datetime.now(UTC).isoformat()
        old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        stable = {"fetched_at": old, "data": {"structured": {"cards": []}}}
        provisional = {
            "fetched_at": fresh,
            "data": {"structured": {"cards": [], "provisional": True}},
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ), patch("app.stale_monitor.SOURCES", [source]), patch(
            "app.stale_monitor.stale_dataset_hours", return_value=12.0
        ):
            save_baseline_once(source.id, POST_PATCH_BASELINE_LABEL, stable)
            write_json(dataset_path(source.id), provisional)
            save_status(source.id, {"state": "ok", "fetched_at": fresh})
            ParserControlStore(Path(directory)).update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="window expired",
                updated_by="test",
            )
            found = find_stale_sources(include_ok=True)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["reason"], "ok_but_stale")
        self.assertGreaterEqual(found[0]["dataset_age_hours"], 99.0)

    def test_active_early_mode_measures_fresh_provisional_candidate(self) -> None:
        source = SOURCE_BY_ID["hsreplay_arena_cards_advanced"]
        fresh = datetime.now(UTC).isoformat()
        old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        stable = {"fetched_at": old, "data": {"structured": {"cards": []}}}
        provisional = {
            "fetched_at": fresh,
            "data": {"structured": {"cards": [], "provisional": True}},
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ), patch("app.stale_monitor.SOURCES", [source]), patch(
            "app.stale_monitor.stale_dataset_hours", return_value=12.0
        ):
            save_baseline_once(source.id, POST_PATCH_BASELINE_LABEL, stable)
            write_json(dataset_path(source.id), provisional)
            save_status(source.id, {"state": "ok", "fetched_at": fresh})
            ParserControlStore(Path(directory)).update_policy(
                expected_revision=1,
                mode="early",
                early_until=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                reason="new patch",
                updated_by="test",
            )
            found = find_stale_sources(include_ok=True)

        self.assertEqual(found, [])
    def test_finds_ok_but_old_status(self) -> None:
        old = (datetime.now(UTC) - timedelta(hours=20)).isoformat()
        status = {
            "source_id": "heartharena_tierlist",
            "state": "ok",
            "fetched_at": old,
        }
        with patch("app.stale_monitor.load_status", return_value=status), patch(
            "app.stale_monitor.load_dataset", return_value={"fetched_at": old}
        ), patch("app.stale_monitor.stale_dataset_hours", return_value=12.0), patch(
            "app.stale_monitor.SOURCES",
            [type("S", (), {"id": "heartharena_tierlist"})()],
        ):
            found = find_stale_sources(include_ok=True)
        ids = [f["source_id"] for f in found]
        self.assertIn("heartharena_tierlist", ids)
        self.assertEqual(found[0].get("reason"), "ok_but_stale")

    def test_skips_fresh_ok(self) -> None:
        fresh = datetime.now(UTC).isoformat()
        status = {"state": "ok", "fetched_at": fresh}
        with patch("app.stale_monitor.load_status", return_value=status), patch(
            "app.stale_monitor.load_dataset", return_value={"fetched_at": fresh}
        ), patch("app.stale_monitor.stale_dataset_hours", return_value=12.0), patch(
            "app.stale_monitor.SOURCES",
            [type("S", (), {"id": "metastats_decks"})()],
        ):
            found = find_stale_sources(include_ok=True)
        self.assertEqual(found, [])

    def test_stale_alert_state_escalates_by_age(self) -> None:
        self.assertEqual(
            _stale_alert_state({"state": "ok", "dataset_age_hours": 20}),
            "stale_ok",
        )
        self.assertEqual(
            _stale_alert_state({"state": "ok", "dataset_age_hours": 25}),
            "stale_ok_24h",
        )
        self.assertEqual(
            _stale_alert_state({"state": "fetch_error", "dataset_age_hours": 50}),
            "stale_data_48h",
        )

    def test_cached_live_failure_alerts_as_stale_data(self) -> None:
        self.assertEqual(
            _stale_alert_state(
                {
                    "state": "ok",
                    "reason": "live_failed_cached",
                    "dataset_age_hours": 25,
                }
            ),
            "stale_data_24h",
        )


if __name__ == "__main__":
    unittest.main()
