from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_horizontal_art as horizontal  # noqa: E402


class HorizontalArtTest(unittest.TestCase):
    def create_source(self, path: Path, size: str) -> None:
        subprocess.run(
            [
                "convert",
                "-size",
                size,
                "gradient:#102030-#f08030",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_render_supported_source_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixtures = {
                "crop": "243x64",
                "card": "256x388",
                "art": "900x1200",
            }
            for source_kind, size in fixtures.items():
                with self.subTest(source_kind=source_kind):
                    source = root / f"{source_kind}.png"
                    target = root / f"{source_kind}.webp"
                    self.create_source(source, size)
                    horizontal.render_horizontal_art(source, target, source_kind)
                    self.assertEqual(horizontal.identify_size(target), (320, 64))
                    self.assertGreater(target.stat().st_size, 0)

    def test_output_filename_is_stable_and_collision_safe(self) -> None:
        self.assertEqual(horizontal.output_filename("BG_TEST_001"), "BG_TEST_001.webp")
        first = horizontal.output_filename("quest:BG_TEST_001")
        second = horizontal.output_filename("quest/BG_TEST_001")
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith(".webp"))

    def test_rejects_unknown_source_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source.png"
            target = Path(tmp_dir) / "target.webp"
            self.create_source(source, "243x64")
            with self.assertRaisesRegex(ValueError, "Unsupported source kind"):
                horizontal.render_horizontal_art(source, target, "unknown")


if __name__ == "__main__":
    unittest.main()
