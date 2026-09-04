from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.publish_gate import validate_candidate_for_publish
from app.scrapers.quality import validate_parsed_data
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID

VALID_STREAMER_DECK_CODE = (
    "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6h"
    "Bom1BoSZB+C+B43cBwAA"
)

VICIOUS_CLASSES = (
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


def _vicious_radars_payload(
    *,
    issue: str,
    latest_issue: str,
    radar_issues: list[str] | None = None,
    discovered_items: int | None = None,
    resolved_items: int | None = None,
    active_radar_urls: int | None = None,
    classes_attempted: int = len(VICIOUS_CLASSES),
) -> dict:
    row_issues = radar_issues or [issue] * len(VICIOUS_CLASSES)
    radars = [
        {
            "class": VICIOUS_CLASSES[index % len(VICIOUS_CLASSES)],
            "archetype": (
                None if index < len(VICIOUS_CLASSES) else f"Archetype {index}"
            ),
            "issue": row_issue,
            "radar_url": f"https://www.vicioussyndicate.com/radars/{index}/",
            "nodes": [{"name": "Card A"}, {"name": "Card B"}],
            "edges": [{"source": "Card A", "target": "Card B"}],
        }
        for index, row_issue in enumerate(row_issues)
    ]
    parsed_radars = len(radars)
    active = parsed_radars if active_radar_urls is None else active_radar_urls
    discovered = active if discovered_items is None else discovered_items
    resolved = discovered if resolved_items is None else resolved_items
    return {
        "type": "vicious_syndicate_radars",
        "issue": issue,
        "latest_report_issue": latest_issue,
        "latest_report_published_at": (
            datetime.now(UTC) - timedelta(days=2)
        ).date().isoformat(),
        "radars": radars,
        "total_radars": parsed_radars,
        "diagnostics": {
            "classes_attempted": classes_attempted,
            "discovered_items": discovered,
            "resolved_items": resolved,
            "active_radar_urls": active,
            "parsed_radars": parsed_radars,
        },
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

    def test_vicious_radars_reject_outdated_issue(self) -> None:
        structured = _vicious_radars_payload(issue="349", latest_issue="352")

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        issue = next(issue for issue in report.issues if issue.code == "vicious_radars.outdated_issue")
        self.assertEqual(issue.severity, "error")

    def test_vicious_radars_outdated_candidate_fails_publish_validation(self) -> None:
        source = SOURCE_BY_ID["vicious_syndicate_radars"]
        parsed = {
            "title": source.description,
            "structured": _vicious_radars_payload(
                issue="354",
                latest_issue="355",
            ),
        }

        gate = validate_candidate_for_publish(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )

        self.assertFalse(gate.ok)
        self.assertIn("source semantic validation failed", gate.reason)
        self.assertIn("outdated (354 < 355)", gate.reason)

    def test_vicious_radars_reject_active_radar_coverage_gap(self) -> None:
        structured = _vicious_radars_payload(
            issue="355",
            latest_issue="355",
            radar_issues=["355"] * 21,
            discovered_items=22,
            resolved_items=22,
            active_radar_urls=22,
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.incomplete_active_coverage",
            {issue.code for issue in report.issues},
        )
        self.assertEqual(report.metrics["active_radar_urls"], 22)
        self.assertEqual(report.metrics["parsed_radars"], 21)

    def test_vicious_radars_reject_mixed_row_issues(self) -> None:
        structured = _vicious_radars_payload(
            issue="355",
            latest_issue="355",
            radar_issues=["355"] * 10 + ["354"],
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.row_issue_mismatch",
            {issue.code for issue in report.issues},
        )
        self.assertEqual(report.metrics["radar_issue_counts"], {"354": 1, "355": 10})

    def test_vicious_radars_reject_dangling_graph_edges(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["radars"][0]["edges"][0]["target"] = "Missing Card"

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.invalid_graph",
            {issue.code for issue in report.issues},
        )
        self.assertEqual(report.metrics["invalid_graphs"], 1)
        self.assertEqual(report.metrics["dangling_edges"], 1)

    def test_vicious_radars_reject_duplicate_or_blank_graph_nodes(self) -> None:
        for nodes in (
            [{"name": "Card A"}, {"name": "Card A"}],
            [{"name": "Card A"}, {"name": ""}],
        ):
            with self.subTest(nodes=nodes):
                structured = _vicious_radars_payload(
                    issue="355",
                    latest_issue="355",
                )
                structured["radars"][0]["nodes"] = nodes

                report = validate_structured(
                    "vicious_syndicate_radars",
                    structured,
                )

                self.assertFalse(report.ok)
                self.assertIn(
                    "vicious_radars.invalid_graph",
                    {issue.code for issue in report.issues},
                )

    def test_vicious_radars_reject_discovery_resolution_gap(self) -> None:
        structured = _vicious_radars_payload(
            issue="355",
            latest_issue="355",
            discovered_items=12,
            resolved_items=11,
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.incomplete_discovery",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_missing_completeness_diagnostic(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["diagnostics"].pop("parsed_radars")

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.missing_completeness_diagnostics",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_non_numeric_completeness_diagnostic(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["diagnostics"]["active_radar_urls"] = "11"

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.missing_completeness_diagnostics",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_issue_ahead_of_latest_report(self) -> None:
        structured = _vicious_radars_payload(issue="356", latest_issue="355")

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.issue_ahead_of_report",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_total_radars_mismatch(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["total_radars"] = 10

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.total_mismatch",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_duplicate_stable_identity(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        duplicate = {**structured["radars"][0]}
        duplicate["radar_url"] = "https://www.vicioussyndicate.com/radars/duplicate/"
        structured["radars"].append(duplicate)
        structured["total_radars"] = 12
        structured["diagnostics"].update(
            discovered_items=12,
            resolved_items=12,
            active_radar_urls=12,
            parsed_radars=12,
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.duplicate_identity",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_duplicate_radar_url_when_present(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        duplicate_url = {**structured["radars"][0]}
        duplicate_url.update(
            {
                "class": "Mage",
                "archetype": "Unique Archetype",
            }
        )
        structured["radars"].append(duplicate_url)
        structured["total_radars"] = 12
        structured["diagnostics"].update(
            discovered_items=12,
            resolved_items=12,
            active_radar_urls=12,
            parsed_radars=12,
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.duplicate_radar_url",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_missing_radar_url(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["radars"][0].pop("radar_url")

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.invalid_radar_url",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_non_official_radar_url(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["radars"][0]["radar_url"] = "https://example.com/radars/0/"

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.invalid_radar_url",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_non_https_official_radar_url(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["radars"][0]["radar_url"] = (
            "http://www.vicioussyndicate.com/radars/0/"
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.invalid_radar_url",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_fake_class_with_same_class_count(self) -> None:
        structured = _vicious_radars_payload(issue="355", latest_issue="355")
        structured["radars"][-1]["class"] = "FakeClass"

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.incomplete_class_coverage",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_require_exact_classes_attempted_count(self) -> None:
        structured = _vicious_radars_payload(
            issue="355",
            latest_issue="355",
            classes_attempted=12,
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.invalid_classes_attempted",
            {issue.code for issue in report.issues},
        )
        self.assertLess(report.score, 1.0)

    def test_vicious_radars_reject_missing_attempted_class(self) -> None:
        structured = _vicious_radars_payload(
            issue="355",
            latest_issue="355",
            radar_issues=["355"] * 10,
            classes_attempted=11,
        )

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.incomplete_class_coverage",
            {issue.code for issue in report.issues},
        )

    def test_vicious_radars_warn_on_old_content_even_when_issue_matches(self) -> None:
        structured = _vicious_radars_payload(issue="352", latest_issue="352")
        structured["latest_report_published_at"] = (
            datetime.now(UTC) - timedelta(days=30)
        ).date().isoformat()

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertTrue(report.ok)
        issue = next(issue for issue in report.issues if issue.code == "vicious_radars.stale_content")
        self.assertEqual(issue.severity, "warning")

    def test_vicious_radars_still_reject_missing_issue_metadata(self) -> None:
        structured = _vicious_radars_payload(issue="Unknown", latest_issue="352")

        report = validate_structured(
            "vicious_syndicate_radars",
            structured,
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "vicious_radars.missing_issue_freshness",
            {issue.code for issue in report.issues},
        )

    def test_vicious_radars_accept_current_recent_report(self) -> None:
        structured = _vicious_radars_payload(issue="353", latest_issue="353")

        report = validate_structured("vicious_syndicate_radars", structured)

        self.assertTrue(report.ok)
        self.assertEqual(report.score, 1.0)
        self.assertEqual(report.metrics["active_radar_urls"], 11)
        self.assertEqual(report.metrics["parsed_radars"], 11)
        self.assertEqual(report.metrics["classes_parsed"], 11)

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

    def test_hsreplay_comps_reject_collapsed_d_tiers_without_metrics(self) -> None:
        comps = [
            {
                "name": f"Strategy {idx}",
                "tier": "D",
                "main_cards": [{"card_id": f"BG36_{idx:03d}"}],
            }
            for idx in range(19)
        ]

        report = validate_structured(
            "hsreplay_battlegrounds_comps",
            {"type": "bg_comps", "comps": comps},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "bg_comps.collapsed_hsreplay_tiers",
            {issue.code for issue in report.issues},
        )

    def test_hsreplay_comps_accept_real_tier_mix_or_metrics(self) -> None:
        tiered = [
            {
                "name": f"Strategy {idx}",
                "tier": tier,
                "main_cards": [{"card_id": f"BG36_{idx:03d}"}],
            }
            for idx, tier in enumerate(("S", "A", "B", "C", "D"))
        ]
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_comps",
                {"type": "bg_comps", "comps": tiered},
            ).ok
        )

        metrics = [
            {
                "name": f"Strategy {idx}",
                "tier": "D",
                "games": 100 + idx,
                "main_cards": [{"card_id": f"BG36_{idx:03d}"}],
            }
            for idx in range(5)
        ]
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_comps",
                {"type": "bg_comps", "comps": metrics},
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
        for minion in minions[40:]:
            minion["field_availability"] = {
                field: {
                    "available": False,
                    "reason": "no_current_patch_aggregates",
                }
                for field in ("impact", "win_share", "popularity")
            }
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

    def test_strict_bg_compositions_reject_domain_and_identity_corruption(self) -> None:
        compositions = [
            {
                "composition_id": idx + 1,
                "type": f"Comp {idx}",
                "first_place": "10.00%",
                "avg_placement": 4.0,
                "popularity": "10.00%",
                "placement_distribution": ["12.50%"] * 8,
                "games": 100,
            }
            for idx in range(10)
        ]
        payload = {
            "type": "bg_compositions",
            "completeness_schema_version": 1,
            "population_completeness": "unverifiable",
            "upstream_freshness": {
                "status": "fresh",
                "reason": None,
                "observed_at": "2026-08-14T02:20:00+00:00",
                "age_seconds": 60,
                "evidence": ["body_as_of"],
                "response_headers": {},
                "body_as_of": "2026-08-14T02:19:00+00:00",
            },
            "compositions": compositions,
        }
        self.assertTrue(
            validate_structured(
                "hsreplay_battlegrounds_compositions",
                payload,
            ).ok
        )

        compositions[-1]["composition_id"] = compositions[0]["composition_id"]
        compositions[0]["placement_distribution"] = ["20.00%"] * 8
        compositions[0]["games"] = -1
        compositions[1]["first_place"] = "9.00%"
        report = validate_structured(
            "hsreplay_battlegrounds_compositions",
            payload,
        )
        issue_codes = {issue.code for issue in report.issues}

        self.assertFalse(report.ok)
        self.assertIn("bg_compositions.duplicate_ids", issue_codes)
        self.assertIn("bg_compositions.impossible_metrics", issue_codes)
        self.assertIn("bg_compositions.first_place_total", issue_codes)

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

    def test_hsguru_streamer_decks_accepts_complete_low_activity_window(self) -> None:
        structured = {
            "type": "streamer_decks",
            "rows": [
                {
                    "Deck": "One",
                    "Streamer": "Streamer",
                    "deck_code": VALID_STREAMER_DECK_CODE,
                }
            ],
        }

        report = validate_structured(
            "hsguru_streamer_decks_legend_1000", structured
        )

        self.assertTrue(report.ok, report.reason)
        self.assertEqual(report.metrics["complete_rows"], 1)
        self.assertEqual(report.metrics["decodable_deck_codes"], 1)

    def test_hsguru_streamer_decks_rejects_any_incomplete_row(self) -> None:
        structured = {
            "type": "streamer_decks",
            "rows": [
                {
                    "Deck": "One",
                    "Streamer": "Streamer",
                    "deck_code": VALID_STREAMER_DECK_CODE,
                },
                {
                    "Deck": "Two",
                    "Streamer": "Other streamer",
                    "deck_code": "AAE-filled-but-undecodable",
                },
            ],
        }

        report = validate_structured(
            "hsguru_streamer_decks_legend_1000", structured
        )

        self.assertFalse(report.ok)
        self.assertFalse(report.metrics["low_activity"])
        self.assertIn(
            "hsguru_streamer_decks.incomplete_rows",
            {issue.code for issue in report.issues},
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
        self.assertFalse(
            validate_structured(
                "hsguru_matchups_legend",
                {"type": "matchups", "matchups": matchups},
            ).ok
        )
        for row in matchups:
            row["winrate"] = "50%"
        self.assertTrue(
            validate_structured(
                "hsguru_matchups_legend",
                {"type": "matchups", "matchups": matchups},
            ).ok
        )


if __name__ == "__main__":
    unittest.main()
