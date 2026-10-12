import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import catalog_integrity as integrity


class CatalogIntegrityTest(unittest.TestCase):
    def test_archival_base_never_enters_live_pool(self):
        old = [{'dbf':2, 'variant_kind':'golden', 'base_dbf':1}]
        en = [{'id':'BASE', 'dbfId':1, 'type':'MINION', 'name':'Old', 'isBattlegroundsPoolMinion':True}]
        rows, unresolved = integrity.archival_bg_bases(old, [{'id':'BASE','name':'Старая'}], en)
        self.assertFalse(unresolved)
        self.assertFalse(rows[0]['in_pool'])
        self.assertEqual(rows[0]['variant_kind'], 'base')
        self.assertEqual(rows[0]['premium_dbf'], 2)
        self.assertEqual(integrity.archival_bg_bases(old+rows, [{'id':'BASE','name':'Старая'}], en), ([], []))

    def test_archival_base_rejects_conflicting_premium_identity(self):
        old = [{'dbf':2, 'variant_kind':'golden', 'base_dbf':1}]
        en = [{'id':'BASE', 'dbfId':1, 'type':'MINION', 'name':'Old', 'battlegroundsPremiumDbfId':3}]
        self.assertEqual(integrity.archival_bg_bases(old, [{'id':'BASE','name':'Старая'}], en), ([], [1]))

    def test_class_variant_preserves_canonical_media_and_observation_date(self):
        existing = [{"card_id": "HERO_02ax", "name_en": "Ragnaros", "static_image_url": "/rag.png", "fetched_at": "2026-10-01"}]
        cards = [{"id": "HERO_02ax", "name": "Ragnaros", "artist": "Team"},
                 {"id": "HERO_01bb", "name": "Ragnaros", "artist": "Team", "set": "HERO_SKINS", "type": "HERO",
                  "cardClass": "WARRIOR", "collectible": True, "dbfId": 117487}]
        before = copy.deepcopy(existing)
        variants, unresolved = integrity.skin_variants(existing, cards)
        self.assertFalse(unresolved)
        self.assertEqual(existing, before)
        self.assertEqual(variants[0]["class_slug"], "warrior")
        self.assertEqual(variants[0]["static_image_url"], "/rag.png")
        self.assertEqual(variants[0]["fetched_at"], "2026-10-01")
        self.assertEqual(integrity.skin_variants(existing+variants, cards), ([], []))

    def test_same_name_other_art_and_other_form_are_not_shared(self):
        existing = [{"card_id": "HERO_01a_gold", "name_en": "Hero"}]
        cards = [{"id": "HERO_01a_gold", "artist": "Other"},
                 {"id": "HERO_02b", "name": "Hero", "artist": "Team", "set": "HERO_SKINS", "type": "HERO", "collectible": True}]
        self.assertEqual(integrity.skin_variants(existing, cards), ([], ["HERO_02b"]))

    def test_transform_parent_resolution_has_digit_boundary(self):
        self.assertEqual(integrity.parent_hero("HERO_59t2", {"HERO_59"}), "HERO_59")
        self.assertIsNone(integrity.parent_hero("HERO_590", {"HERO_59"}))

    def test_bg_skin_does_not_guess_unknown_parent_or_image(self):
        upstream = [{"id": "HERO_59t", "dbfId": 2}, {"id": "SKIN", "dbfId": 3, "name": "Skin", "battlegroundsSkinParentId": 2}]
        rows, unresolved = integrity.bg_skin_variants([], [{"card_id": "HERO_59"}], upstream, lambda _: None)
        self.assertFalse(unresolved)
        self.assertIsNone(rows[0]["image_url"])
        self.assertEqual(rows[0]["hero_card_id"], "HERO_59")

    def test_catalog_does_not_assert_live_membership(self):
        en = [{"id": "BGDUO_MagicItem_001", "dbfId": 1, "name": "Thread", "type": "BATTLEGROUND_TRINKET"}]
        ru = [{"id": "BGDUO_MagicItem_001", "name": "Нить"}]
        rows = integrity.library_supplements([], ru, en)
        self.assertFalse(rows[0]["in_pool"])
        self.assertEqual(rows[0]["pool_status"], "unknown")
        self.assertEqual(integrity.library_supplements(rows, ru, en), [])

    def test_timewarped_excludes_exit_and_golden_forms(self):
        en = [{"id": "BG34_X", "name": "Timewarped Card", "dbfId": 1, "type": "MINION"},
              {"id": "BG34_Exit", "name": "Exit the Timewarped Tavern", "dbfId": 2, "type": "BATTLEGROUND_SPELL"},
              {"id": "BG34_X_G", "name": "Timewarped Card", "dbfId": 3, "type": "MINION", "battlegroundsNormalDbfId": 1}]
        rows = integrity.timewarped_supplements([], [{"id": "BG34_X", "name": "Карта"}], en)
        self.assertEqual([x["card_id"] for x in rows], ["BG34_X"])

    def test_embedded_skins_merge_is_idempotent(self):
        existing = [{"heading": "Original", "cards": [{"card_id": "A"}]}]
        additions = [{"skin_card_id": "B", "title": "New"}]
        merged = integrity.merge_embedded_skins(existing, additions)
        self.assertEqual(integrity.merge_embedded_skins(merged, additions), merged)
        self.assertEqual(len(existing), 1)
