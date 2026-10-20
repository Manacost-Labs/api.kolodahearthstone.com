from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_wiki_heroes as heroes


class SnapshotStabilityTest(unittest.TestCase):
    """Whether a page came from cache or network is not a catalog change."""

    def enrich(self, source: str) -> dict:
        parser = mock.Mock(safe_filename=lambda title: title)
        result = {"page_title": "Imperial Defender", "page_url": "https://hearthstone.wiki.gg/wiki/Imperial_Defender"}
        with (
            mock.patch.object(heroes, "related_page_title_for_card", return_value="Imperial Defender"),
            mock.patch.object(heroes, "load_or_fetch_related_card_page", return_value=(result, source)),
        ):
            return heroes.enrich_related_card_from_wiki({"card_id": "BG22_HERO_007_Buddy"}, 1, Path("."), parser, False, {})

    def test_cache_and_network_fetches_store_identical_cards(self) -> None:
        self.assertEqual(self.enrich("cache"), self.enrich("network"))
        self.assertNotIn("cache_source", self.enrich("network")["wiki"])


if __name__ == "__main__":
    unittest.main()
