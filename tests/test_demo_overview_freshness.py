from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.demo import build_overview
from app.sources import SOURCE_BY_ID
from app.storage import status_path, write_json


class DemoOverviewFreshnessTest(unittest.TestCase):
    def _status(self, source_id: str, fetched_at: datetime) -> None:
        write_json(
            status_path(source_id),
            {
                "source_id": source_id,
                "state": "ok",
                "fetched_at": fetched_at.isoformat(),
            },
        )

    def test_overview_uses_status_timestamp_for_asset_only_source(self) -> None:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_compositions_screenshot"]
        captured_at = datetime.now(UTC) - timedelta(minutes=5)

        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {"HS_API_DATA_DIR": directory},
                clear=False,
            ),
            patch("app.demo.SOURCES", (source,)),
            patch("app.stale_monitor.SOURCES", (source,)),
        ):
            self._status(source.id, captured_at)
            overview = build_overview()

        row = overview["sources"][0]
        self.assertEqual(row["fetched_at"], captured_at.isoformat())
        self.assertTrue(row["operationally_enabled"])
        self.assertFalse(row["stale"])

    def test_overview_uses_source_threshold_and_excludes_disabled_source(self) -> None:
        slow_source = SOURCE_BY_ID["hsreplay_archetypes"]
        stale_source = SOURCE_BY_ID["vicious_syndicate_radars"]
        disabled_source = SOURCE_BY_ID["firestone_standard"]
        now = datetime.now(UTC)

        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_FIRESTONE_STANDARD_AUTHORIZED": "false",
                },
                clear=False,
            ),
            patch("app.demo.SOURCES", (slow_source, stale_source, disabled_source)),
            patch(
                "app.stale_monitor.SOURCES",
                (slow_source, stale_source, disabled_source),
            ),
        ):
            self._status(slow_source.id, now - timedelta(hours=96))
            self._status(stale_source.id, now - timedelta(hours=72))
            self._status(disabled_source.id, now - timedelta(hours=96))
            overview = build_overview()

        rows = {row["source_id"]: row for row in overview["sources"]}
        self.assertFalse(rows[slow_source.id]["stale"])
        self.assertEqual(rows[slow_source.id]["stale_hours_threshold"], 120)
        self.assertTrue(rows[stale_source.id]["stale"])
        self.assertEqual(rows[stale_source.id]["stale_reason"], "ok_but_stale")
        self.assertFalse(rows[disabled_source.id]["operationally_enabled"])
        self.assertEqual(rows[disabled_source.id]["state"], "disabled")
        self.assertFalse(rows[disabled_source.id]["stale"])


if __name__ == "__main__":
    unittest.main()
