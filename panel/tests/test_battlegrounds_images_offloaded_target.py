from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_battlegrounds_images as images


class OffloadedTargetTest(unittest.TestCase):
    """Image targets stay in local staging after offload linked them to S3."""

    def test_offloaded_link_is_a_valid_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            remote = root / "uploads" / ".s3-storage" / "cards"
            remote.mkdir(parents=True)
            (remote / "BG_BOT_911.png").write_bytes(b"png")
            cards = root / "uploads" / "cards"
            cards.mkdir()
            (cards / "BG_BOT_911.png").symlink_to(remote / "BG_BOT_911.png")
            with mock.patch.object(images, "APP_ROOT", root):
                target = images.local_path("/uploads/cards/BG_BOT_911.png", "cards")
            self.assertEqual(target, cards.resolve() / "BG_BOT_911.png")

    def test_escapes_are_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "uploads" / "cards").mkdir(parents=True)
            with mock.patch.object(images, "APP_ROOT", root):
                for path in ("/uploads/cards/../golden/x.png", "/uploads/cards/sub/x.png", "/uploads/cards/.."):
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        images.local_path(path, "cards")


if __name__ == "__main__":
    unittest.main()
