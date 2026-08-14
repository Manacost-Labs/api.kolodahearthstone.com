from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.hsreplay_bg_stats import (
    BG_TIME_RANGE,
    _minion_stats,
    _query_url,
    fetch_battlegrounds_minions,
)


class HsReplayBattlegroundStatsTest(unittest.TestCase):
    def test_minion_identity_rejects_schema_drift(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            _minion_stats({"minion_dbf_id": True})
        with self.assertRaisesRegex(ValueError, "positive integer"):
            _minion_stats({"minion_dbf_id": 1.5})

    def test_minion_payload_rejects_duplicate_identities(self) -> None:
        payload = {
            "series": {
                "data": [
                    {"minion_dbf_id": 1},
                    {"minion_dbf_id": 1},
                ]
            }
        }
        with (
            patch(
                "app.hsreplay_bg_stats.scrape_source",
                new=AsyncMock(side_effect=RuntimeError("page optional")),
            ),
            patch(
                "app.hsreplay_bg_stats.fetch_hsreplay_json",
                new=AsyncMock(return_value=payload),
            ),
            patch(
                "app.hsreplay_bg_stats._minion_stats",
                side_effect=lambda row: {
                    "minion_dbf_id": row["minion_dbf_id"],
                    "popularity": "1.00%",
                },
            ),
            self.assertRaisesRegex(ValueError, "duplicate minion_dbf_id"),
        ):
            asyncio.run(
                fetch_battlegrounds_minions(
                    "hsreplay_battlegrounds_minions"
                )
            )

    def test_minions_publish_v1_row_retrieval_evidence(self) -> None:
        payload = {"series": {"data": []}}
        with (
            patch(
                "app.hsreplay_bg_stats.scrape_source",
                new=AsyncMock(side_effect=RuntimeError("page optional")),
            ),
            patch(
                "app.hsreplay_bg_stats.fetch_hsreplay_json",
                new=AsyncMock(return_value=payload),
            ),
        ):
            result = asyncio.run(
                fetch_battlegrounds_minions(
                    "hsreplay_battlegrounds_minions"
                )
            )

        self.assertEqual(result["completeness_schema_version"], 1)
        self.assertEqual(result["row_retrieval"]["raw_rows"], 0)
        self.assertEqual(result["row_retrieval"]["normalized_rows"], 0)
        self.assertEqual(result["row_retrieval"]["unexplained_drops"], 0)

    def test_bg_minion_stats_use_current_patch_window(self) -> None:
        url = _query_url("battlegrounds_minion_list")

        self.assertEqual(BG_TIME_RANGE, "CURRENT_BATTLEGROUNDS_PATCH")
        self.assertIn("BattlegroundsTimeRange=CURRENT_BATTLEGROUNDS_PATCH", url)
        self.assertNotIn("LAST_7_DAYS", url)

    def test_minions_publish_upstream_freshness_without_claiming_population(self) -> None:
        freshness = {
            "status": "fresh",
            "reason": None,
            "observed_at": "2026-08-14T02:20:00+00:00",
            "age_seconds": 60,
            "evidence": ["body_as_of"],
            "response_headers": {},
            "body_as_of": "2026-08-14T02:19:00Z",
        }
        with (
            patch(
                "app.hsreplay_bg_stats.scrape_source",
                new=AsyncMock(side_effect=RuntimeError("page optional")),
            ),
            patch(
                "app.hsreplay_bg_stats.fetch_hsreplay_json",
                new=AsyncMock(
                    return_value={
                        "as_of": "2026-08-14T02:19:00Z",
                        "series": {"data": []},
                    }
                ),
            ),
            patch(
                "app.hsreplay_bg_stats.get_hsreplay_json_target_headers",
                return_value={"last-modified": "Fri, 14 Aug 2026 02:19:00 GMT"},
            ) as headers,
            patch(
                "app.hsreplay_bg_stats.build_hsreplay_bg_upstream_freshness",
                return_value=freshness,
            ) as build,
        ):
            result = asyncio.run(
                fetch_battlegrounds_minions("hsreplay_battlegrounds_minions")
            )

        self.assertEqual(result["upstream_freshness"], freshness)
        self.assertEqual(result["population_completeness"], "unverifiable")
        cache_key = "bg:minions:TOP_50_PERCENT:CURRENT_BATTLEGROUNDS_PATCH"
        headers.assert_called_once_with(cache_key)
        build.assert_called_once()
