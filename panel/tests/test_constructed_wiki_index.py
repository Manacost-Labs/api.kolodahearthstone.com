import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_constructed_wiki_meta as sync


class ConstructedWikiIndexTest(unittest.TestCase):
    def test_default_uses_freshest_index_but_custom_path_is_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            own, shared, custom = [Path(directory) / name for name in ["own", "shared", "custom"]]
            for i, path in enumerate([own, shared, custom], 1):
                path.write_text(json.dumps({"marker": path.name}))
                os.utime(path, (i, i))
            lookup = Mock()
            with patch.object(sync, "DEFAULT_INDEX_PATH", own), patch.object(sync, "SHARED_INDEX_PATH", shared):
                self.assertEqual(sync.load_index(lookup, own, False)[0], {"marker": "shared"})
                os.utime(own, (5, 5))
                self.assertEqual(sync.load_index(lookup, own, False)[0], {"marker": "own"})
                self.assertEqual(sync.load_index(lookup, custom, False)[0], {"marker": "custom"})
            lookup.load_or_refresh_index.assert_not_called()

    def test_explicit_refresh_is_not_replaced_by_cached_index(self):
        lookup = Mock()
        lookup.load_or_refresh_index.return_value = ({"fresh": True}, True)
        path = Path("explicit-cache.json")
        self.assertEqual(sync.load_index(lookup, path, True), ({"fresh": True}, True, str(path)))
        lookup.load_or_refresh_index.assert_called_once_with(path, "card", True)
