import sys
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

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
            "standard",
            self.ru,
            self.en,
            blizzard_dbfs=set(),
            today=date(2026, 8, 25),
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
            "standard",
            self.ru,
            self.en,
            blizzard_dbfs=set(),
            today=date(2026, 8, 25),
        )

        self.assertEqual([card["id"] for card in fallback], ["JAIL_EVENT_100"])


class OfficialExpansionRevealTest(unittest.TestCase):
    def test_fetches_all_revealed_pages_with_optional_fields_missing(self):
        def fake_http_json(url, headers=None, data=None):
            self.assertEqual(urlparse(url).path, "/en-us/api/cards")
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query["set"], ["reign-of-the-black-empire"])
            page = int(query.get("page", ["1"])[0])
            return {
                "cardCount": 2,
                "pageCount": 2,
                "page": page,
                "cards": [{
                    "id": 130000 + page,
                    "cardSetId": 1994,
                    "collectible": 1,
                    "name": f"Revealed {page}",
                }],
            }

        with patch.object(sync, "http_json", side_effect=fake_http_json):
            cards = sync.fetch_official_reveals("en_US")

        self.assertEqual(sorted(cards), [130001, 130002])
        self.assertNotIn("image", cards[130001])

    def test_rejects_wrong_set_without_publishing_a_partial_batch(self):
        payload = {
            "cardCount": 1,
            "pageCount": 1,
            "page": 1,
            "cards": [{"id": 130001, "cardSetId": 9999, "collectible": 1, "name": "Wrong set"}],
        }
        with patch.object(sync, "http_json", return_value=payload), self.assertRaises(RuntimeError):
            sync.fetch_official_reveals("en_US")

    def test_rejects_non_textual_public_name(self):
        payload = {
            "cardCount": 1,
            "pageCount": 1,
            "page": 1,
            "cards": [{"id": 130001, "cardSetId": 1994, "collectible": 1, "name": {"hidden": True}}],
        }
        with patch.object(sync, "http_json", return_value=payload), self.assertRaises(RuntimeError):
            sync.fetch_official_reveals("en_US")

    def test_english_only_reveal_keeps_russian_name_missing(self):
        english = {
            "id": 130001,
            "name": "Amitut",
            "image": "https://d15f34w2p8l1cc.cloudfront.net/hearthstone/card.png",
            "cardSetId": 1994,
            "classId": 5,
            "cardTypeId": 4,
            "collectible": 1,
        }
        standard = [{
            "class": {"id": 5, "slug": "priest"},
            "cardType": {"id": 4, "slug": "minion"},
        }]
        card = sync.make_reveal_card(130001, None, english, standard, {}, {})

        self.assertEqual(card["card_id"], "blizzard:130001")
        self.assertIsNone(card["name_ru"])
        self.assertEqual(card["name_en"], "Amitut")
        self.assertEqual(card["card_set"], "BE")
        self.assertEqual(card["class_slug"], "priest")
        self.assertEqual(card["image_url"], "https://d15f34w2p8l1cc.cloudfront.net/hearthstone/card.png")

    def test_preview_does_not_publish_unrevealed_hearthstonejson_fields(self):
        english = {"id": 130001, "name": "Amitut", "cardSetId": 1994, "collectible": 1}
        hsj_ru = {130001: {"id": "BE_001", "name": "Скрытое имя", "text": "Скрытый текст"}}
        hsj_en = {130001: {"id": "BE_001", "name": "Secret name", "text": "Secret rules"}}

        card = sync.make_reveal_card(130001, None, english, [], hsj_ru, hsj_en)

        self.assertEqual(card["card_id"], "BE_001")
        self.assertIsNone(card["name_ru"])
        self.assertIsNone(card["text_ru"])
        self.assertIsNone(card["text_en"])
        self.assertIsNone(card["image_url"])

    def test_preview_omits_unsafe_images_and_internal_text_suffix(self):
        card = sync.make_reveal_card(
            130001,
            {"id": 130001, "name": "Карта", "cardSetId": 1994, "collectible": 1,
             "image": "javascript:alert(1)", "cropImage": "http://example.test/crop.png",
             "text": "Открытый текст ~GAMEPLAY ASPROXY BE 997"},
            None, [], {}, {},
        )

        self.assertIsNone(card["image_url"])
        self.assertIsNone(card["crop_image_url"])
        self.assertEqual(card["text_ru"], "Открытый текст")

    def test_missing_reveal_fields_do_not_erase_earlier_values(self):
        incoming = {"name_ru": None, "text_ru": None, "image_url": None, "mana_cost": None, "multi_class_json": []}
        previous = {"name_ru": "Амитут", "text_ru": "Известный текст", "image_url": "https://example.test/old.png", "mana_cost": 0, "multi_class_json": "[2, 6]"}

        merged = sync.preserve_preview_fields(incoming, previous)

        self.assertEqual(merged, {**previous, "multi_class_json": [2, 6]})

    def test_standard_sync_adds_new_reveal_without_overwriting_playable_card(self):
        playable = {"id": 130001, "name": "Playable", "cardSetId": 1994, "collectible": 1, "image": "https://example.test/playable.png"}
        announced = {"id": 130002, "name": "Announced", "cardSetId": 1994, "collectible": 1}

        with ExitStack() as stack:
            stack.enter_context(patch.object(sync, "fetch_blizzard_cards", return_value={130001: playable}))
            stack.enter_context(patch.object(sync, "fetch_official_reveals", side_effect=lambda locale: {
                130001: {**playable, "cardSetId": 1994},
                130002: announced,
            }))
            stack.enter_context(patch.object(sync, "existing_card_id_by_dbf", return_value=None))
            stack.enter_context(patch.object(sync, "current_hash", return_value=None))
            stack.enter_context(patch.object(sync, "load_existing_preview_row", return_value=None))
            save = stack.enter_context(patch.object(sync, "save_card", return_value="changed"))
            formats = stack.enter_context(patch.object(sync, "save_format"))
            removed = stack.enter_context(patch.object(sync, "mark_removed", return_value=0))
            stats = sync.sync_format(object(), "standard", "us", "token", {}, {}, False)

        self.assertEqual([item.args[1]["dbf"] for item in save.call_args_list], [130001, 130002])
        self.assertEqual(save.call_args_list[0].args[1]["card_set"], "BE")
        self.assertEqual(stats["preview"], 1)
        self.assertEqual(formats.call_args_list[1].kwargs["availability_status"], "preview")
        self.assertEqual(removed.call_args.args[2], {"blizzard:130001", "blizzard:130002"})

    def test_revealed_card_in_standard_feed_without_expansion_set_is_still_preview(self):
        game_card = {"id": 130001, "name": "Game Data name", "collectible": 1, "text": "Public Game Data rules"}
        reveal = {"id": 130001, "name": "Gallery name", "cardSetId": 1994, "collectible": 1}

        with ExitStack() as stack:
            stack.enter_context(patch.object(sync, "fetch_blizzard_cards", return_value={130001: game_card}))
            stack.enter_context(patch.object(sync, "fetch_official_reveals", return_value={130001: reveal}))
            stack.enter_context(patch.object(sync, "existing_card_id_by_dbf", return_value="blizzard:130001"))
            stack.enter_context(patch.object(sync, "load_existing_preview_row", return_value=None))
            save = stack.enter_context(patch.object(sync, "save_card", return_value="changed"))
            formats = stack.enter_context(patch.object(sync, "save_format"))
            stack.enter_context(patch.object(sync, "mark_removed", return_value=0))
            stats = sync.sync_format(object(), "standard", "us", "token", {}, {}, False)

        saved = save.call_args.args[1]
        self.assertEqual(saved["card_set"], "BE")
        self.assertEqual(saved["text_ru"], "Public Game Data rules")
        self.assertEqual(saved["source"], sync.REVEAL_SOURCE)
        self.assertEqual(saved["source_payload"]["game_data_ru"], game_card)
        self.assertEqual(saved["source_hash"], sync.stable_hash(saved["source_payload"]))
        self.assertEqual(stats["preview"], 1)
        self.assertEqual(formats.call_args.kwargs["availability_status"], "preview")

    def test_two_failed_reveal_locales_abort_before_removal(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(sync, "fetch_blizzard_cards", return_value={}))
            stack.enter_context(patch.object(sync, "fetch_official_reveals", side_effect=RuntimeError("source unavailable")))
            removed = stack.enter_context(patch.object(sync, "mark_removed"))
            with self.assertRaises(RuntimeError):
                sync.sync_format(object(), "standard", "us", "token", {}, {}, False)

        removed.assert_not_called()

    def test_mark_removed_never_deactivates_preview_rows(self):
        class Connection:
            def __init__(self):
                self.sql = ""

            def cursor(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def execute(self, sql, params):
                self.sql = sql

            rowcount = 0

        conn = Connection()
        sync.mark_removed(conn, "standard", {"KNOWN_1"}, False)
        self.assertIn("availability_status = 'available'", conn.sql)


if __name__ == "__main__":
    unittest.main()
