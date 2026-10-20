from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_battlegrounds_framed.py"
SPEC = importlib.util.spec_from_file_location("audit_battlegrounds_framed", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class BattlegroundsFramedAuditTest(unittest.TestCase):
    def test_accepts_media_from_unified_api_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "uploads" / "cards" / "BG_TEST.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + b"\x00\x00\x00\x0dIHDR"
                + struct.pack(">II", 256, 388)
            )
            original_root = audit.APP_ROOT
            audit.APP_ROOT = root
            try:
                result = audit.validate_png_asset(
                    "BG_TEST",
                    "card",
                    "https://api.kolodahearthstone.com/uploads/cards/BG_TEST.png?v=1",
                    "cards",
                    (256, 388),
                )
            finally:
                audit.APP_ROOT = original_root

        self.assertIsNone(result)

    def test_rejects_media_from_retired_host(self) -> None:
        result = audit.validate_png_asset(
            "BG_TEST",
            "card",
            "https://db." + "kolodahs.ru/uploads/cards/BG_TEST.png",
            "cards",
            (256, 388),
        )

        self.assertIn("non-local", result or "")

    def golden(self, url: str, size: tuple[int, int] = (512, 776)) -> str | None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "uploads" / "golden" / ("BGS_004-" + "a" * 64 + ".png")
            image.parent.mkdir(parents=True)
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", *size))
            original_root = audit.APP_ROOT
            audit.APP_ROOT = root
            try:
                return audit.validate_png_asset("TB_BaconUps_079", "card", url, "cards", (256, 388))
            finally:
                audit.APP_ROOT = original_root

    def test_accepts_content_addressed_golden_render(self) -> None:
        url = "https://api.kolodahearthstone.com/uploads/golden/BGS_004-" + "a" * 64 + ".png?v=2026-10-20%2021%3A37%3A28"
        self.assertIsNone(self.golden(url))

    def test_checks_golden_render_geometry(self) -> None:
        url = "https://api.kolodahearthstone.com/uploads/golden/BGS_004-" + "a" * 64 + ".png"
        self.assertIn("geometry", self.golden(url, (256, 388)) or "")

    def test_rejects_unversioned_or_foreign_golden_paths(self) -> None:
        for url in (
            "https://api.kolodahearthstone.com/uploads/golden/BGS_004.png",
            "https://api.kolodahearthstone.com/uploads/golden/../cards/BGS_004-" + "a" * 64 + ".png",
            "https://cards.hearthpwn.com/uploads/golden/BGS_004-" + "a" * 64 + ".png",
        ):
            with self.subTest(url=url):
                self.assertIn("non-local", self.golden(url) or "")


if __name__ == "__main__":
    unittest.main()
