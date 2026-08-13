from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_horizontal_art as horizontal


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

    def test_process_local_candidate_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            uploads = root / "uploads"
            source = uploads / "art" / "TEST_CARD.jpg"
            source.parent.mkdir(parents=True)
            self.create_source(source, "900x1200")
            candidate = horizontal.Candidate(
                "battleground_card",
                "TEST_CARD",
                123,
                "/uploads/art/TEST_CARD.jpg",
                "art",
            )
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", uploads),
            ):
                generated = horizontal.process_candidate(
                    candidate,
                    None,
                    force=False,
                    dry_run=False,
                )
                self.assertEqual(generated["action"], "generated")
                target = horizontal.local_upload_path(generated["public_path"])
                self.assertEqual(horizontal.identify_size(target), (320, 64))
                unchanged = horizontal.process_candidate(
                    candidate,
                    {
                        "status": "ready",
                        "source_signature": generated["source_signature"],
                        "recipe_version": horizontal.RECIPE_VERSION,
                    },
                    force=False,
                    dry_run=False,
                )
                self.assertEqual(unchanged["action"], "unchanged")

    def test_local_upload_path_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            uploads = root / "uploads"
            uploads.mkdir()
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", uploads),
                self.assertRaisesRegex(ValueError, "escapes media root"),
            ):
                horizontal.local_upload_path("/uploads/../outside.webp")

    def test_job_and_timer_are_wired(self) -> None:
        runner = (ROOT / "scripts" / "run_sync_job.sh").read_text(encoding="utf-8")
        timer = ROOT / "systemd" / "kolodahs-sync-horizontal-art.timer"
        self.assertIn("horizontal-art)", runner)
        self.assertIn('sync_horizontal_art.py"', runner)
        self.assertTrue(timer.is_file())
        self.assertIn(
            "kolodahs-sync@horizontal-art.service", timer.read_text(encoding="utf-8")
        )

    def test_rest_api_exposes_horizontal_art_for_supported_entities(self) -> None:
        api = (ROOT / "api" / "index.php").read_text(encoding="utf-8")
        self.assertIn("function attach_horizontal_art(", api)
        self.assertIn("'horizontal' =>", api)
        for entity_type in (
            "battleground_card",
            "constructed_card",
            "hero",
            "hero_skin",
            "pet",
            "coin",
            "timewarped_card",
            "library_card",
        ):
            self.assertIn(f"'{entity_type}'", api)


if __name__ == "__main__":
    unittest.main()
