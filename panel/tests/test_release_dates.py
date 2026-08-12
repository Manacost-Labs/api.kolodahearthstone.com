from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class ReleaseDateContractTest(unittest.TestCase):
    def test_pet_card_id_falls_back_to_canonical_wiki_image(self):
        pets = load_module(ROOT / "scripts" / "sync_pets.py", "sync_pets_card_id")

        self.assertEqual(
            pets.resolve_pet_card_id(
                {"card_id": "", "pet_id": 9, "level": 1},
                ["Neutral_icon.png", "PET_9_1.png", "Deathling_full.jpg"],
            ),
            "PET_9_1",
        )
        self.assertIsNone(
            pets.resolve_pet_card_id({"card_id": "", "pet_id": 9, "level": None}, []),
        )

    def test_coin_identity_falls_back_to_live_wiki_parse_data(self):
        coins = load_module(ROOT / "scripts" / "sync_coins.py", "sync_coins_identity")
        item = {
            "page_title": "Deathwing Coin",
            "wiki_image_file": "CATA COIN2.png",
        }
        parsed = {
            "wikitext": {"*": "{{Card template v2\n|dbfId=128002\n}}"},
            "images": ["CATA_COIN2.png", "CATA_COIN2_Premium1.png"],
        }

        entry = coins.coin_entry_from_parse(item, parsed)

        self.assertEqual(entry["card_id"], "CATA_COIN2")
        self.assertEqual(entry["dbf_id"], 128002)
        self.assertEqual(entry["page_title"], "Deathwing Coin")

    def test_wiki_release_dates_keep_the_earliest_added_patch_date(self):
        release_dates = load_module(
            ROOT / "scripts" / "wiki_release_dates.py",
            "wiki_release_dates",
        )
        responses = [
            {
                "cargoquery": [
                    {
                        "title": {
                            "Dbf": "120228",
                            "ReleaseDate": "2025-02-18 18:30:00",
                        }
                    },
                    {
                        "title": {
                            "Dbf": "122617",
                            "ReleaseDate": "2025-07-01 18:00:00",
                        }
                    },
                ]
            },
            {
                "cargoquery": [
                    {
                        "title": {
                            "Dbf": "130273",
                            "ReleaseDate": "2026-06-30 17:00:00",
                        }
                    }
                ]
            },
        ]

        with patch.object(
            release_dates.urllib.request,
            "urlopen",
            side_effect=[FakeResponse(payload) for payload in responses],
        ):
            result = release_dates.fetch_release_dates(
                [120228, 122617, 130273],
                user_agent="test",
                chunk_size=2,
            )

        self.assertEqual(
            result,
            {
                120228: "2025-02-18",
                122617: "2025-07-01",
                130273: "2026-06-30",
            },
        )

    def test_all_three_syncs_persist_release_date(self):
        for script_name in (
            "sync_hero_skins.py",
            "sync_pets.py",
            "sync_coins.py",
        ):
            source = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
            with self.subTest(script=script_name):
                self.assertIn("fetch_release_dates", source)
                self.assertIn("release_date", source)
                self.assertIn("ADD COLUMN release_date DATE", source)

    def test_api_exposes_release_date_for_all_three_object_types(self):
        source = (ROOT / "api" / "index.php").read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count("'release_date' =>"), 3)

    def test_web_cards_show_release_date_in_russian(self):
        source = (ROOT / "index.php").read_text(encoding="utf-8")

        self.assertIn("function format_release_date_ru", source)
        self.assertGreaterEqual(source.count("<b>Дата выхода</b>"), 3)


if __name__ == "__main__":
    unittest.main()
