from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.hsreplay_legendaries_api import (
    _groups_from_class_buckets,
    _load_arena_card_stats_index,
    enrich_legendary_groups,
    normalize_legendary_package,
)
from app.source_validators import validate_structured


class LegendaryGroupsByClassTests(unittest.TestCase):
    def test_cached_enrichment_reads_resolved_public_arena_dataset(self) -> None:
        cached = {
            "data": {
                "structured": {
                    "cards": [
                        {
                            "card_id": "STABLE_CARD",
                            "pick_rate": 2.5,
                        }
                    ]
                }
            }
        }
        with (
            patch(
                "app.hsreplay_arena_api.fetch_arena_card_tiers",
                new=AsyncMock(side_effect=RuntimeError("offline")),
            ),
            patch(
                "app.hsreplay_legendaries_api.load_resolved_public_dataset",
                return_value=cached,
            ) as resolved_loader,
        ):
            stats, backend = asyncio.run(
                _load_arena_card_stats_index("hsreplay_arena_legendaries")
            )

        resolved_loader.assert_called_once_with("hsreplay_arena_cards_advanced")
        self.assertEqual(backend, "cached_hsreplay_arena_cards_advanced")
        self.assertEqual(stats["STABLE_CARD"]["pick_rate"], 2.5)

    def test_groups_keep_per_class_metrics(self) -> None:
        groups = _groups_from_class_buckets(
            {
                "ALL": [
                    {
                        "package_key_card_id": "JAIL_851",
                        "package_card_ids": ["REV_022", "TRL_520", "TSC_069"],
                        "win_rate": 45.07,
                        "pick_rate": 3.83,
                        "offer_rate": 5.21,
                        "score": 3.4,
                    }
                ],
                "DEATHKNIGHT": [
                    {
                        "package_key_card_id": "JAIL_851",
                        "package_card_ids": ["REV_022", "TRL_520", "TSC_069"],
                        "win_rate": 66.67,
                        "pick_rate": 0.68,
                        "offer_rate": 4.3,
                        "score": 1.2,
                    }
                ],
            },
            locale="enUS",
        )
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["winrate"], "45.07%")
        self.assertEqual(group["pick_rate"], "3.83%")
        self.assertEqual(group["score"], 3.4)
        self.assertEqual(group["by_class"]["all"]["winrate"], "45.07%")
        self.assertEqual(group["by_class"]["death-knight"]["winrate"], "66.67%")
        self.assertEqual(group["by_class"]["death-knight"]["pick_rate"], "0.68%")
        self.assertEqual(group["by_class"]["death-knight"]["score"], 1.2)

    def test_normalize_package_keeps_winrate(self) -> None:
        group = normalize_legendary_package(
            {
                "package_key_card_id": "TOY_813",
                "package_card_ids": ["TOY_813", "TOY_813t"],
                "win_rate": 62.9,
                "pick_rate": 83.3,
                "offer_rate": 3.9,
                "score": 53,
            },
            locale="enUS",
        )
        assert group is not None
        self.assertEqual(group["key_card"]["card_id"], "TOY_813")
        self.assertEqual(group["winrate"], "62.9%")
        self.assertEqual(group["pick_rate"], "83.3%")
        self.assertEqual(group["score"], 53.0)

    def test_enrich_fills_only_missing_all_metrics(self) -> None:
        groups = [
            {
                "key_card": {"card_id": "TOY_813", "name": "Toy Captain Tarim"},
                "winrate": "62.9%",
                "pick_rate": None,
                "offer_rate": None,
                "score": None,
                "by_class": {"all": {"winrate": "62.9%"}},
            }
        ]
        filled = enrich_legendary_groups(
            groups,
            {
                "TOY_813": {
                    "pick_rate": 83.3,
                    "offer_rate": 3.9,
                    "score": 53,
                }
            },
        )
        self.assertEqual(filled["joined"], 1)
        self.assertEqual(groups[0]["pick_rate"], "83.3%")
        self.assertEqual(groups[0]["by_class"]["all"]["score"], 53.0)

    def test_validator_requires_arenasmith_metrics(self) -> None:
        groups = [
            {
                "key_card": {"card_id": f"CARD_{idx}"},
                "winrate": "50%",
                "pick_rate": None,
                "offer_rate": None,
                "score": None,
            }
            for idx in range(10)
        ]
        report = validate_structured(
            "hsreplay_arena_legendaries",
            {"type": "arena_legendary_groups", "groups": groups},
        )
        self.assertFalse(report.ok)
        for row in groups:
            row["pick_rate"] = "10%"
            row["offer_rate"] = "2%"
            row["score"] = 40
        self.assertTrue(
            validate_structured(
                "hsreplay_arena_legendaries",
                {"type": "arena_legendary_groups", "groups": groups},
            ).ok
        )


class LegendaryGroupsFetchFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_failure_uses_firecrawl_fallback(self) -> None:
        from app import hsreplay_legendaries_api as mod

        fallback_payload = {
            "type": "arena_legendary_groups",
            "groups": [{"key_card": {"card_id": "TOY_813"}}],
            "source": {"backend": "firecrawl+hsreplay_api"},
        }
        with (
            patch.object(mod, "fetch_hsreplay_json", AsyncMock(side_effect=RuntimeError("cf"))),
            patch.object(
                mod,
                "fetch_legendary_groups_via_firecrawl",
                AsyncMock(return_value=fallback_payload),
            ) as firecrawl,
        ):
            result = await mod.fetch_legendary_groups(source_id="hsreplay_arena_legendaries")
        firecrawl.assert_awaited_once()
        self.assertEqual(result["source"]["backend"], "firecrawl+hsreplay_api")


if __name__ == "__main__":
    unittest.main()
