from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_horizontal_art as horizontal


class OffloadedTargetTest(unittest.TestCase):
    """Generated art is staged locally even after offload linked it to S3."""

    def test_target_keeps_offloaded_link_path_inside_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            uploads = root / "uploads"
            remote = uploads / ".s3-storage" / "horizontal-art" / "battleground_card"
            remote.mkdir(parents=True)
            (remote / "BG1.webp").write_bytes(b"old")
            local_dir = uploads / "horizontal-art" / "battleground_card"
            local_dir.mkdir(parents=True)
            (local_dir / "BG1.webp").symlink_to(remote / "BG1.webp")
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", uploads),
            ):
                target = horizontal.local_upload_path(
                    "/uploads/horizontal-art/battleground_card/BG1.webp"
                )
            self.assertEqual(target, local_dir.resolve() / "BG1.webp")
            staged = root / "staged.webp"
            staged.write_bytes(b"new")
            os.replace(staged, target)
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual((remote / "BG1.webp").read_bytes(), b"old")

    def test_parent_escape_is_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            uploads = root / "uploads"
            (uploads / "horizontal-art").mkdir(parents=True)
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", uploads),
            ):
                for path in (
                    "/uploads/../outside.webp",
                    "/uploads/horizontal-art/../../outside.webp",
                    "/uploads/horizontal-art/..",
                ):
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        horizontal.local_upload_path(path)


if __name__ == "__main__":
    unittest.main()
