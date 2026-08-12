from __future__ import annotations

import unittest

from app.api_only_sources import blocks_browser_fallback
from app.dataset_regression import check_dataset_regression
from app.hsreplay_client import _channel_urls
from app.scrapers.quality import (
    looks_like_real_page,
    quality_metrics,
    validate_parsed_data,
)
from app.source_contracts import contract_quality_report, get_contract
from app.sources import SOURCE_BY_ID

VALID_STREAMER_DECK_CODE = (
    "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6h"
    "Bom1BoSZB+C+B43cBwAA"
)


class SourceContractsTest(unittest.TestCase):
    def test_page_size_thresholds_come_from_source_contracts(self) -> None:
        meta = SOURCE_BY_ID["hsguru_meta_standard_legend"]
        streamer = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
        meta_contract = get_contract(meta.id)
        streamer_contract = get_contract(streamer.id)

        self.assertEqual(meta_contract.min_html_bytes, 25_000)  # type: ignore[union-attr]
        self.assertEqual(streamer_contract.min_html_bytes, 8_000)  # type: ignore[union-attr]
        self.assertFalse(looks_like_real_page("hsguru.com".ljust(24_999, "x"), meta))
        self.assertTrue(looks_like_real_page("hsguru.com".ljust(25_000, "x"), meta))
        self.assertFalse(looks_like_real_page("hsguru.com".ljust(7_999, "x"), streamer))
        self.assertTrue(looks_like_real_page("hsguru.com".ljust(8_000, "x"), streamer))

    def test_page_size_threshold_counts_utf8_bytes(self) -> None:
        meta = SOURCE_BY_ID["hsguru_meta_standard_legend"]
        multibyte_page = "hsguru.com" + "я" * 12_495

        self.assertLess(len(multibyte_page), 25_000)
        self.assertEqual(len(multibyte_page.encode("utf-8")), 25_000)
        self.assertTrue(looks_like_real_page(multibyte_page, meta))

    def test_vicious_page_without_structured_data_is_rejected(self) -> None:
        source = SOURCE_BY_ID["vicious_syndicate_live_beta"]

        ok, reason = validate_parsed_data(source, {"title": "Data Reaper Live"})

        self.assertFalse(ok)
        self.assertIn("structured data missing", reason)

    def test_hsguru_streamer_decks_keeps_public_source_identity(self) -> None:
        source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]

        self.assertEqual(source.site, "hsguru")
        self.assertEqual(source.category, "streamer_decks")
        self.assertIn("last_played=min_ago_60", source.fetch_url)
        self.assertIn("legend=1000", source.fetch_url)
        self.assertIn("violet_hold=no", source.fetch_url)
        self.assertIn("limit=100", source.fetch_url)

    def test_streamer_rolling_hour_accepts_one_or_two_complete_rows(self) -> None:
        for row_count in (1, 2):
            with self.subTest(row_count=row_count):
                report = contract_quality_report(
                    "hsguru_streamer_decks_legend_1000",
                    {
                        "type": "streamer_decks",
                        "rows": [
                            {
                                "Deck": f"Deck {index}",
                                "Streamer": f"Streamer {index}",
                                "deck_code": VALID_STREAMER_DECK_CODE,
                            }
                            for index in range(row_count)
                        ],
                    },
                )

                self.assertTrue(report["ok"], report["warnings"])
                self.assertTrue(report["low_activity"])
                self.assertEqual(report["minimum_rows"], 3)
                self.assertEqual(report["decodable_deck_codes"]["rate"], 1.0)

    def test_streamer_rolling_hour_rejects_empty_or_undecodable_rows(self) -> None:
        empty = contract_quality_report(
            "hsguru_streamer_decks_legend_1000",
            {"type": "streamer_decks", "rows": []},
        )
        invalid_code = contract_quality_report(
            "hsguru_streamer_decks_legend_1000",
            {
                "type": "streamer_decks",
                "rows": [
                    {
                        "Deck": "Deck",
                        "Streamer": "Streamer",
                        "deck_code": "AAE-this-is-filled-but-not-a-deckstring",
                    }
                ],
            },
        )

        self.assertFalse(empty["ok"])
        self.assertFalse(invalid_code["ok"])
        self.assertFalse(invalid_code["low_activity"])
        self.assertIn("too few rows", " ".join(empty["warnings"]))
        self.assertIn("decodable deck codes", " ".join(invalid_code["warnings"]))

    def test_arena_advanced_contract_blocks_bad_fallback(self) -> None:
        contract = get_contract("hsreplay_arena_cards_advanced")

        self.assertIsNotNone(contract)
        self.assertFalse(contract.allow_browser_fallback)  # type: ignore[union-attr]
        self.assertTrue(blocks_browser_fallback("hsreplay_arena_cards_advanced"))

    def test_plan_named_sources_have_contracts(self) -> None:
        for source_id in (
            "hsreplay_arena_winning_decks",
            "hsreplay_arena_legendaries",
            "hsreplay_battlegrounds_trinkets_lesser",
            "hsreplay_battlegrounds_trinkets_greater",
        ):
            with self.subTest(source_id=source_id):
                self.assertIsNotNone(get_contract(source_id))

    def test_arena_winning_decks_contract_checks_final_deck_fill(self) -> None:
        report = contract_quality_report(
            "hsreplay_arena_winning_decks",
            {
                "type": "arena_winning_decks",
                "decks": [{"title": "good", "final_deck": ["Card"]}, {"title": "bad", "final_deck": []}],
            },
        )

        self.assertFalse(report["ok"])
        self.assertIn("final_deck", report["critical_fields"])

    def test_trinket_contract_checks_canonical_id_fill(self) -> None:
        # The trinket contract now requires stats fields on top of canonical ids:
        # critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement")
        # with min_field_fill_rate=0.90 (app/source_contracts.py:150-160), so a
        # realistic full row carries pick_rate/avg_placement as well.
        report = contract_quality_report(
            "hsreplay_battlegrounds_trinkets_lesser",
            {
                "type": "bg_trinkets",
                "trinkets": [
                    {
                        "name": f"Trinket {idx}",
                        "trinket_id": f"BG30_MagicItem_{idx}",
                        "description": "A complete canonical trinket description.",
                        "pick_rate": "10.0%",
                        "avg_placement": "4.50",
                    }
                    for idx in range(80)
                ],
            },
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["quality_score"], 1.0)

    def test_trinket_contract_rejects_ranked_rows_as_canonical_rows(self) -> None:
        report = contract_quality_report(
            "hsreplay_battlegrounds_trinkets_lesser",
            {
                "type": "bg_trinkets",
                "trinkets": [{"name": f"Trinket {idx}", "pick_rate": "10%"} for idx in range(80)],
            },
        )

        self.assertFalse(report["ok"])
        self.assertIn("trinket_id", report["critical_fields"])

    def test_hsreplay_contract_overrides_channel_order(self) -> None:
        labels = [
            label
            for label, _ in _channel_urls(
                "https://hsreplay.net/api/v1/arena/card_stats/",
                source_id="hsreplay_arena_cards_advanced",
            )
        ]

        self.assertEqual(
            labels[:3],
            ["flaresolverr", "scrape_do", "curl_cffi"],
        )

    def test_contract_field_fill_rejects_missing_hidden_columns(self) -> None:
        source = SOURCE_BY_ID["hsreplay_arena_cards_advanced"]
        cards = [
            {
                "name": f"Card {idx}",
                "deck_winrate": "55%",
                "winrate_when_drawn": "56%",
                "winrate_when_played": "57%",
                "in_runs": "1%",
                "avg_copies": "1.1",
            }
            for idx in range(900)
        ]
        for card in cards[:200]:
            card.pop("winrate_when_drawn")
        parsed = {
            "title": "HSReplay Arena",
            "structured": {"type": "arena_card_tiers", "cards": cards},
            "text_preview": ["arena"] * 50,
        }

        ok, reason = validate_parsed_data(source, parsed)

        self.assertFalse(ok)
        self.assertIn("winrate_when_drawn fill rate", reason)

    def test_bg_heroes_rejects_placeholder_names_and_bad_avg(self) -> None:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]
        parsed = {
            "title": "HSReplay premium Battlegrounds heroes tier list.",
            "structured": {
                "type": "bg_heroes",
                "heroes": [
                    {
                        "hero": "—",
                        "dbfId": idx,
                        "pick_rate": "50.0%",
                        "avg_placement": "7",
                        "tier": "A",
                        "placement_distribution": ["12.50%"] * 8,
                    }
                    for idx in range(30)
                ],
            },
        }

        ok, reason = validate_parsed_data(source, parsed)

        self.assertFalse(ok)
        self.assertIn("hero fill rate", reason)

    def test_firestone_comps_accepts_small_post_patch_strategy_set(self) -> None:
        comps = [
            {
                "name": f"Patch strategy {idx}",
                "main_cards": [{"card_id": f"BG36_{idx:03d}"}],
            }
            for idx in range(7)
        ]

        report = contract_quality_report(
            "firestone_battlegrounds_comps",
            {"type": "bg_comps", "comps": comps},
        )

        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["minimum_rows"], 5)
        contract = get_contract("firestone_battlegrounds_comps")
        self.assertIsNotNone(contract)
        self.assertEqual(contract.regression_drop_ratio, 0.85)  # type: ignore[union-attr]

    def test_hsreplay_comps_allows_hidden_guide_cleanup_after_patch(self) -> None:
        contract = get_contract("hsreplay_battlegrounds_comps")

        self.assertIsNotNone(contract)
        self.assertEqual(contract.min_rows, 5)  # type: ignore[union-attr]
        self.assertEqual(contract.regression_drop_ratio, 0.85)  # type: ignore[union-attr]

    def test_hsguru_legend_patch_reset_keeps_absolute_and_field_guards(self) -> None:
        rows = [
            {
                "Archetype": f"Deck {index}",
                "Winrate↓": "52.1",
                "Popularity": "1.2% (120)",
            }
            for index in range(23)
        ]

        report = contract_quality_report(
            "hsguru_meta_standard_legend",
            {"type": "meta", "strategies": rows},
        )
        contract = get_contract("hsguru_meta_standard_legend")

        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["minimum_rows"], 10)
        self.assertEqual(contract.regression_drop_ratio, 0.75)  # type: ignore[union-attr]

    def test_hsguru_legend_patch_reset_rejects_incomplete_table(self) -> None:
        report = contract_quality_report(
            "hsguru_meta_wild_legend",
            {
                "type": "meta",
                "strategies": [
                    {"Archetype": f"Deck {index}", "Popularity": "1%"}
                    for index in range(9)
                ],
            },
        )

        self.assertFalse(report["ok"])
        self.assertIn("too few rows (9 < 10)", report["warnings"])

    def test_firestone_comps_still_rejects_truncated_post_patch_response(self) -> None:
        comps = [
            {
                "name": f"Truncated strategy {idx}",
                "main_cards": [{"card_id": f"BG36_{idx:03d}"}],
            }
            for idx in range(4)
        ]

        report = contract_quality_report(
            "firestone_battlegrounds_comps",
            {"type": "bg_comps", "comps": comps},
        )

        self.assertFalse(report["ok"])
        self.assertIn("too few rows (4 < 5)", report["warnings"])

    def test_quality_metrics_include_contract_report(self) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_legend_1d"]
        parsed = {
            "structured": {
                "type": "card_stats",
                "cards": [{"id": idx, "deck_winrate": "55%", "deck_popularity": "1%"} for idx in range(900)],
            }
        }

        metrics = quality_metrics(source, parsed)

        self.assertEqual(metrics["rows_total"], 900)
        self.assertEqual(metrics["quality_score"], 1.0)
        self.assertIn("deck_winrate", metrics["critical_fields"])

    def test_structured_api_candidate_does_not_require_html_title(self) -> None:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]
        parsed = {
            "structured": {
                "type": "bg_heroes",
                "heroes": [
                    {
                        "hero": f"Hero {idx}",
                        "dbfId": idx,
                        "pick_rate": "10.0%",
                        "avg_placement": f"{3.0 + idx / 20:.2f}",
                        "tier": ("A", "B", "C", "D")[idx % 4],
                        "placement_distribution": ["12.50%"] * 8,
                    }
                    for idx in range(30)
                ],
            }
        }

        ok, reason = validate_parsed_data(source, parsed)

        self.assertTrue(ok, reason)

    def test_daily_standard_legend_accepts_complete_low_sample_payload(self) -> None:
        cards = [
            {
                "id": idx,
                "deck_winrate": "52.0%",
                "deck_popularity": "1.0%",
            }
            for idx in range(485)
        ]

        report = contract_quality_report(
            "hsreplay_cards_legend_1d",
            {"type": "card_stats", "cards": cards},
        )

        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["minimum_rows"], 450)

    def test_daily_standard_legend_still_rejects_truncated_payload(self) -> None:
        cards = [
            {
                "id": idx,
                "deck_winrate": "52.0%",
                "deck_popularity": "1.0%",
            }
            for idx in range(449)
        ]

        report = contract_quality_report(
            "hsreplay_cards_legend_1d",
            {"type": "card_stats", "cards": cards},
        )

        self.assertFalse(report["ok"])
        self.assertIn("too few rows (449 < 450)", report["warnings"])

    def test_daily_source_uses_contract_regression_ratio(self) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_wild_legend_1d"]
        prev = {"structured": {"type": "card_stats", "cards": [{"id": idx} for idx in range(1000)]}}
        new = {"structured": {"type": "card_stats", "cards": [{"id": idx} for idx in range(520)]}}

        reg, _msg, extra = check_dataset_regression(source, previous_data=prev, new_data=new)

        self.assertFalse(reg)
        self.assertEqual(extra["drop_ratio"], 0.5)

    def test_current_patch_contract_accepts_safe_early_patch_reset(self) -> None:
        report = contract_quality_report(
            "hsreplay_cards_legend_patch",
            {
                "type": "card_stats",
                "cards": [
                    {"id": idx, "deck_winrate": "52%", "deck_popularity": "1%"}
                    for idx in range(574)
                ],
            },
        )

        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["minimum_rows"], 450)

    def test_contract_report_flags_too_few_rows(self) -> None:
        report = contract_quality_report(
            "hsreplay_meta_archetypes_legend_eu_1d",
            {"type": "hsreplay_meta_archetypes", "classes": []},
        )

        self.assertFalse(report["ok"])
        self.assertIn("too few rows", "; ".join(report["warnings"]))

    def test_contract_backed_structured_source_needs_no_duplicate_site_branch(self) -> None:
        source = SOURCE_BY_ID["metastats_decks"]
        parsed = {
            "title": "MetaStats decks",
            "structured": {
                "type": "metastats_decks",
                "decks": [
                    {
                        "archetype_name": f"Deck {idx}",
                        "win_rate": "50%",
                        "games": 100,
                    }
                    for idx in range(40)
                ],
            },
        }

        ok, reason = validate_parsed_data(source, parsed)

        self.assertTrue(ok, reason)

    def test_hsguru_meta_contract_rejects_tiny_hydration(self) -> None:
        report = contract_quality_report(
            "hsguru_meta_wild_top_legend",
            {
                "type": "meta",
                "strategies": [
                    {"Archetype": "A", "Popularity": "1%"},
                    {"Archetype": "B", "Popularity": "2%"},
                ],
            },
        )

        self.assertFalse(report["ok"])
        self.assertIn("too few rows", "; ".join(report["warnings"]))

    def test_hsguru_matchups_contract_checks_min_rows(self) -> None:
        # hsguru_matchups_legend uses the generic hsguru contract
        # (app/source_contracts.py:381-404): min_rows=3, NO critical_fields —
        # so per-field quality_score is None by design
        # (app/source_contracts.py:561: score is None when no rates collected)
        # and the contract gate is the row count.
        contract = get_contract("hsguru_matchups_legend")
        self.assertIsNotNone(contract)
        self.assertEqual(contract.critical_fields, ())  # type: ignore[union-attr]
        self.assertEqual(contract.min_rows, 3)  # type: ignore[union-attr]

        rows = [
            {"archetype": f"Deck {idx}", "vs": "Opponent", "winrate": "50%"}
            for idx in range(120)
        ]
        report = contract_quality_report(
            "hsguru_matchups_legend",
            {"type": "matchups", "matchups": rows},
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["rows_total"], len(rows))
        self.assertIsNone(report["quality_score"])

        tiny = contract_quality_report(
            "hsguru_matchups_legend",
            {"type": "matchups", "matchups": rows[:2]},
        )

        self.assertFalse(tiny["ok"])
        self.assertIn("too few rows", "; ".join(tiny["warnings"]))

    def test_hsguru_legend_matchup_sources_use_requested_samples(self) -> None:
        standard = SOURCE_BY_ID["hsguru_matchups_legend"]
        wild = SOURCE_BY_ID["hsguru_matchups_wild_legend"]

        for source in (standard, wild):
            self.assertIn("min_archetype_sample=100", source.fetch_url)
            self.assertIn("min_matchup_sample=25", source.fetch_url)
            self.assertIn("rank=legend", source.fetch_url)
            self.assertEqual(source.stale_hours, 36)
        self.assertNotIn("format=", standard.fetch_url)
        self.assertIn("format=1", wild.fetch_url)

    def test_vicious_radars_contract_rejects_tiny_optional_fetch_result(self) -> None:
        contract = get_contract("vicious_syndicate_radars")

        self.assertIsNotNone(contract)
        # min_rows was lowered to 5 (app/source_contracts.py:223-231): a radar
        # fetch is optional, but fewer than 5 radars is still a broken result.
        self.assertEqual(contract.min_rows, 5)  # type: ignore[union-attr]

        # Derive the "tiny" fixture from the contract instead of hardcoding.
        tiny_count = contract.min_rows - 1  # type: ignore[union-attr]
        report = contract_quality_report(
            "vicious_syndicate_radars",
            {
                "type": "vicious_syndicate_radars",
                "radars": [
                    {"nodes": [{"name": "A"}], "edges": [{"source": "A", "target": "B"}]}
                    for _ in range(tiny_count)
                ],
            },
        )

        self.assertFalse(report["ok"])
        self.assertIn("too few rows", "; ".join(report["warnings"]))


if __name__ == "__main__":
    unittest.main()
