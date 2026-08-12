from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

database_helpers = types.ModuleType("backfill_constructed_images")
database_helpers.connect_db = lambda _config: None
database_helpers.load_php_config = lambda: {}
sys.modules["backfill_constructed_images"] = database_helpers

import sync_battlegrounds_images as images


class GoldenAssetSourceTest(unittest.TestCase):
    def setUp(self):
        self.row = {
            "card_id": "BG36_520",
            "dbf": 132756,
            "card_type": "minion",
            "card_image": "https://example.invalid/BG36_520.png",
            "golden_image": None,
        }

    def test_ruRU_hearthstonejson_triple_is_primary_source(self):
        asset = images.golden_assets([self.row], {})[0]

        self.assertEqual(asset["source_kind"], "hearthstonejson_ruRU_triple")
        self.assertEqual(
            asset["source_url"],
            "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/BG36_520_G_triple.png",
        )
        self.assertEqual(asset["public_path"], "/uploads/golden/BG36_520.png")
        self.assertTrue(asset["target"].endswith("/uploads/golden/BG36_520.png"))

    def test_blizzard_and_hearthpwn_are_ordered_fallbacks(self):
        blizzard_url = "https://example.invalid/blizzard-golden.png"
        asset = images.golden_assets([self.row], {132756: blizzard_url})[0]

        self.assertEqual(
            [candidate["kind"] for candidate in asset["source_candidates"]],
            ["hearthstonejson_ruRU_triple", "blizzard_fallback", "hearthpwn_fallback"],
        )
        self.assertEqual(asset["source_candidates"][1]["url"], blizzard_url)

    def test_golden_variant_rows_do_not_create_nested_golden_ids(self):
        golden_variant = {**self.row, "card_id": "BG36_520_G"}
        self.assertEqual(images.golden_assets([golden_variant], {}), [])

    def test_spells_never_receive_minion_golden_fallback(self):
        spell = {**self.row, "card_type": "spell"}
        self.assertEqual(images.golden_assets([spell], {}), [])

    def test_missing_remote_sources_preserve_valid_local_render(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "legacy.png"
            target.write_bytes(b"validated-local-image")
            asset = {
                "key": "golden:BGS_004",
                "card_id": "BGS_004",
                "kind": "golden",
                "source_url": "https://example.invalid/primary.png",
                "source_candidates": [
                    {"url": "https://example.invalid/primary.png", "kind": "primary"},
                    {"url": "https://example.invalid/fallback.png", "kind": "fallback"},
                ],
                "target": str(target),
            }
            with mock.patch.object(images, "request_with_retries", side_effect=RuntimeError("missing")):
                metadata = images.remote_metadata(asset)

        self.assertTrue(metadata["preserved_local"])
        self.assertNotIn("error", metadata)
        self.assertIn("primary: missing", metadata["preserve_reason"])


if __name__ == "__main__":
    unittest.main()
