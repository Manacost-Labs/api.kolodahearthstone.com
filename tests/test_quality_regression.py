from __future__ import annotations

import unittest
from unittest.mock import patch

from app.dataset_regression import check_dataset_regression, estimate_metric_count
from app.scrapers.quality import quality_metrics
from app.sources import SOURCE_BY_ID


class DatasetRegressionTest(unittest.TestCase):
    def test_estimate_card_stats(self) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_legend_included_popularity"]
        count = estimate_metric_count(
            source,
            {"structured": {"type": "card_stats", "cards": [{"id": 1}] * 50}},
        )
        self.assertEqual(count, 50)

    def test_quality_metrics_counts_filled_card_stats(self) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_legend_included_popularity"]
        metrics = quality_metrics(
            source,
            {
                "structured": {
                    "type": "card_stats",
                    "cards": [
                        {"id": 1, "deck_winrate": "55%"},
                        {"id": 2},
                    ],
                }
            },
        )

        self.assertEqual(metrics["cards"], 2)
        self.assertEqual(metrics["cards_with_metrics"], 1)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_regression_detected(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsreplay_arena_cards_advanced"]
        prev = {"structured": {"type": "arena_card_tiers", "cards": [{"x": 1}] * 100}}
        new = {"structured": {"type": "arena_card_tiers", "cards": [{"x": 1}] * 50}}
        reg, msg, extra = check_dataset_regression(
            source, previous_data=prev, new_data=new
        )
        self.assertTrue(reg)
        self.assertIsNotNone(msg)
        self.assertEqual(extra["rows_before"], 100)
        self.assertEqual(extra["rows_after"], 50)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_no_regression_small_drop(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsreplay_arena_cards_advanced"]
        prev = {"structured": {"type": "arena_card_tiers", "cards": [{"x": 1}] * 100}}
        new = {"structured": {"type": "arena_card_tiers", "cards": [{"x": 1}] * 80}}
        reg, _, _ = check_dataset_regression(source, previous_data=prev, new_data=new)
        self.assertFalse(reg)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_regression_detected_when_card_metrics_disappear(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_legend_included_popularity"]
        prev = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": i, "deck_popularity": "1%"} for i in range(50)],
            }
        }
        new = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": i} for i in range(50)],
            }
        }
        reg, msg, extra = check_dataset_regression(
            source, previous_data=prev, new_data=new
        )

        self.assertTrue(reg)
        self.assertIn("filled metric count dropped", msg or "")
        self.assertEqual(extra["filled_before"], 50)
        self.assertEqual(extra["filled_after"], 0)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_volatile_hsreplay_1d_cards_allow_large_daily_swing(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_wild_legend_1d"]
        prev = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": i, "deck_popularity": "1%"} for i in range(3489)],
            }
        }
        new = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": i, "deck_popularity": "1%"} for i in range(2069)],
            }
        }

        reg, _, extra = check_dataset_regression(source, previous_data=prev, new_data=new)

        self.assertFalse(reg)
        self.assertEqual(extra["rows_before"], 3489)
        self.assertEqual(extra["rows_after"], 2069)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_current_patch_reset_uses_absolute_quality_floor(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_wild_legend_patch"]
        prev = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": idx, "deck_popularity": "1%"} for idx in range(4965)],
            }
        }
        new = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": idx, "deck_popularity": "1%"} for idx in range(1036)],
            }
        }

        reg, _, extra = check_dataset_regression(source, previous_data=prev, new_data=new)

        self.assertFalse(reg)
        self.assertEqual(extra["drop_ratio"], 0.85)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.10)
    def test_hsguru_meta_allows_rank_slice_volatility(self, _ratio: object) -> None:
        # Rank-slice volatility is now expressed via the per-source contract:
        # hsguru_meta_* contracts set regression_drop_ratio=0.30
        # (app/source_contracts.py:382-404), and
        # regression_drop_ratio_for_source takes max(default, contract)
        # (app/source_contracts.py:425-429). The old blanket 0.50 allowance for
        # top-legend slices is gone. Base default is patched to 0.10 so the test
        # proves the contract RAISES the allowance: a 25% drop (44 -> 33) would
        # regress at 0.10 but passes at the contract's 0.30.
        source = SOURCE_BY_ID["hsguru_meta_wild_top_legend"]
        prev = {
            "structured": {
                "type": "meta",
                "strategies": [
                    {"Archetype": f"Deck {idx}", "Popularity": "1%"} for idx in range(44)
                ],
            }
        }
        new = {
            "structured": {
                "type": "meta",
                "strategies": [
                    {"Archetype": f"Deck {idx}", "Popularity": "1%"} for idx in range(33)
                ],
            }
        }

        reg, _, extra = check_dataset_regression(source, previous_data=prev, new_data=new)

        self.assertFalse(reg)
        self.assertEqual(extra["drop_ratio"], 0.30)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_bg_trinkets_regression_counts_active_rows_only(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_trinkets_lesser"]
        prev = {
            "structured": {
                "type": "bg_trinkets",
                "trinkets": [
                    {"trinket_id": f"active_{idx}", "name": f"A{idx}", "pick_rate": "1%"}
                    for idx in range(101)
                ]
                + [
                    {"trinket_id": f"inactive_{idx}", "name": f"I{idx}"}
                    for idx in range(63)
                ],
            }
        }
        new = {
            "structured": {
                "type": "bg_trinkets",
                "trinkets": [
                    {"trinket_id": f"active_{idx}", "name": f"A{idx}", "pick_rate": "1%"}
                    for idx in range(80)
                ],
            }
        }

        reg, _, extra = check_dataset_regression(source, previous_data=prev, new_data=new)

        self.assertFalse(reg)
        self.assertEqual(extra["rows_before"], 101)
        self.assertEqual(extra["rows_after"], 80)

    @patch("app.dataset_regression.dataset_regression_drop_ratio", return_value=0.30)
    def test_vicious_radar_regression_uses_radar_count(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["vicious_syndicate_radars"]
        prev = {
            "structured": {
                "type": "vicious_syndicate_radars",
                "radars": [{"nodes": [1]} for _ in range(24)],
            }
        }
        new = {
            "structured": {
                "type": "vicious_syndicate_radars",
                "radars": [{"nodes": [1]} for _ in range(4)],
            }
        }
        reg, msg, extra = check_dataset_regression(
            source, previous_data=prev, new_data=new
        )

        self.assertTrue(reg)
        self.assertIn("metric count dropped", msg or "")
        self.assertEqual(extra["rows_before"], 24)
        self.assertEqual(extra["rows_after"], 4)


if __name__ == "__main__":
    unittest.main()
