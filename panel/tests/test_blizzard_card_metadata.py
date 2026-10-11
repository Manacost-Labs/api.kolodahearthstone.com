import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from blizzard_card_metadata import reference_cards
import sync_constructed_cards as sync


class BlizzardMetadataTest(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "classes": [{"id": 1, "slug": "deathknight"}],
            "types": [{"id": 4, "slug": "minion"}],
            "rarities": [{"id": 5, "slug": "legendary"}],
            "minionTypes": [], "spellSchools": [],
        }

    def test_preview_without_hsj_gets_class_type_and_rarity(self):
        raw = {"id": 130001, "name": "Revealed card", "cardSetId": 1994,
               "classId": 1, "cardTypeId": 4, "rarityId": 5, "collectible": 1}
        card = sync.make_reveal_card(130001, raw, raw, reference_cards(self.metadata), {}, {})
        self.assertEqual((card["class_slug"], card["card_type"], card["rarity"]),
                         ("deathknight", "minion", "legendary"))
        self.assertEqual(card["card_set"], "BE")
        self.assertEqual(card["card_id"], "blizzard:130001")

    def test_unknown_ids_are_not_guessed(self):
        raw = {"id": 130001, "name": "Revealed card", "classId": 999,
               "cardTypeId": 999, "rarityId": 999}
        card = sync.make_reveal_card(130001, raw, raw, reference_cards(self.metadata), {}, {})
        self.assertIsNone(card["class_slug"])
        self.assertIsNone(card["card_type"])
        self.assertIsNone(card["rarity"])

    def test_integer_zero_preview_is_not_collectible(self):
        for value in (0, False):
            with self.subTest(value=value):
                raw = {"id": 130001, "name": "Child card", "collectible": value}
                card = sync.make_reveal_card(130001, raw, raw, reference_cards(self.metadata), {}, {})
                self.assertEqual(card["collectible"], 0)

    def test_missing_or_empty_required_metadata_is_rejected(self):
        for key in ["classes", "types", "rarities"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                reference_cards({**self.metadata, key: []})

    def test_fetch_uses_authenticated_metadata_endpoint(self):
        with patch.object(sync, "http_json", return_value=self.metadata) as fetch:
            self.assertEqual(sync.fetch_metadata_reference_cards("eu", "fixture-token"),
                             reference_cards(self.metadata))
        fetch.assert_called_once_with("https://eu.api.blizzard.com/hearthstone/metadata?locale=en_US",
                                      headers={"Authorization": "Bearer fixture-token"})

    def test_metadata_failure_stops_before_catalog_writes(self):
        raw = {"id": 130001, "name": "Revealed card", "cardSetId": 1994}
        with patch.object(sync, "fetch_blizzard_cards", return_value={}), \
             patch.object(sync, "fetch_official_reveals", return_value={130001: raw}), \
             patch.object(sync, "fetch_metadata_reference_cards", side_effect=ValueError("missing metadata")), \
             patch.object(sync, "save_card") as save, \
             self.assertRaises(ValueError):
            sync.sync_format(object(), "standard", "us", "fixture-token", {}, {}, False)
        save.assert_not_called()
