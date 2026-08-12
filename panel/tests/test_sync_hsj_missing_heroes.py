from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_hsj_missing_heroes as sync


class HeroImageCandidateTest(unittest.TestCase):
    def test_wiki_card_and_full_art_follow_missing_hsj_render(self):
        candidates = sync.hero_image_candidates("BG36_HERO_105")

        self.assertEqual(
            candidates,
            [
                "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/BG36_HERO_105.png",
                "https://hearthstone.wiki.gg/wiki/Special:Redirect/file/BG36_HERO_105.png",
                "https://art.hearthstonejson.com/v1/orig/BG36_HERO_105.png",
            ],
        )

    def test_only_hsj_owned_existing_rows_are_refreshed(self):
        self.assertTrue(
            sync.should_sync_existing_hero(
                "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/BG36_HERO_105.png"
            )
        )
        self.assertFalse(
            sync.should_sync_existing_hero(
                "https://hearthstone.wiki.gg/images/BG36_HERO_105.png"
            )
        )
        self.assertFalse(sync.should_sync_existing_hero(None))


if __name__ == "__main__":
    unittest.main()
