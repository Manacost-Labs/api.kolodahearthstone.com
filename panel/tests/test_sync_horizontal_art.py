from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

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

    def test_subject_focus_prefers_detailed_character_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "subject.png"
            image = Image.new("RGB", (600, 800), "#253443")
            draw = ImageDraw.Draw(image)
            draw.ellipse((390, 190, 510, 310), fill="#d99b75")
            for offset in range(0, 96, 12):
                draw.line(
                    (402 + offset, 205, 402, 290 - offset),
                    fill="#17202a",
                    width=5,
                )
            draw.ellipse((420, 235, 435, 250), fill="#ffffff")
            draw.ellipse((465, 235, 480, 250), fill="#ffffff")
            image.save(source)

            focus_x, focus_y = horizontal.subject_focus(source)

            self.assertGreater(focus_x, 0.50)
            self.assertLess(focus_x, 0.62)
            self.assertGreater(focus_y, 0.20)
            self.assertLess(focus_y, 0.48)

    def test_subject_focus_ignores_detailed_outer_border(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "border.png"
            image = Image.new("RGB", (600, 800), "#263748")
            draw = ImageDraw.Draw(image)
            for inset in range(0, 45, 5):
                draw.rectangle(
                    (inset, inset, 599 - inset, 799 - inset),
                    outline="#f7ca5d",
                    width=2,
                )
            draw.ellipse((245, 210, 355, 320), fill="#b77b62")
            draw.line((260, 230, 340, 300), fill="#18212b", width=8)
            draw.line((340, 230, 260, 300), fill="#18212b", width=8)
            image.save(source)

            focus_x, focus_y = horizontal.subject_focus(source)

            self.assertGreater(focus_x, 0.38)
            self.assertLess(focus_x, 0.62)
            self.assertGreater(focus_y, 0.20)
            self.assertLess(focus_y, 0.50)

    def test_portrait_character_face_remains_visible_in_horizontal_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "portrait.png"
            target = root / "horizontal.webp"
            image = Image.new("RGB", (600, 800), "#21384a")
            draw = ImageDraw.Draw(image)
            draw.ellipse((365, 155, 515, 315), fill="#d99b75")
            draw.ellipse((400, 215, 420, 235), fill="#ffffff")
            draw.ellipse((460, 215, 480, 235), fill="#ffffff")
            draw.arc((405, 225, 475, 280), 10, 170, fill="#4a211a", width=6)
            image.save(source)

            horizontal.render_horizontal_art(source, target, "art")

            rendered = Image.open(target).convert("RGB")
            face_pixels = [
                (x, y)
                for y in range(rendered.height)
                for x in range(185, rendered.width)
                if rendered.getpixel((x, y))[0] > 150
                and rendered.getpixel((x, y))[1] > 75
                and rendered.getpixel((x, y))[2] < 155
            ]
            self.assertGreater(len(face_pixels), 450)
            self.assertGreater(
                max(y for _, y in face_pixels) - min(y for _, y in face_pixels), 28
            )

    def test_curated_crop_keeps_left_side_character_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "curated-crop.png"
            target = root / "horizontal.webp"
            image = Image.new("RGB", (243, 64), "#132839")
            draw = ImageDraw.Draw(image)
            draw.ellipse((18, 3, 82, 63), fill="#d99b75")
            draw.ellipse((34, 22, 43, 31), fill="#ffffff")
            draw.ellipse((57, 22, 66, 31), fill="#ffffff")
            draw.line((35, 45, 65, 45), fill="#4a211a", width=4)
            image.save(source)

            horizontal.render_horizontal_art(source, target, "crop")

            rendered = Image.open(target).convert("RGB")
            visible_face_pixels = sum(
                1
                for y in range(rendered.height)
                for x in range(150, rendered.width)
                if rendered.getpixel((x, y))[0] > 135
                and rendered.getpixel((x, y))[1] > 65
            )
            self.assertGreater(visible_face_pixels, 300)

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

    def test_accepts_generic_binary_image_content_type(self) -> None:
        self.assertTrue(
            horizontal.allowed_image_content_type("application/octet-stream")
        )
        self.assertTrue(horizontal.allowed_image_content_type("image/webp"))
        self.assertFalse(horizontal.allowed_image_content_type("text/html"))

    def test_square_crop_source_and_white_edge_are_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "square.jpg"
            target = root / "horizontal.webp"
            subprocess.run(
                [
                    "convert",
                    "-size",
                    "410x512",
                    "gradient:#153755-#ed782f",
                    "-size",
                    "102x512",
                    "xc:white",
                    "+append",
                    str(source),
                ],
                check=True,
            )
            horizontal.render_horizontal_art(source, target, "crop")
            pixel = subprocess.run(
                ["identify", "-format", "%[pixel:p{319,32}]", str(target)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn("255,255,255", pixel)

    def test_wide_crop_source_white_edge_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "crop.jpg"
            target = root / "horizontal.webp"
            subprocess.run(
                [
                    "convert",
                    "-size",
                    "213x64",
                    "gradient:#153755-#ed782f",
                    "-size",
                    "30x64",
                    "xc:white",
                    "+append",
                    str(source),
                ],
                check=True,
            )
            horizontal.render_horizontal_art(source, target, "crop")
            pixel = subprocess.run(
                ["identify", "-format", "%[pixel:p{319,32}]", str(target)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn("255,255,255", pixel)

    def test_blank_primary_crop_uses_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            uploads = root / "uploads"
            blank = uploads / "blank.jpg"
            fallback = uploads / "fallback.jpg"
            uploads.mkdir()
            subprocess.run(
                ["convert", "-size", "243x64", "xc:white", str(blank)], check=True
            )
            self.create_source(fallback, "900x1200")
            candidate = horizontal.Candidate(
                "hero",
                "BLANK_HERO",
                None,
                "/uploads/blank.jpg",
                "crop",
                (("/uploads/fallback.jpg", "art"),),
            )
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", uploads),
            ):
                result = horizontal.process_candidate(
                    candidate, None, force=False, dry_run=False
                )
            self.assertEqual(result["action"], "generated")
            self.assertEqual(result["source_url"], "/uploads/fallback.jpg")

    def test_process_candidate_uses_fallback_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            uploads = root / "uploads"
            fallback = uploads / "art" / "fallback.jpg"
            fallback.parent.mkdir(parents=True)
            self.create_source(fallback, "900x1200")
            candidate = horizontal.Candidate(
                "hero",
                "TEST_HERO",
                456,
                "ftp://invalid.test/primary.jpg",
                "art",
                (("/uploads/art/fallback.jpg", "art"),),
            )
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", uploads),
            ):
                result = horizontal.process_candidate(
                    candidate, None, force=False, dry_run=False
                )
            self.assertEqual(result["action"], "generated")
            self.assertEqual(result["source_url"], "/uploads/art/fallback.jpg")

    def test_permanently_missing_art_is_not_a_sync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            candidate = horizontal.Candidate(
                "battleground_card",
                "MISSING_TEST_CARD",
                None,
                "https://images.example/missing.png",
                "art",
            )
            missing = urllib.error.HTTPError(
                candidate.source_url, 404, "Not Found", None, None
            )
            with (
                mock.patch.object(horizontal, "APP_ROOT", root),
                mock.patch.object(horizontal, "UPLOAD_ROOT", root / "uploads"),
                mock.patch.object(horizontal, "download_source", side_effect=missing),
            ):
                result = horizontal.process_candidate(
                    candidate, None, force=False, dry_run=False
                )
        self.assertEqual(result["action"], "unavailable")

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

    def test_web_panel_displays_horizontal_art_for_supported_entities(self) -> None:
        panel = (ROOT / "index.php").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        self.assertIn("function panel_attach_horizontal_art(", panel)
        self.assertIn("function horizontal_art_preview(", panel)
        self.assertGreaterEqual(panel.count("horizontal_art_preview("), 9)
        self.assertIn('class="horizontal-art-button"', panel)
        self.assertIn(".horizontal-art-preview", styles)
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
            self.assertIn(f"'{entity_type}'", panel)


if __name__ == "__main__":
    unittest.main()
