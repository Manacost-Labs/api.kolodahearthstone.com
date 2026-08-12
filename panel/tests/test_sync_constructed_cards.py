from pathlib import Path
from datetime import date
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_constructed_cards as sync


class HearthstoneJsonFormatFallbackTest(unittest.TestCase):
    def setUp(self):
        self.ru = {
            129959: {
                "id": "JAIL_EVENT_100",
                "dbfId": 129959,
                "name": "Кватсон",
                "text": "Русский текст",
                "set": "EVENT",
                "type": "MINION",
                "rarity": "LEGENDARY",
                "cardClass": "NEUTRAL",
                "cost": 2,
                "collectible": True,
            }
        }
        self.en = {
            129959: {
                "id": "JAIL_EVENT_100",
                "dbfId": 129959,
                "name": "Watfin",
                "text": "English text",
                "set": "EVENT",
                "type": "MINION",
                "rarity": "LEGENDARY",
                "cardClass": "NEUTRAL",
                "cost": 2,
                "collectible": True,
            }
        }

    def test_current_event_card_is_used_only_while_blizzard_omits_it(self):
        fallback = sync.hsj_format_fallback_cards(
            "standard", self.ru, self.en, blizzard_dbfs=set()
        )

        self.assertEqual([card["id"] for card in fallback], ["JAIL_EVENT_100"])
        self.assertEqual(fallback[0]["ru"]["name"], "Кватсон")
        self.assertEqual(fallback[0]["en"]["name"], "Watfin")
        normalized = sync.normalize_card(
            129959,
            fallback[0]["ru"],
            fallback[0]["en"],
            fallback[0]["ru"],
            fallback[0]["en"],
        )
        self.assertEqual(normalized["name_en"], "Watfin")
        self.assertEqual(
            normalized["image_url"],
            "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/JAIL_EVENT_100.png",
        )

        self.assertEqual(
            sync.hsj_format_fallback_cards(
                "standard", self.ru, self.en, blizzard_dbfs={129959}
            ),
            [],
        )
        self.assertEqual(
            sync.hsj_format_fallback_cards(
                "standard",
                self.ru,
                self.en,
                blizzard_dbfs=set(),
                today=date(2026, 8, 26),
            ),
            [],
        )

    def test_unlisted_hsj_cards_are_never_injected_into_a_format(self):
        self.ru[999999] = {
            **self.ru[129959],
            "id": "UNRELATED_001",
            "dbfId": 999999,
        }
        self.en[999999] = {
            **self.en[129959],
            "id": "UNRELATED_001",
            "dbfId": 999999,
        }

        fallback = sync.hsj_format_fallback_cards(
            "standard", self.ru, self.en, blizzard_dbfs=set()
        )

        self.assertEqual([card["id"] for card in fallback], ["JAIL_EVENT_100"])


if __name__ == "__main__":
    unittest.main()
