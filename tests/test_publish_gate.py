from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.publish_gate import (
    validate_candidate_for_publish,
    validate_existing_publication_for_serving,
)
from app.sources import SOURCE_BY_ID


def _bg_hero_row(index: int) -> dict:
    return {
        "hero": f"Hero {index}",
        "dbfId": 1000 + index,
        "pick_rate": f"{index / 10:.2f}%",
        "avg_placement": f"{3.5 + (index % 15) / 20:.2f}",
        "tier": ["S", "A", "B", "C"][index % 4],
        "placement_distribution": [
            "12.50%",
            "12.50%",
            "12.50%",
            "12.50%",
            "12.50%",
            "12.50%",
            "12.50%",
            "12.50%",
        ],
    }


class PublishGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]
        self.parsed = {
            "title": "Battlegrounds heroes",
            "structured": {
                "type": "bg_heroes",
                "heroes": [_bg_hero_row(index) for index in range(30)],
            },
        }

    def test_cloud_page_backends_cannot_publish_api_only_bg_heroes(self) -> None:
        for backend in (
            "firecrawl",
            "scrape_do",
            "scrape_do_super",
            "scrapfly",
            "brightdata_web_unlocker",
        ):
            with self.subTest(backend=backend):
                result = validate_candidate_for_publish(
                    self.source,
                    self.parsed,
                    backend=backend,
                )

                self.assertFalse(result.ok)
                self.assertIn("backend policy rejected", result.reason)
                self.assertEqual(result.extra["backend"], backend)
                self.assertFalse(result.extra["backend_allowed"])

    def test_structured_brightdata_wrapper_is_not_misclassified_as_page_html(self) -> None:
        source = SOURCE_BY_ID["hsreplay_cards_legend_patch"]
        result = validate_candidate_for_publish(
            source,
            {"structured": {"type": "card_stats", "cards": []}},
            backend="hsreplay_cards_api+brightdata_web_unlocker",
        )

        self.assertNotIn("backend policy rejected", result.reason)
        self.assertTrue(result.extra["backend_allowed"])

    def test_hsreplay_backend_can_publish_valid_bg_heroes(self) -> None:
        result = validate_candidate_for_publish(
            self.source,
            self.parsed,
            backend="hsreplay_premium_flaresolverr",
        )

        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.reason, "ok")
        self.assertTrue(result.extra["backend_allowed"])

    def test_page_fallback_is_rejected_for_api_only_trinkets(self) -> None:
        source = SOURCE_BY_ID["hsreplay_battlegrounds_trinkets_lesser"]
        trinkets = [
            {
                "name": f"Valid Trinket {index}",
                "trinket_id": f"BG_TEST_{index}",
                "description": "A complete canonical trinket description.",
                "pick_rate": "1.0%",
                "avg_placement": "4.0",
            }
            for index in range(80)
        ]
        parsed = {
            "title": "Battlegrounds lesser trinkets",
            "structured": {
                "type": "bg_trinkets",
                "trinkets": trinkets,
                "parser_level": "fallback_anchor",
                "dropped_rows": 3,
            },
        }

        result = validate_candidate_for_publish(source, parsed, backend="firecrawl")

        self.assertFalse(result.ok)
        self.assertIn("backend policy rejected candidate", result.reason)

        existing = validate_existing_publication_for_serving(
            source,
            parsed,
            backend="firecrawl",
        )

        self.assertTrue(existing.ok, existing.reason)
        self.assertFalse(existing.extra["backend_allowed"])
        self.assertTrue(existing.extra["existing_publication"])
        self.assertTrue(existing.extra["backend_policy_grandfathered"])

    def test_vicious_outdated_snapshot_is_usable_only_as_explicit_lkg(self) -> None:
        source = SOURCE_BY_ID["vicious_syndicate_radars"]
        classes = (
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
        radars = [
            {
                "class": class_name,
                "archetype": None,
                "issue": "354",
                "radar_url": (
                    "https://www.vicioussyndicate.com/"
                    f"wp-content/datareaper/radars/{class_name}/index.html"
                ),
                "nodes": [{"name": "Card A"}, {"name": "Card B"}],
                "edges": [{"source": "Card A", "target": "Card B"}],
            }
            for index, class_name in enumerate(classes)
        ]
        parsed = {
            "title": source.description,
            "structured": {
                "type": "vicious_syndicate_radars",
                "issue": "354",
                "latest_report_issue": "355",
                "latest_report_published_at": datetime.now(UTC).date().isoformat(),
                "upstream_state": "upstream_publication_pending",
                "radars": radars,
                "total_radars": len(radars),
                "diagnostics": {
                    "classes_attempted": len(classes),
                    "discovered_items": len(radars) + 1,
                    "resolved_items": len(radars) + 1,
                    "active_radar_urls": len(radars) + 1,
                    "parsed_radars": len(radars),
                },
            },
        }

        candidate = validate_candidate_for_publish(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )
        existing = validate_existing_publication_for_serving(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )

        self.assertFalse(candidate.ok)
        self.assertTrue(existing.ok)
        self.assertTrue(existing.extra["lkg_temporal_grandfathered"])

        for field, invalid_value in (
            ("nodes", []),
            ("nodes", "not-a-node-list"),
            ("edges", "not-an-edge-list"),
        ):
            with self.subTest(field=field, invalid_value=invalid_value):
                parsed["structured"]["radars"][0][field] = invalid_value
                corrupt = validate_existing_publication_for_serving(
                    source,
                    parsed,
                    backend="vicious_syndicate_api",
                )
                self.assertFalse(corrupt.ok)
                parsed["structured"]["radars"][0][field] = (
                    [{"name": "Card 0"}]
                    if field == "nodes"
                    else [{"source": "Card A", "target": "Card B"}]
                )

        parsed["structured"]["radars"][0]["issue"] = "353"
        mixed_issue = validate_existing_publication_for_serving(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )
        self.assertFalse(mixed_issue.ok)

        parsed["structured"]["radars"][0]["issue"] = "354"
        parsed["structured"]["diagnostics"].update(
            discovered_items=100,
            resolved_items=100,
            active_radar_urls=100,
            parsed_radars=len(radars),
        )
        grossly_incomplete = validate_existing_publication_for_serving(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )
        self.assertFalse(grossly_incomplete.ok)

        parsed["structured"]["diagnostics"].update(
            discovered_items=len(radars) - 1,
            resolved_items=len(radars) - 1,
            active_radar_urls=len(radars) - 1,
            parsed_radars=len(radars),
        )
        impossible_coverage = validate_existing_publication_for_serving(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )
        self.assertFalse(impossible_coverage.ok)

        parsed["structured"]["diagnostics"].update(
            discovered_items=len(radars) + 1,
            resolved_items=len(radars) + 1,
            active_radar_urls=len(radars) + 1,
            parsed_radars=len(radars),
        )
        parsed["structured"]["radars"][0]["edges"][0]["target"] = "Missing Card"
        dangling_graph = validate_existing_publication_for_serving(
            source,
            parsed,
            backend="vicious_syndicate_api",
        )
        self.assertFalse(dangling_graph.ok)


if __name__ == "__main__":
    unittest.main()
