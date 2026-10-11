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

    def test_historical_golden_identity_is_taken_from_relationship(self):
        asset = images.golden_assets([{**self.row,"card_id":"BGS_004","premium_card_id":"TB_BaconUps_003"}],{})[0]
        self.assertTrue(asset["source_url"].endswith('/TB_BaconUps_003_triple.png'))
        self.assertTrue(asset["source_candidates"][-1]["url"].endswith('/TB_BaconUps_003.png'))

    def test_golden_variant_rows_do_not_create_nested_golden_ids(self):
        golden_variant = {**self.row, "card_id": "BG36_520_G"}
        self.assertEqual(images.golden_assets([golden_variant], {}), [])

    def test_spells_never_receive_minion_golden_fallback(self):
        spell = {**self.row, "card_type": "spell"}
        self.assertEqual(images.golden_assets([spell], {}), [])

    def test_golden_refresh_keeps_old_files_and_uses_immutable_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/"old.png"
            target.write_bytes(b"old-render")
            metadata={"card_id":"BG_TEST", "target":str(target), "source_url":"https://example.invalid/bg.png"}
            def convert(command,**kwargs):
                Path(command[-1]).write_bytes(Path(command[1]).read_bytes())
            with mock.patch.object(images,"download",side_effect=lambda _url,path:path.write_bytes(b"new-validated-render")), mock.patch.object(images.subprocess,"run",side_effect=convert), mock.patch.object(images,"identify_png"):
                digest=images.replace_golden_card(metadata)
            self.assertEqual(target.read_bytes(),b"old-render")
            self.assertEqual(Path(metadata["target"]).read_bytes(),b"new-validated-render")
            self.assertEqual(metadata["public_path"],"/uploads/golden/BG_TEST-"+digest+".png")

    def test_unverified_golden_cache_is_refreshed_even_with_newer_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "old.png"
            target.write_bytes(b"wrong-constructed-frame")
            metadata = {"kind":"golden", "target":str(target), "source_url":"https://example.invalid/bg.png", "remote_timestamp":1}
            self.assertEqual(images.refresh_reason(metadata, None, False), "bootstrap_unverified_golden")
            previous = {**images.signature(metadata), "local_sha256":"old-digest"}
            self.assertEqual(images.refresh_reason(metadata, previous, False), "local_golden_changed")
            previous["local_sha256"] = images.sha256_file(target)
            self.assertIsNone(images.refresh_reason(metadata, previous, False))

    def test_verified_unchanged_image_is_republished_after_scan_rewrites_link(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "valid.png"
            target.write_bytes(b"validated-bg-frame")
            asset = {"key":"golden:A", "card_id":"A", "kind":"golden", "target":str(target), "source_url":"https://example.invalid/bg.png"}
            state = {"assets": {asset["key"]:{**images.signature(asset), "local_sha256":images.sha256_file(target)}}}
            with mock.patch.object(images, "remote_metadata", return_value=asset), mock.patch.object(images,"replace_golden_card") as replace:
                result = images.sync_assets([asset], state, False, False, 1)
            replace.assert_not_called()
            self.assertEqual(result["_verified_assets"], [asset])
            self.assertEqual(result["refreshed"],0)

    def test_archived_selection_binds_ids_without_changing_pool(self):
        conn = mock.MagicMock()
        images.active_cards(conn, None, True, ["BG_TEST"])
        sql, bindings = conn.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertNotIn("in_pool = 1", sql)
        self.assertIn("base.card_id IN (%s)", sql)
        self.assertEqual(bindings,["BG_TEST"])
        with self.assertRaises(ValueError):
            images.active_cards(conn,None,True,["evil' OR 1=1"])

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
