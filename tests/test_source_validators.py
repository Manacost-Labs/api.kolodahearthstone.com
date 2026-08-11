from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.scrapers.quality import validate_parsed_data
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID


def _hero_row(idx: int, *, name: str | None = None, avg: str | None = None) -> dict:
    place_one = 20.0 + (idx % 5) * 0.1
    rest = round((100.0 - place_one) / 7, 2)
    distribution = [f"{place_one:.2f}%"] + [f"{rest:.2f}%"] * 6
    distribution.append(f"{100.0 - place_one - rest * 6:.2f}%")
    return {
        "hero": name or f"Герой {idx}",
        "dbfId": 50_000 + idx,
        "pick_rate": f"{10 + idx / 10:.2f}%",
        "avg_placement": avg or f"{3.5 + idx / 100:.2f}",
        "tier": ["S", "A", "B", "C"][idx % 4],
        "placement_distribution": distribution,
    }


class SourceValidatorsTest(unittest.TestCase):
    def test_bg_heroes_semantic_validator_accepts_diverse_rows(self) -> None:
        structured = {
            "type": "bg_heroes",
            "heroes": [_hero_row(idx) for idx in range(40)],
        }

        report = validate_structured("hsreplay_battlegrounds_heroes", structured)

        self.assertTrue(report.ok)
        self.assertGreaterEqual(report.score, 0.95)
        self.assertEqual(report.metrics["valid_names"], 40)
        self.assertEqual(report.metrics["valid_distributions"], 40)

    def test_bg_heroes_semantic_validator_rejects_formally_filled_placeholders(self) -> None:
        structured = {
            "type": "bg_heroes",
            "heroes": [_hero_row(idx, name="—", avg="7") for idx in range(40)],
        }

        report = validate_structured("hsreplay_battlegrounds_heroes", structured)

        self.assertFalse(report.ok)
        codes = {issue.code for issue in report.issues}
        self.assertIn("bg_heroes.bad_names", codes)
        self.assertIn("bg_heroes.low_avg_diversity", codes)

    def test_quality_validation_runs_semantic_validator(self) -> None:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]
        parsed = {
            "title": "HSReplay premium Battlegrounds heroes tier list.",
            "structured": {
                "type": "bg_heroes",
                "heroes": [
                    {
                        **_hero_row(idx, avg="7"),
                        "hero": f"Герой {idx}",
                    }
                    for idx in range(40)
                ],
            },
        }

        ok, reason = validate_parsed_data(source, parsed)

        self.assertFalse(ok)
        self.assertIn("source semantic validation failed", reason)
        self.assertIn("avg_placement diversity", reason)

    def test_vicious_live_rejects_class_placeholders_as_archetypes(self) -> None:
        placeholder_decks = [
            {"deck": f"Other {hs_class}", "class": hs_class, "frequency": "9.09%"}
            for hs_class in (
                "DeathKnight",
                "DemonHunter",
                "Druid",
                "Hunter",
                "Mage",
                "Paladin",
                "Priest",
                "Rogue",
                "Shaman",
                "Warlock",
                "Warrior",
            )
        ]
        structured = {
            "type": "vicious_live",
            "deck_distribution": placeholder_decks,
            "tier_list": [
                {
                    "rank_bracket": bracket,
                    "decks": [
                        {"deck": row["deck"], "winrate": "50.00%"}
                        for row in placeholder_decks
                    ],
                }
                for bracket in ("All ranks", "Legend", "Diamond 1-4")
            ],
        }

        report = validate_structured("vicious_syndicate_live_beta", structured)

        self.assertFalse(report.ok)
        self.assertEqual(report.metrics["named_archetypes"], 0)
        self.assertEqual(report.metrics["placeholder_ratio"], 1.0)
        codes = {issue.code for issue in report.issues}
        self.assertIn("vicious_live.too_few_named_archetypes", codes)
        self.assertIn("vicious_live.placeholder_dominated", codes)

    def test_vicious_live_accepts_meaningful_archetype_names(self) -> None:
        deck_names = [
            "Rainbow DeathKnight",
            "Discover Hunter",
            "Spell Mage",
            "Starship Rogue",
            "Control Warrior",
            "Other Priest",
        ]
        structured = {
            "type": "vicious_live",
            "class_distribution": [{"class": f"Class {idx}"} for idx in range(8)],
            "deck_distribution": [{"deck": name} for name in deck_names],
            "tier_list": [
                {
                    "rank_bracket": bracket,
                    "decks": [{"deck": name, "winrate": "50.00%"} for name in deck_names],
                }
                for bracket in ("All ranks", "Legend", "Diamond 1-4", "Diamond 5-10")
            ],
        }

        report = validate_structured("vicious_syndicate_live_beta", structured)

        self.assertTrue(report.ok)
        self.assertEqual(report.metrics["named_archetypes"], 5)

    def test_vicious_live_rejects_sparse_structural_payload(self) -> None:
        structured = {
            "type": "vicious_live",
            "class_distribution": [{"class": "Mage"}],
            "deck_distribution": [{"deck": "Spell Mage"}],
            "tier_list": [
                {
                    "rank_bracket": "All ranks",
                    "decks": [{"deck": "Spell Mage", "winrate": "50%"}],
                }
            ],
        }

        report = validate_structured("vicious_syndicate_live_beta", structured)

        self.assertFalse(report.ok)
        codes = {issue.code for issue in report.issues}
        self.assertIn("vicious_live.too_few_classes", codes)
        self.assertIn("vicious_live.too_few_tier_decks", codes)

    def test_vicious_radars_publish_latest_available_issue_with_warning(self) -> None:
        structured = {
            "type": "vicious_syndicate_radars",
            "issue": "349",
            "latest_report_issue": "352",
            "latest_report_published_at": (datetime.now(UTC) - timedelta(days=10)).date().isoformat(),
        }

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertTrue(report.ok)
        issue = next(issue for issue in report.issues if issue.code == "vicious_radars.outdated_issue")
        self.assertEqual(issue.severity, "warning")

    def test_vicious_radars_warn_on_old_content_even_when_issue_matches(self) -> None:
        structured = {
            "type": "vicious_syndicate_radars",
            "issue": "352",
            "latest_report_issue": "352",
            "latest_report_published_at": (datetime.now(UTC) - timedelta(days=30)).date().isoformat(),
        }

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertTrue(report.ok)
        issue = next(issue for issue in report.issues if issue.code == "vicious_radars.stale_content")
        self.assertEqual(issue.severity, "warning")

    def test_vicious_radars_still_reject_missing_issue_metadata(self) -> None:
        report = validate_structured(
            "vicious_syndicate_radars",
            {
                "type": "vicious_syndicate_radars",
                "issue": "Unknown",
                "latest_report_issue": "352",
                "latest_report_published_at": datetime.now(UTC).date().isoformat(),
            },
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.missing_issue_freshness",
            {issue.code for issue in report.issues},
        )

    def test_vicious_radars_accept_current_recent_report(self) -> None:
        structured = {
            "type": "vicious_syndicate_radars",
            "issue": "353",
            "latest_report_issue": "353",
            "latest_report_published_at": (datetime.now(UTC) - timedelta(days=2)).date().isoformat(),
        }

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertTrue(report.ok)
        self.assertEqual(report.score, 1.0)

    def test_arena_class_matrix_requires_all_playable_classes(self) -> None:
        report = validate_structured(
            "hsreplay_arena",
            {"type": "arena_class_matrix", "classes": [{"class": idx} for idx in range(7)]},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "arena_class_matrix.too_few_classes",
            {issue.code for issue in report.issues},
        )

    def test_arena_class_pages_require_stats_for_ten_classes(self) -> None:
        good = {
            "type": "arena_class_pages",
            "classes": [
                {"class": idx, "win_rate": "50%", "pick_rate": "10%"}
                for idx in range(10)
            ],
        }
        bad = {
            **good,
            "classes": [
                {
                    **row,
                    **({"pick_rate": None} if idx == 0 else {}),
                }
                for idx, row in enumerate(good["classes"])
            ],
        }

        self.assertTrue(validate_structured("hsreplay_arena_class_pages_firecrawl", good).ok)
        report = validate_structured("hsreplay_arena_class_pages_firecrawl", bad)
        self.assertFalse(report.ok)
        self.assertIn("arena_class_pages.missing_stats", {issue.code for issue in report.issues})

    def test_arena_winning_decks_require_a_final_deck(self) -> None:
        good = {
            "type": "arena_winning_decks",
            "decks": [{"title": "12 wins", "final_deck": ["Card"]}],
        }
        bad = {"type": "arena_winning_decks", "decks": [{"title": "broken"}]}

        self.assertTrue(validate_structured("hsreplay_arena_winning_decks", good).ok)
        report = validate_structured("hsreplay_arena_winning_decks", bad)
        self.assertFalse(report.ok)
        self.assertIn(
            "arena_winning_decks.missing_final_deck",
            {issue.code for issue in report.issues},
        )

    def test_arena_legendary_groups_require_key_card(self) -> None:
        groups = [
            {
                "name": f"Group {idx}",
                "key_card": None,
                "winrate": 50.0,
                "pick_rate": 10.0,
                "offer_rate": 20.0,
                "score": 1.0,
            }
            for idx in range(10)
        ]
        report = validate_structured(
            "hsreplay_arena_legendaries",
            {"type": "arena_legendary_groups", "groups": groups},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "arena_legendary_groups.missing_key_card",
            {issue.code for issue in report.issues},
        )
        groups[0]["key_card"] = "Legendary"
        self.assertTrue(
            validate_structured(
                "hsreplay_arena_legendaries",
                {"type": "arena_legendary_groups", "groups": groups},
            ).ok
        )

    def test_bg_comps_require_cards_in_at_least_half_the_rows(self) -> None:
        comps = [
            {"name": f"Comp {idx}", "main_cards": ["Card"] if idx < 2 else []}
            for idx in range(6)
        ]
        report = validate_structured(
            "hsreplay_battlegrounds_comps",
            {"type": "bg_comps", "comps": comps},
        )

        self.assertFalse(report.ok)
        self.assertIn("bg_comps.mostly_empty", {issue.code for issue in report.issues})
        comps[2]["additional_cards"] = ["Card"]
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_comps",
                {"type": "bg_comps", "comps": comps},
            ).ok
        )

    def test_bg_card_stats_require_placement_metrics(self) -> None:
        cards = [
            {"name": f"Card {idx}", "average_placement": 4.0 if idx < 39 else None}
            for idx in range(50)
        ]
        report = validate_structured(
            "firestone_battlegrounds_cards",
            {"type": "bg_card_stats", "tiers": {"1": cards}},
        )

        self.assertFalse(report.ok)
        self.assertIn("bg_card_stats.missing_stats", {issue.code for issue in report.issues})
        cards[39]["total_played"] = 100
        self.assertTrue(
            validate_structured(
                "firestone_battlegrounds_cards",
                {"type": "bg_card_stats", "tiers": {"1": cards}},
            ).ok
        )

    def test_bg_trinkets_reject_placeholder_names(self) -> None:
        trinkets = [
            {
                "name": "—" if idx < 3 else f"Trinket {idx}",
                "pick_rate": "5%",
                "description": "A complete trinket description for validation.",
            }
            for idx in range(8)
        ]
        report = validate_structured(
            "hsreplay_battlegrounds_trinkets_lesser",
            {"type": "bg_trinkets", "trinkets": trinkets},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "bg_trinkets.invalid_names_or_stats",
            {issue.code for issue in report.issues},
        )
        for idx in range(3):
            trinkets[idx]["name"] = f"Trinket fixed {idx}"
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_trinkets_lesser",
                {"type": "bg_trinkets", "trinkets": trinkets},
            ).ok
        )

    def test_bg_trinkets_reject_truncated_descriptions(self) -> None:
        trinkets = [
            {
                "name": f"Trinket {idx}",
                "pick_rate": "5%",
                "description": (
                    "Start of Combat:"
                    if idx < 2
                    else "Start of Combat: Give your minions +2/+2 permanently."
                ),
            }
            for idx in range(8)
        ]

        report = validate_structured(
            "hsreplay_battlegrounds_trinkets_lesser",
            {"type": "bg_trinkets", "trinkets": trinkets},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "bg_trinkets.incomplete_descriptions",
            {issue.code for issue in report.issues},
        )

    def test_combined_bg_trinkets_require_both_tiers(self) -> None:
        source_id = (
            "hsreplay_battlegrounds_trinkets_top_20_percent_"
            "current_battlegrounds_patch"
        )
        trinkets = [
            {
                "name": f"Trinket {idx}",
                "pick_rate": "5%",
                "description": "Start of Combat: Give your minions +2/+2 permanently.",
                "trinket_tier": "Lesser" if idx < 4 else "Greater",
            }
            for idx in range(8)
        ]

        complete = validate_structured(
            source_id,
            {"type": "bg_trinkets", "trinkets": trinkets},
        )
        self.assertTrue(complete.ok, complete.reason)

        trinkets[-1]["trinket_tier"] = "Lesser"
        incomplete = validate_structured(
            source_id,
            {"type": "bg_trinkets", "trinkets": trinkets},
        )
        self.assertFalse(incomplete.ok)
        self.assertIn(
            "bg_trinkets.incomplete_tier_mix",
            {issue.code for issue in incomplete.issues},
        )

    def test_bg_minions_require_forty_stat_rows(self) -> None:
        minions = [
            {
                "name": f"Minion {idx}",
                "impact": 0.1 if idx < 39 else None,
                "win_share": "50%" if idx < 39 else None,
                "popularity": "5%" if idx < 39 else None,
            }
            for idx in range(50)
        ]
        report = validate_structured(
            "hsreplay_battlegrounds_minions",
            {"type": "bg_minions", "minions": minions},
        )

        self.assertFalse(report.ok)
        self.assertIn("bg_minions.missing_stats", {issue.code for issue in report.issues})
        minions[39].update({"impact": 0.2, "win_share": "50%", "popularity": "5%"})
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_minions",
                {"type": "bg_minions", "minions": minions},
            ).ok
        )

    def test_bg_compositions_require_five_complete_stat_rows(self) -> None:
        compositions = [
            {
                "name": f"Comp {idx}",
                "first_place": "20%" if idx < 4 else None,
                "avg_placement": 4.0 if idx < 4 else None,
                "popularity": "5%" if idx < 4 else None,
            }
            for idx in range(5)
        ]
        report = validate_structured(
            "hsreplay_battlegrounds_compositions",
            {"type": "bg_compositions", "compositions": compositions},
        )

        self.assertFalse(report.ok)
        self.assertIn("bg_compositions.missing_stats", {issue.code for issue in report.issues})
        compositions[4].update(
            {"first_place": "20%", "avg_placement": 4.0, "popularity": "5%"}
        )
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_compositions",
                {"type": "bg_compositions", "compositions": compositions},
            ).ok
        )

    def test_arena_card_tiers_require_labels_for_hsreplay(self) -> None:
        cards = [{"name": f"Card {idx}"} for idx in range(100)]
        report = validate_structured(
            "hsreplay_arena_cards_test",
            {"type": "arena_card_tiers", "cards": cards},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "arena_card_tiers.missing_tier_labels",
            {issue.code for issue in report.issues},
        )
        cards[0]["tier"] = "A"
        self.assertTrue(
            validate_structured(
                "hsreplay_arena_cards_test",
                {"type": "arena_card_tiers", "cards": cards},
            ).ok
        )

    def test_arena_card_tiers_keep_firestone_label_exemption(self) -> None:
        report = validate_structured(
            "firestone_arena_cards_test",
            {
                "type": "arena_card_tiers",
                "cards": [{"name": f"Card {idx}"} for idx in range(100)],
            },
        )

        self.assertTrue(report.ok)

    def test_heartharena_requires_two_hundred_tier_ids(self) -> None:
        classes = [
            {
                "class": f"Class {class_idx}",
                "cards": [
                    {
                        "name": f"Card {class_idx}-{card_idx}",
                        "tier_id": "A" if class_idx * 60 + card_idx < 199 else None,
                    }
                    for card_idx in range(60)
                ],
            }
            for class_idx in range(5)
        ]
        structured = {
            "type": "heartharena_tierlist",
            "total_cards": 300,
            "classes": classes,
        }

        report = validate_structured("heartharena_tierlist", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "heartharena_tierlist.missing_tier_ids",
            {issue.code for issue in report.issues},
        )
        classes[3]["cards"][19]["tier_id"] = "A"
        self.assertTrue(validate_structured("heartharena_tierlist", structured).ok)

    def test_card_stats_reject_blocked_and_metric_sparse_payloads(self) -> None:
        blocked = {
            "type": "card_stats",
            "blocked": True,
            "cards": [{"name": f"Card {idx}"} for idx in range(5)],
        }
        report = validate_structured("hsreplay_cards_test", blocked)
        codes = {issue.code for issue in report.issues}
        self.assertIn("card_stats.blocked_or_empty", codes)
        self.assertIn("card_stats.too_few_cards", codes)

        cards = [
            {"name": f"Card {idx}", "deck_winrate": "50%" if idx < 19 else None}
            for idx in range(30)
        ]
        report = validate_structured(
            "hsreplay_cards_test",
            {"type": "card_stats", "cards": cards},
        )
        self.assertFalse(report.ok)
        self.assertIn("card_stats.missing_metrics", {issue.code for issue in report.issues})
        cards[19]["deck_popularity"] = "1%"
        self.assertTrue(
            validate_structured(
                "hsreplay_cards_test",
                {"type": "card_stats", "cards": cards},
            ).ok
        )

    def test_hsreplay_meta_requires_twenty_complete_archetypes(self) -> None:
        classes = [
            {
                "class": f"Class {class_idx}",
                "archetypes": [
                    {
                        "archetype": f"Deck {class_idx}-{deck_idx}",
                        "winrate": "50%" if class_idx * 3 + deck_idx < 19 else None,
                        "popularity": "2%" if class_idx * 3 + deck_idx < 19 else None,
                        "games": 100 if class_idx * 3 + deck_idx < 19 else None,
                    }
                    for deck_idx in range(3)
                ],
            }
            for class_idx in range(8)
        ]
        structured = {"type": "hsreplay_meta_archetypes", "classes": classes}

        report = validate_structured("hsreplay_meta_test", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "hsreplay_meta_archetypes.missing_metrics",
            {issue.code for issue in report.issues},
        )
        classes[6]["archetypes"][1].update(
            {"winrate": "50%", "popularity": "2%", "games": 100}
        )
        self.assertTrue(validate_structured("hsreplay_meta_test", structured).ok)

    def test_hsguru_meta_uses_structured_strategy_count(self) -> None:
        structured = {
            "type": "meta",
            "strategies": [{"Archetype": f"Deck {idx}"} for idx in range(4)],
        }
        with patch("app.source_validators.threshold_for", return_value=5):
            report = validate_structured("hsguru_meta_standard_legend", structured)
        self.assertFalse(report.ok)
        self.assertIn("hsguru_meta.too_few_rows", {issue.code for issue in report.issues})
        structured["strategies"].append({"Archetype": "Deck 4"})
        with patch("app.source_validators.threshold_for", return_value=5):
            self.assertTrue(validate_structured("hsguru_meta_standard_legend", structured).ok)

    def test_hsguru_streamer_decks_accept_rows_or_codes(self) -> None:
        sparse = {"type": "streamer_decks", "rows": [{"Deck": "One"}]}
        report = validate_structured("hsguru_streamer_decks_legend_1000", sparse)
        self.assertFalse(report.ok)
        sparse["rows"].append({"Deck": "Two", "deck_code": "AAE-one"})
        sparse["rows"].append({"Deck": "Three"})
        self.assertTrue(
            validate_structured("hsguru_streamer_decks_legend_1000", sparse).ok
        )

    def test_hsguru_matchups_require_rows_and_winrates(self) -> None:
        matchups = [{"archetype": f"Deck {idx}", "vs": "Other"} for idx in range(3)]
        report = validate_structured(
            "hsguru_matchups_legend",
            {"type": "matchups", "matchups": matchups},
        )
        self.assertFalse(report.ok)
        self.assertIn(
            "hsguru_matchups.missing_winrates",
            {issue.code for issue in report.issues},
        )
        matchups[0]["winrate"] = "50%"
        self.assertTrue(
            validate_structured(
                "hsguru_matchups_legend",
                {"type": "matchups", "matchups": matchups},
            ).ok
        )


if __name__ == "__main__":
    unittest.main()
