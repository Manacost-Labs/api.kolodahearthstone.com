from __future__ import annotations

import unittest

from app.hsreplay_bg_stats import BG_TIME_RANGE, _query_url


class HsReplayBattlegroundStatsTest(unittest.TestCase):
    def test_bg_minion_stats_use_current_patch_window(self) -> None:
        url = _query_url("battlegrounds_minion_list")

        self.assertEqual(BG_TIME_RANGE, "CURRENT_BATTLEGROUNDS_PATCH")
        self.assertIn("BattlegroundsTimeRange=CURRENT_BATTLEGROUNDS_PATCH", url)
        self.assertNotIn("LAST_7_DAYS", url)
