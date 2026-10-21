from __future__ import annotations

import json
import random
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_horizontal_art as horizontal

HSJ = "https://art.hearthstonejson.com/v1/"


def textured(size: tuple[int, int], seed: int = 7) -> Image.Image:
    """Painterly noise: smooth color fields with enough detail to match."""
    rng = random.Random(seed)
    small = Image.new("RGB", (max(2, size[0] // 16), max(2, size[1] // 16)))
    small.putdata(
        [
            (rng.randrange(40, 220), rng.randrange(40, 220), rng.randrange(40, 220))
            for _ in range(small.width * small.height)
        ]
    )
    return small.resize(size, Image.Resampling.BICUBIC)


def rows_for(tables: dict[str, list[dict]]):
    def query_rows(conn, query):
        for table, rows in tables.items():
            if f"FROM {table}" in query:
                return rows
        return []

    return query_rows


class CandidateTest(unittest.TestCase):
    def collect(self, tables: dict[str, list[dict]]) -> list[horizontal.Candidate]:
        with mock.patch.object(horizontal, "query_rows", side_effect=rows_for(tables)):
            return horizontal.collect_candidates(mock.Mock())

    def test_revealed_cards_use_only_trusted_official_illustration_strips(self) -> None:
        official = "https://d15f34w2p8l1cc.cloudfront.net/hearthstone/illustration.png"
        candidates = self.collect(
            {
                "constructed_cards": [
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
                            {"official_reveal_en": {"cropImage": "https://x.example/a.png"}}
                        ),
                    },
                    {"card_id": "blizzard:3", "source_payload_json": "invalid"},
                ]
            }
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            (candidates[0].source_url, candidates[0].source_kind), (official, "strip")
        )
        self.assertEqual(candidates[0].fallback_sources, ())

    def test_constructed_prefers_local_full_art_then_game_texture_then_tile(
        self,
    ) -> None:
        candidates = self.collect(
            {
                "constructed_cards": [
                    {
                        "card_id": "CORE_RLK_079",
                        "dbf": 1,
                        "local_wiki_full_art_url": "/uploads/full/CORE_RLK_079.jpg",
                        "wiki_full_art_url": "https://wiki.example/full.jpg",
                    }
                ]
            }
        )
        candidate = candidates[0]
        self.assertEqual(
            [(candidate.source_url, candidate.source_kind), *candidate.fallback_sources],
            [
                ("/uploads/full/CORE_RLK_079.jpg", "art"),
                (HSJ + "orig/CORE_RLK_079.png", "art"),
                (HSJ + "orig/RLK_079.png", "art"),
                ("https://wiki.example/full.jpg", "art"),
                (HSJ + "tiles/CORE_RLK_079.webp", "tile"),
                (HSJ + "tiles/RLK_079.webp", "tile"),
            ],
        )
        self.assertEqual(
            candidate.focus_tiles,
            (HSJ + "tiles/CORE_RLK_079.webp", HSJ + "tiles/RLK_079.webp"),
        )

    def test_rendered_cards_and_crops_are_never_sources(self) -> None:
        framed = {
            "dbf": 1,
            "static_image_url": "/uploads/hero-skins/static/HERO_01af.png",
            "card_image_url": "/uploads/pets/cards/PET_8_1.png",
            "hero_image_url": "https://wiki.example/BG20_HERO_100.png",
            "crop_image_url": "https://images.example/crop.png",
            "wiki_image_url": "https://wiki.example/thumb/AT_COIN.png",
            "image_url": "https://images.example/render/AT_COIN.png",
        }
        candidates = self.collect(
            {
                "hero_skins": [
                    dict(framed, card_id="HERO_01af", game_id="HERO_01af", full_art_url=None)
                ],
                "battlegrounds_heroes": [
                    dict(
                        framed,
                        card_id="BG20_HERO_100",
                        game_id="BG20_HERO_100",
                        hero_full_art_url="https://wiki.example/full.jpg",
                    )
                ],
                "hearthstone_coins": [dict(framed, card_id="AT_COIN", game_id="AT_COIN")],
                "hearthstone_pets": [
                    dict(framed, card_id="PET_8_1", variant_id=22),
                    dict(
                        framed,
                        card_id="PET_3_1",
                        variant_id=5,
                        end_screen_background_url="https://wiki.example/bg.png",
                    ),
                ],
                "battlegrounds_library_cards": [
                    dict(
                        framed,
                        library="quest",
                        card_id="BG24_Quest_314",
                        game_id="BG24_Quest_314",
                    )
                ],
            }
        )
        sources = {
            (item.entity_type, item.entity_id): [
                (item.source_url, item.source_kind),
                *item.fallback_sources,
            ]
            for item in candidates
        }
        self.assertEqual(
            sources,
            {
                ("hero_skin", "HERO_01af"): [(HSJ + "orig/HERO_01af.png", "art")],
                # Remote wiki art can be a panorama: the game portrait comes first.
                ("hero", "BG20_HERO_100"): [
                    (HSJ + "orig/BG20_HERO_100.png", "art"),
                    ("https://wiki.example/full.jpg", "art"),
                ],
                ("coin", "AT_COIN"): [(HSJ + "orig/AT_COIN.png", "art")],
                ("pet", "PET_3_1"): [("https://wiki.example/bg.png", "art")],
                ("library_card", "quest:BG24_Quest_314"): [
                    (HSJ + "orig/BG24_Quest_314.png", "art")
                ],
            },
        )
        for item in candidates:
            self.assertEqual(item.focus_tiles, ())

    def test_local_art_precedes_the_game_texture(self) -> None:
        candidates = self.collect(
            {
                "battlegrounds_cards": [
                    {"card_id": "BG1", "game_id": "BG1", "dbf": 1, "art_image": "/uploads/art/BG1.jpg"}
                ]
            }
        )
        self.assertEqual(candidates[0].source_url, "/uploads/art/BG1.jpg")
        self.assertEqual(
            candidates[0].fallback_sources, ((HSJ + "orig/BG1.png", "art"),)
        )


class SyncTest(unittest.TestCase):
    def test_sync_disables_framed_and_cropped_assets_of_every_entity(self) -> None:
        with sqlite3.connect(":memory:") as database:
            database.execute(
                "CREATE TABLE horizontal_art_assets (entity_type TEXT, source_kind TEXT, status TEXT, last_error TEXT, recipe_version TEXT)"
            )
            fixtures = [
                ("hero_skin", "card", "ready"),
                ("hero", "crop", "ready"),
                ("library_card", "art", "ready"),
                ("constructed_card", "strip", "ready"),
                ("timewarped_card", "crop", "error"),
                ("pet", "card", "ready"),
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
            self.assertEqual(horizontal.disable_framed_card_assets(conn), 3)
            statuses = database.execute(
                "SELECT status FROM horizontal_art_assets ORDER BY rowid"
            ).fetchall()
            self.assertEqual(
                [status for (status,) in statuses],
                ["unavailable", "unavailable", "ready", "ready", "error", "unavailable"],
            )

    def run_main(self, argv: list[str], candidates=(), existing=None):
        conn = mock.MagicMock()
        output = StringIO()
        with (
            mock.patch.object(sys, "argv", ["sync_horizontal_art.py", *argv]),
            mock.patch.object(horizontal, "load_php_config", return_value={}),
            mock.patch.object(horizontal, "connect_db", return_value=conn),
            mock.patch.object(horizontal, "ensure_schema"),
            mock.patch.object(horizontal, "load_focus_overrides", return_value={}),
            mock.patch.object(
                horizontal, "collect_candidates", return_value=list(candidates)
            ),
            mock.patch.object(
                horizontal, "load_existing_assets", return_value=existing or {}
            ),
            mock.patch.object(
                horizontal,
                "process_candidate",
                side_effect=lambda candidate, *_, **__: {
                    "action": "error",
                    "candidate": candidate,
                    "error": "x",
                },
            ) as process,
            mock.patch.object(horizontal, "persist_result"),
            mock.patch.object(
                horizontal, "disable_framed_card_assets", return_value=2
            ) as disable,
            redirect_stdout(output),
        ):
            code = horizontal.main()
        return code, json.loads(output.getvalue()), conn, disable, process

    def test_dry_run_does_not_disable_existing_assets(self) -> None:
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                code, summary, conn, disable, _ = self.run_main(
                    ["--workers", "1", *(["--dry-run"] if dry_run else [])]
                )
                self.assertEqual(code, 0)
                if dry_run:
                    disable.assert_not_called()
                    conn.commit.assert_not_called()
                else:
                    disable.assert_called_once_with(conn)
                    conn.commit.assert_called_once_with()
                self.assertEqual(summary["framed_assets_disabled"], 0 if dry_run else 2)

    def test_bounded_runs_defer_work_and_keep_legacy_assets(self) -> None:
        candidates = [
            horizontal.Candidate("hero", f"H{index}", None, "/uploads/a.png", "art")
            for index in range(5)
        ]
        code, summary, _, disable, process = self.run_main(
            ["--max-generate", "2"], candidates
        )
        self.assertEqual(code, 1)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(summary["deferred"], 3)
        # Legacy rows stay visible until a complete run replaced what it can.
        disable.assert_not_called()
        _, summary, _, disable, process = self.run_main(
            ["--entity", "coin", "--max-generate", "0"], candidates
        )
        self.assertEqual(process.call_count, 0)
        disable.assert_not_called()


class RenderTest(unittest.TestCase):
    def test_white_and_gray_texture_padding_is_removed(self) -> None:
        # New cards: portrait art padded with white columns to a square texture.
        canvas = Image.new("RGBA", (512, 512), "white")
        canvas.paste(textured((300, 512)), (106, 0))
        trimmed = horizontal.trim_padding(canvas)
        self.assertLessEqual(abs(trimmed.width - 300), 8)
        self.assertGreaterEqual(trimmed.height, 500)
        # Older textures: light gray packing bars across most of the bottom rows.
        canvas = textured((512, 512)).convert("RGBA")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 470, 200, 511), fill="#c8c8c8")
        draw.rectangle((330, 470, 511, 511), fill="#c8c8c8")
        trimmed = horizontal.trim_padding(canvas)
        self.assertLessEqual(abs(trimmed.height - 470), 8)
        self.assertGreaterEqual(trimmed.width, 500)

    def test_bright_subject_touching_edge_is_not_treated_as_padding(self) -> None:
        image = textured((256, 256)).convert("RGBA")
        ImageDraw.Draw(image).ellipse((180, 60, 300, 180), fill="#fff4d0")
        self.assertGreater(horizontal.trim_padding(image).width, 248)

    def test_game_tile_locates_stretched_subject_in_full_art(self) -> None:
        art = textured((500, 640), seed=3)
        left, top, right, bottom = 60, 150, 460, 270
        # Game tiles are a horizontally stretched view of the illustration.
        tile = art.crop((left, top, right, bottom)).resize((256, 59))
        ImageDraw.Draw(tile).rectangle((10, 0, 55, 58), fill="white")
        x, y, width, height, score = horizontal.locate_game_tile(
            art.convert("RGBA"), tile.convert("RGBA")
        )
        self.assertGreater(score, 0.9)
        self.assertAlmostEqual(x + width / 2, (left + right) / 2 / 500, delta=0.04)
        self.assertAlmostEqual(y + height / 2, (top + bottom) / 2 / 640, delta=0.04)

    def test_subject_focus_prefers_face_over_detailed_lower_hands(self) -> None:
        image = Image.new("RGB", (600, 800), "#253443")
        draw = ImageDraw.Draw(image)
        draw.ellipse((390, 190, 510, 310), fill="#d99b75")
        draw.ellipse((420, 235, 435, 250), fill="#ffffff")
        draw.ellipse((465, 235, 480, 250), fill="#ffffff")
        for offset in range(0, 200, 12):
            draw.line((80, 560 + offset // 4, 280, 760 - offset), fill="#f0e090", width=3)
        focus_x, focus_y = horizontal.subject_focus(image)
        self.assertAlmostEqual(focus_y, 250 / 800, delta=0.07)
        self.assertGreater(focus_x, 0.45)

    def test_crop_keeps_aspect_and_subject_out_of_the_text_fade(self) -> None:
        for size, focus in (((400, 512), (0.5, 0.3)), ((2000, 400), (0.8, 0.5))):
            with self.subTest(size=size):
                left, top, width, height = horizontal.plan_crop(*size, *focus)
                self.assertAlmostEqual(width / height, horizontal.ART_ASPECT, places=6)
                self.assertTrue(0 <= left and left + width <= size[0] + 1e-6)
                self.assertTrue(0 <= top and top + height <= size[1] + 1e-6)
                self.assertTrue(top <= focus[1] * size[1] <= top + height)
        left, _, width, _ = horizontal.plan_crop(400, 512, 0.3, 0.3)
        self.assertGreaterEqual((0.3 * 400 - left) / width, 0.45)

    def test_render_writes_sharp_2x_and_compatible_1x_renditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source.png"
            textured((1200, 1600)).save(source)
            target = Path(tmp_dir) / "out" / "CARD.webp"
            report = horizontal.render_horizontal_art(source, target, "art")
            retina = horizontal.retina_path(target)
            self.assertEqual(retina.name, "CARD@2x.webp")
            with Image.open(target) as small, Image.open(retina) as large:
                self.assertEqual(small.size, (320, 64))
                self.assertEqual(large.size, (640, 128))
                alpha = np.asarray(large.convert("RGBA"))[..., 3]
            self.assertEqual(int(alpha[:, :150].max()), 0)
            self.assertGreaterEqual(int(alpha[:, 400:].min()), 250)
            self.assertEqual(report["focus_method"], "subject")
            self.assertEqual(report["white_columns"], 0)

    def test_quality_check_rejects_artifacts(self) -> None:
        image = Image.new("RGBA", (640, 128), "#406080")
        ImageDraw.Draw(image).rectangle((420, 0, 639, 127), fill="white")
        with self.assertRaisesRegex(RuntimeError, "white margin"):
            horizontal.check_quality(horizontal.quality_report(image))
        flat = Image.new("RGBA", (640, 128), "#406080")
        with self.assertRaisesRegex(RuntimeError, "blank"):
            horizontal.check_quality(horizontal.quality_report(flat))
        with self.assertRaises(ValueError):
            horizontal.render_horizontal_art(Path("x"), Path("y.webp"), "card")


class ProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.uploads = self.root / "uploads"
        (self.uploads / "art").mkdir(parents=True)
        self.patches = [
            mock.patch.object(horizontal, "APP_ROOT", self.root),
            mock.patch.object(horizontal, "UPLOAD_ROOT", self.uploads),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def test_new_recipe_writes_beside_the_previous_files(self) -> None:
        textured((900, 1200)).save(self.uploads / "art" / "TEST_CARD.jpg")
        old = self.uploads / "horizontal-art" / "battleground_card" / "TEST_CARD.webp"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"recipe 7")
        candidate = horizontal.Candidate(
            "battleground_card", "TEST_CARD", 1, "/uploads/art/TEST_CARD.jpg", "art"
        )
        generated = horizontal.process_candidate(
            candidate, None, force=False, dry_run=False
        )
        self.assertEqual(generated["action"], "generated")
        self.assertEqual(
            generated["public_path"],
            "/uploads/horizontal-art/r8/battleground_card/TEST_CARD.webp",
        )
        self.assertEqual(old.read_bytes(), b"recipe 7")
        current = {
            "status": "ready",
            "source_signature": generated["source_signature"],
            "recipe_version": horizontal.RECIPE_VERSION,
            "local_image_url": generated["public_path"],
        }
        unchanged = horizontal.process_candidate(
            candidate, current, force=False, dry_run=False
        )
        self.assertEqual(unchanged["action"], "unchanged")
        self.assertTrue(horizontal.is_current(candidate, current, None))
        # A manual focus override is part of the signature.
        overridden = horizontal.process_candidate(
            candidate,
            current,
            force=False,
            dry_run=False,
            focus_overrides={"battleground_card:TEST_CARD": (0.5, 0.2)},
        )
        self.assertEqual(overridden["action"], "generated")
        self.assertEqual(overridden["report"]["focus_method"], "override")
        legacy = dict(current, recipe_version="7-game-cuts-trimmed-art")
        self.assertFalse(horizontal.is_current(candidate, legacy, None))

    def test_failed_source_falls_back_and_missing_art_is_unavailable(self) -> None:
        textured((900, 1200)).save(self.uploads / "art" / "fallback.jpg")
        Image.new("RGB", (900, 1200), "white").save(self.uploads / "art" / "blank.jpg")
        candidate = horizontal.Candidate(
            "battleground_card",
            "TEST",
            None,
            "/uploads/art/blank.jpg",
            "art",
            (("/uploads/art/fallback.jpg", "art"),),
        )
        result = horizontal.process_candidate(candidate, None, force=False, dry_run=False)
        self.assertEqual(result["action"], "generated")
        self.assertEqual(result["source_url"], "/uploads/art/fallback.jpg")

        missing = horizontal.Candidate(
            "battleground_card", "MISSING", None, "https://images.example/x.png", "art"
        )
        error = urllib.error.HTTPError(missing.source_url, 404, "Not Found", None, None)
        with mock.patch.object(horizontal, "download_source", side_effect=error):
            result = horizontal.process_candidate(missing, None, force=False, dry_run=False)
        self.assertEqual(result["action"], "unavailable")

    def test_constructed_tile_sets_focus(self) -> None:
        art = textured((500, 640), seed=5)
        art.save(self.uploads / "art" / "C.png")
        art.crop((40, 380, 460, 480)).resize((256, 59)).save(self.uploads / "art" / "tile.webp")
        candidate = horizontal.Candidate(
            "constructed_card",
            "C",
            None,
            "/uploads/art/C.png",
            "art",
            focus_tiles=("/uploads/art/tile.webp",),
        )
        result = horizontal.process_candidate(candidate, None, force=False, dry_run=False)
        self.assertEqual(result["report"]["focus_method"], "game_tile")
        self.assertAlmostEqual(result["report"]["focus"][1], 430 / 640, delta=0.04)


class WiringTest(unittest.TestCase):
    def test_output_filename_is_stable_and_collision_safe(self) -> None:
        self.assertEqual(horizontal.output_filename("BG_001"), "BG_001.webp")
        self.assertRegex(
            horizontal.output_filename("quest:BG24_Quest_314"),
            r"^quest_BG24_Quest_314-[0-9a-f]{10}\.webp$",
        )

    def test_focus_overrides_file_is_valid(self) -> None:
        overrides = horizontal.load_focus_overrides()
        for key in overrides:
            entity_type, _, entity_id = key.partition(":")
            self.assertIn(entity_type, horizontal.ENTITY_TYPES)
            self.assertTrue(entity_id)

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
        self.assertEqual(api.count("'horizontal' =>"), api.count("'horizontal_2x' =>"))
        for entity_type in horizontal.ENTITY_TYPES:
            self.assertIn(f"'{entity_type}'", api)

    def test_panel_data_attaches_horizontal_art_for_supported_entities(self) -> None:
        panel = (ROOT / "lib" / "panel_data.php").read_text(encoding="utf-8")
        self.assertIn("function panel_attach_horizontal_art(", panel)
        loader_start = panel.index("function load_wiki_meta_map(")
        loader_end = panel.index("\n}\n", loader_start)
        self.assertIn("$variants = panel_attach_horizontal_art(", panel[loader_start:loader_end])
        for entity_type in horizontal.ENTITY_TYPES:
            self.assertIn(f"'{entity_type}'", panel)


if __name__ == "__main__":
    unittest.main()
