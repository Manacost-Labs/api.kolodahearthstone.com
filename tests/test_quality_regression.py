from __future__ import annotations

import unittest
from unittest.mock import patch

from app.dataset_regression import check_dataset_regression, estimate_metric_count
from app.scrapers.quality import quality_metrics
from app.source_contracts import contract_quality_report
from app.sources import SOURCE_BY_ID

VALID_STREAMER_DECK_CODE = (
    "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6h"
    "Bom1BoSZB+C+B43cBwAA"
)


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
    def test_streamer_rolling_hour_accepts_complete_three_row_window(
        self,
        _ratio: object,
    ) -> None:
        source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
        previous = {
            "structured": {
                "type": "streamer_decks",
                "rows": [
                    {
                        "Deck": f"Previous deck {index}",
                        "Streamer": f"Streamer {index}",
                        "deck_code": f"previous-{index}",
                    }
                    for index in range(10)
                ],
            }
        }
        current = {
            "structured": {
                "type": "streamer_decks",
                "rows": [
                    {
                        "Deck": f"Current deck {index}",
                        "Streamer": f"Streamer {index}",
                        "deck_code": VALID_STREAMER_DECK_CODE,
                    }
                    for index in range(3)
                ],
            }
        }

        contract = contract_quality_report(source.id, current["structured"])
        regression, _message, extra = check_dataset_regression(
            source,
            previous_data=previous,
            new_data=current,
        )

        self.assertTrue(contract["ok"], contract["warnings"])
        self.assertFalse(regression)
        self.assertEqual(extra["rows_before"], 10)
        self.assertEqual(extra["rows_after"], 3)

    def test_streamer_rolling_hour_rejects_incomplete_rows_at_absolute_floor(
        self,
    ) -> None:
        structured = {
            "type": "streamer_decks",
            "rows": [
                {
                    "Deck": "Complete deck",
                    "Streamer": "Complete streamer",
                    "deck_code": "complete-code",
                },
                {
                    "Deck": "Missing code",
                    "Streamer": "Second streamer",
                },
                {
                    "Deck": "Missing streamer",
                    "deck_code": "third-code",
                },
            ],
        }

        report = contract_quality_report(
            "hsguru_streamer_decks_legend_1000",
            structured,
        )

        self.assertFalse(report["ok"])
        self.assertIn("deck_code fill rate", " ".join(report["warnings"]))
        self.assertIn("Streamer fill rate", " ".join(report["warnings"]))

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
    def test_hsguru_legend_accepts_verified_early_patch_pool(self, _ratio: object) -> None:
        source = SOURCE_BY_ID["hsguru_meta_wild_legend"]
        previous = {
            "structured": {
                "type": "meta",
                "strategies": [{"Archetype": f"Old {index}"} for index in range(105)],
            }
        }
        current = {
            "structured": {
                "type": "meta",
                "strategies": [{"Archetype": f"New {index}"} for index in range(29)],
            }
        }

        regression, _message, extra = check_dataset_regression(
            source, previous_data=previous, new_data=current
        )

        self.assertFalse(regression)
        self.assertEqual(extra["drop_ratio"], 0.75)

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
