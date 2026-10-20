from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import StringIO
from itertools import pairwise
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_horizontal_art as horizontal


class HorizontalArtTest(unittest.TestCase):
    def test_revealed_cards_use_only_trusted_official_illustration_crops(self) -> None:
        official = "https://d15f34w2p8l1cc.cloudfront.net/hearthstone/illustration.png"
        fixtures = [
            {
                "card_id": "blizzard:1",
                "source_payload_json": json.dumps(
                    {
                        "official_reveal_en": {
                            "cropImage": official,
                            "image": "https://images.example/framed.png",
                        },
                        "blizzard_en": {"cropImage": official},
                    }
                ),
            },
            {
                "card_id": "blizzard:2",
                "source_payload_json": json.dumps(
                    {
                        "official_reveal_en": {
                            "cropImage": "https://images.example/framed.png"
                        }
                    }
                ),
            },
            {"card_id": "blizzard:3", "source_payload_json": "invalid"},
        ]
        with mock.patch.object(
            horizontal,
            "query_rows",
            side_effect=lambda conn, query: (
                fixtures if "FROM constructed_cards" in query else []
            ),
        ):
            candidates = horizontal.collect_candidates(mock.Mock(), {})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_url, official)
        self.assertEqual(candidates[0].source_kind, "art")
        self.assertEqual(candidates[0].fallback_sources, ())

    def test_constructed_reprints_try_their_own_art_before_original_card_art(
        self,
    ) -> None:
        for prefix in ("CORE_", "VAN_", "LEG_"):

            def rows(conn, query, prefix=prefix):
                return (
                    [{"card_id": prefix + "RLK_079", "dbf": 1}]
                    if "FROM constructed_cards" in query
                    else []
                )

            with mock.patch.object(horizontal, "query_rows", side_effect=rows):
                candidates = horizontal.collect_candidates(mock.Mock(), {})
            self.assertEqual(
                candidates[0].source_url,
                "https://art.hearthstonejson.com/v1/tiles/" + prefix + "RLK_079.webp",
            )
            self.assertEqual(
                candidates[0].fallback_sources,
                (
                    ("https://art.hearthstonejson.com/v1/tiles/RLK_079.webp", "art"),
                    (
                        "https://art.hearthstonejson.com/v1/orig/"
                        + prefix
                        + "RLK_079.png",
                        "art",
                    ),
                    ("https://art.hearthstonejson.com/v1/orig/RLK_079.png", "art"),
                ),
            )

    def test_card_candidates_use_full_art_and_hsj_for_constructed_missing_wiki(
        self,
    ) -> None:
        tables = {
            "battlegrounds_cards": ("battleground_card", "art_image"),
            "constructed_cards": ("constructed_card", "local_wiki_full_art_url"),
            "battlegrounds_library_cards": ("library_card", "local_full_art_url"),
            "battlegrounds_timewarped_cards": ("timewarped_card", "art_image_url"),
        }

        def rows(conn, query):
            for table, (_, art_field) in tables.items():
                if f"FROM {table}" not in query:
                    continue
                framed = {
                    "card_id": "FRAMED",
                    "entity_id": "FRAMED",
                    "library": "quest",
                    "dbf": 1,
                    "local_crop_image_url": "/uploads/crop.jpg",
                    "crop_image_url": "https://images.example/crop.jpg",
                    "card_image": "/uploads/card.png",
                    "image_url": "/uploads/card.png",
                    "local_image_url": "/uploads/card.png",
                    "card_image_url": "/uploads/card.png",
                }
                artwork = dict(framed, card_id="ART", entity_id="ART")
                artwork[art_field] = f"/uploads/art/{table}.jpg"
                artwork["wiki_full_art_url"] = "https://images.example/full-art.jpg"
                return [artwork, framed]
            return []

        with mock.patch.object(horizontal, "query_rows", side_effect=rows):
            candidates = horizontal.collect_candidates(
                mock.Mock(), {1: "/uploads/blizzard-crop.jpg"}
            )

        self.assertEqual(len(candidates), 5)
        for candidate in candidates:
            self.assertEqual(candidate.source_kind, "art")
            if (
                candidate.entity_type == "constructed_card"
                and candidate.entity_id == "FRAMED"
            ):
                self.assertEqual(
                    candidate.source_url,
                    "https://art.hearthstonejson.com/v1/tiles/FRAMED.webp",
                )
                continue
            self.assertIn("ART", candidate.entity_id)
            if candidate.entity_type != "constructed_card":
                self.assertTrue(candidate.source_url.startswith("/uploads/art/"))
            self.assertTrue(
                all(kind == "art" for _, kind in candidate.fallback_sources)
            )
            if candidate.entity_type == "constructed_card":
                self.assertEqual(
                    candidate.source_url,
                    "https://art.hearthstonejson.com/v1/tiles/ART.webp",
                )
                self.assertIn(
                    ("/uploads/art/constructed_cards.jpg", "art"),
                    candidate.fallback_sources,
                )
                self.assertIn(
                    ("https://art.hearthstonejson.com/v1/orig/ART.png", "art"),
                    candidate.fallback_sources,
                )

    def test_sync_disables_framed_card_assets_but_keeps_art_and_other_entities(
        self,
    ) -> None:
        with sqlite3.connect(":memory:") as database:
            database.execute(
                "CREATE TABLE horizontal_art_assets (entity_type TEXT, source_kind TEXT, status TEXT, last_error TEXT, recipe_version TEXT)"
            )
            fixtures = [
                ("battleground_card", "crop", "ready"),
                ("constructed_card", "card", "ready"),
                ("library_card", "art", "ready"),
                ("timewarped_card", "crop", "error"),
                ("hero", "card", "ready"),
            ]
            database.executemany(
                "INSERT INTO horizontal_art_assets VALUES (?, ?, ?, NULL, 'old')",
                fixtures,
            )
            conn = mock.MagicMock()
            cursor = conn.cursor.return_value.__enter__.return_value

            def execute(query, params):
                result = database.execute(query.replace("%s", "?"), params)
                cursor.rowcount = result.rowcount

            cursor.execute.side_effect = execute
            self.assertEqual(horizontal.disable_framed_card_assets(conn), 2)
            statuses = database.execute(
                "SELECT status FROM horizontal_art_assets ORDER BY rowid"
            ).fetchall()
            self.assertEqual(
                statuses,
                [
                    ("unavailable",),
                    ("unavailable",),
                    ("ready",),
                    ("error",),
                    ("ready",),
                ],
            )

    def test_dry_run_does_not_disable_existing_assets(self) -> None:
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                conn = mock.MagicMock()
                output = StringIO()
                argv = ["sync_horizontal_art.py", "--workers", "1"]
                if dry_run:
                    argv.append("--dry-run")
                with (
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(horizontal, "load_php_config", return_value={}),
                    mock.patch.object(horizontal, "connect_db", return_value=conn),
                    mock.patch.object(horizontal, "ensure_schema"),
                    mock.patch.object(
                        horizontal, "fetch_blizzard_crop_urls", return_value={}
                    ),
                    mock.patch.object(
                        horizontal, "collect_candidates", return_value=[]
                    ),
                    mock.patch.object(
                        horizontal, "load_existing_assets", return_value={}
                    ),
                    mock.patch.object(
                        horizontal, "disable_framed_card_assets", return_value=2
                    ) as disable,
                    redirect_stdout(output),
                ):
                    self.assertEqual(horizontal.main(), 0)
                if dry_run:
                    disable.assert_not_called()
                    conn.commit.assert_not_called()
                else:
                    disable.assert_called_once_with(conn)
                    conn.commit.assert_called_once_with()
                self.assertEqual(
                    json.loads(output.getvalue())["framed_assets_disabled"],
                    0 if dry_run else 2,
                )

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
                    with Image.open(target) as rendered:
                        self.assertEqual(rendered.mode, "RGBA")

    def test_light_margins_are_removed_on_all_sides_before_focusing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "padded.png"
            target = Path(tmp_dir) / "trimmed.png"
            image = Image.new("RGB", (512, 512), "white")
            ImageDraw.Draw(image).rectangle((70, 8, 441, 503), fill="#164878")
            image.save(source)
            horizontal.trim_light_art_margins(source, target)
            trimmed = Image.open(target).convert("RGB")
            self.assertEqual(trimmed.size, (372, 496))
            self.assertEqual(trimmed.getextrema(), ((22, 22), (72, 72), (120, 120)))

    def test_bright_subject_touching_edge_is_not_treated_as_padding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "subject.png"
            target = Path(tmp_dir) / "trimmed.png"
            image = Image.new("RGB", (256, 64), "#164878")
            ImageDraw.Draw(image).ellipse((200, 8, 280, 56), fill="white")
            image.save(source)
            horizontal.trim_light_art_margins(source, target)
            self.assertEqual(horizontal.identify_size(target), image.size)

    def test_game_strip_keeps_face_at_right_edge_without_recropping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "game-cut.png"
            target = Path(tmp_dir) / "tile.webp"
            image = Image.new("RGB", (256, 59), "#164878")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 27, 58), fill="white")
            draw.rectangle((230, 3, 253, 55), fill="#df9955")
            image.save(source)
            horizontal.render_horizontal_art(source, target, "art")
            rendered = Image.open(target).convert("RGBA")
            face = [
                (x, y)
                for y in range(64)
                for x in range(225, 320)
                if rendered.getpixel((x, y))[0] > 170
                and 90 < rendered.getpixel((x, y))[1] < 190
                and rendered.getpixel((x, y))[2] < 130
            ]
            self.assertGreater(len(face), 1200)
            self.assertGreater(max(x for x, _ in face), 313)
            self.assertGreater(max(y for _, y in face) - min(y for _, y in face), 50)
            self.assertFalse(
                any(
                    min(rendered.getpixel((x, y))[:3]) > 240
                    and rendered.getpixel((x, y))[3] > 10
                    for x in range(320)
                    for y in range(64)
                )
            )

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

    def test_tile_art_blends_into_rarity_background_without_black_padding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "crop.png"
            target = root / "tile.webp"
            Image.new("RGB", (243, 64), "#d08040").save(source)

            horizontal.render_horizontal_art(source, target, "crop")

            with Image.open(target) as image:
                self.assertEqual(image.mode, "RGBA")
                alpha = image.getchannel("A")
                self.assertEqual(alpha.getpixel((0, 32)), 0)
                self.assertEqual(alpha.getpixel((128, 32)), 0)
                self.assertEqual(alpha.getpixel((319, 32)), 255)
                transition = [alpha.getpixel((x, 32)) for x in range(129, 226)]
                self.assertEqual(transition, sorted(transition))
                self.assertGreater(len(set(transition)), 60)
                self.assertLessEqual(max(b - a for a, b in pairwise(transition)), 5)
                for color in ("#666666", "#20405a", "#503961", "#705022"):
                    with self.subTest(background=color):
                        background = Image.new("RGBA", image.size, color)
                        composite = Image.alpha_composite(background, image)
                        self.assertEqual(
                            composite.getpixel((80, 32)),
                            background.getpixel((80, 32)),
                        )
                        self.assertGreater(composite.getpixel((180, 32))[0], 50)

    def test_tile_art_preserves_transparent_source_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "crop.png"
            target = root / "tile.webp"
            image = Image.new("RGBA", (243, 64), "#d08040")
            ImageDraw.Draw(image).rectangle((0, 0, 242, 7), fill=(0, 0, 0, 0))
            image.save(source)

            horizontal.render_horizontal_art(source, target, "crop")

            with Image.open(target) as rendered:
                self.assertEqual(rendered.convert("RGBA").getpixel((319, 2))[3], 0)
                self.assertEqual(rendered.convert("RGBA").getpixel((319, 32))[3], 255)

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
                regenerated = horizontal.process_candidate(
                    candidate,
                    {
                        "status": "ready",
                        "source_signature": generated["source_signature"],
                        "recipe_version": "4-subject-aware-focus",
                    },
                    force=False,
                    dry_run=False,
                )
                self.assertEqual(regenerated["action"], "generated")

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
        loader_start = panel.index("function load_wiki_meta_map(")
        loader_end = panel.index("\n}\n", loader_start)
        loader = panel[loader_start:loader_end]
        self.assertIn("$variants = panel_attach_horizontal_art(", loader)
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
