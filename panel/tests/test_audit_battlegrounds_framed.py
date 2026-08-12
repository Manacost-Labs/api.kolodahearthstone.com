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


if __name__ == "__main__":
    unittest.main()
