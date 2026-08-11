from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.hsreplay_bg_heroes import (
    build_heroes_from_stats,
    merge_hero_stats,
    parse_hsreplay_bg_hero_stats_text,
    reconcile_heroes_with_stats,
)
from app.hsreplay_cards_api import parse_cards_from_api_payloads
from app.hsreplay_meta_api import normalize_meta_archetypes
from app.vicious_live import build_ladder_view

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ContractFixturesTest(unittest.TestCase):
    def test_hsreplay_card_list_contract_preserves_hidden_columns(self) -> None:
        payload = load_fixture("hsreplay_card_list.json")

        cards = parse_cards_from_api_payloads(
            [("https://hsreplay.net/analytics/query/card_list/", payload)],
            sort_mode="popularity",
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["dbfId"], 69545)
        self.assertEqual(card["deck_popularity"], "24.90%")
        self.assertEqual(card["deck_winrate"], "52.01%")
        self.assertEqual(card["winrate_when_drawn"], "53.50%")
        self.assertEqual(card["avg_turns_in_hand"], 0.75)
        self.assertEqual(card["avg_turn_played_on"], 4.53)

    def test_hsreplay_meta_archetypes_contract_groups_by_class(self) -> None:
        payload = load_fixture("hsreplay_meta_archetypes.json")

        classes = normalize_meta_archetypes(
            payload,
            {
                52: {"name": "Token Druid", "url": "/archetypes/52/token-druid"},
                142: {"name": "Burn Mage", "url": "/archetypes/142/burn-mage"},
            },
        )

        self.assertEqual(classes[0]["class"], "DRUID")
        self.assertEqual(classes[0]["games"], 1294)
        self.assertEqual(classes[0]["archetypes"][0]["archetype"], "Token Druid")
        self.assertEqual(classes[0]["archetypes"][0]["winrate"], "51.06%")
        self.assertEqual(classes[0]["archetypes"][0]["popularity"], "6.55%")

    def test_vicious_ladder_contract_builds_distributions(self) -> None:
        payload = load_fixture("vicious_ladder_data.json")

        view = build_ladder_view(payload["lastDay"])

        self.assertEqual(view["games"], 2000)
        self.assertEqual(view["class_distribution"][0]["class"], "DeathKnight")
        self.assertEqual(view["class_distribution"][0]["frequency"], "70.00%")
        self.assertEqual(view["deck_distribution"][0]["deck"], "Alpha DeathKnight")

    def test_hsreplay_bg_hero_stats_contract_preserves_distribution(self) -> None:
        payload = load_fixture("hsreplay_bg_hero_stats.json")

        stats = parse_hsreplay_bg_hero_stats_text(json.dumps(payload))

        self.assertEqual(stats[64400]["tier_v2"], "A")
        self.assertEqual(stats[64400]["api_avg_placement"], "4.42")
        self.assertEqual(len(stats[64400]["placement_distribution"]), 8)
        self.assertEqual(stats[64400]["placement_distribution"][0], "15.00%")

    def test_hsreplay_bg_hero_stats_merge_overwrites_page_metrics(self) -> None:
        heroes = [{"hero": "Раканишу", "dbfId": 64400, "pick_rate": "1%", "avg_placement": "7"}]
        stats = parse_hsreplay_bg_hero_stats_text(
            json.dumps(load_fixture("hsreplay_bg_hero_stats.json"))
        )

        merged = merge_hero_stats(heroes, stats)

        self.assertEqual(merged[0]["pick_rate"], "8.70%")
        self.assertEqual(merged[0]["avg_placement"], "4.42")
        self.assertEqual(merged[0]["tier"], "A")
        self.assertEqual(len(merged[0]["placement_distribution"]), 8)

    def test_hsreplay_bg_hero_stats_builds_rows_when_html_is_empty(self) -> None:
        stats = {
            57946: {
                "placement_distribution": ["22.96%", "15.97%"],
                "tier_v2": "S",
                "api_pick_rate": "47.00%",
                "api_avg_placement": "3.78",
                "best_composition_id": 8,
            }
        }

        heroes = build_heroes_from_stats(stats)

        self.assertEqual(heroes[0]["dbfId"], 57946)
        self.assertNotEqual(heroes[0]["hero"], "—")
        self.assertEqual(heroes[0]["pick_rate"], "47.00%")
        self.assertEqual(heroes[0]["avg_placement"], "3.78")

    def test_hsreplay_bg_hero_stats_recovers_rows_missing_from_rendered_page(self) -> None:
        page_heroes = [
            {
                "hero": f"Page Hero {idx}",
                "dbfId": 100_000 + idx,
                "best_comp": "Механизмы",
                "pick_rate": "1.00%",
            }
            for idx in range(30)
        ]
        stats = {
            100_000 + idx: {
                "placement_distribution": ["12.50%"] * 8,
                "tier_v2": "A",
                "api_pick_rate": f"{idx + 1:.2f}%",
                "api_avg_placement": "4.50",
                "best_composition_id": 8,
            }
            for idx in range(42)
        }

        heroes = reconcile_heroes_with_stats(page_heroes, stats)

        self.assertEqual(len(heroes), 42)
        by_dbf = {hero["dbfId"]: hero for hero in heroes}
        self.assertEqual(by_dbf[100_000]["best_comp"], "Механизмы")
        self.assertEqual(by_dbf[100_000]["pick_rate"], "1.00%")
        self.assertEqual(by_dbf[100_041]["pick_rate"], "42.00%")
        self.assertEqual(by_dbf[100_041]["tier"], "A")

    def test_hsreplay_bg_hero_stats_does_not_expand_from_smaller_api_result(self) -> None:
        page_heroes = [{"hero": f"Hero {idx}", "dbfId": idx} for idx in range(40)]
        stats = {
            idx: {
                "placement_distribution": ["12.50%"] * 8,
                "tier_v2": "A",
                "api_pick_rate": "1.00%",
                "api_avg_placement": "4.50",
                "best_composition_id": 8,
            }
            for idx in range(30)
        }

        heroes = reconcile_heroes_with_stats(page_heroes, stats)

        self.assertEqual(len(heroes), 40)


if __name__ == "__main__":
    unittest.main()
