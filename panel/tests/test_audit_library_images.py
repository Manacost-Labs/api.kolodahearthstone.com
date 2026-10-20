from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_library_images as audit


def row(card_id: str, image: str | None, in_pool: int, library: str = "reward") -> dict:
    return {"library": library, "card_id": card_id, "image_url": image, "in_pool": in_pool,
            "local_full_art_url": None}


class AuditLibraryImagesTest(unittest.TestCase):
    def test_archived_cards_without_images_do_not_fail(self) -> None:
        result, code = audit.audit_rows([row("BG28_Reward_502", None, 0)], lambda url: True)
        self.assertEqual(code, 0)
        self.assertEqual(result["broken_count"], 0)
        self.assertEqual(result["archived_without_image_count"], 1)

    def test_pool_card_without_image_fails(self) -> None:
        _, code = audit.audit_rows([row("BG_LIVE", None, 1)], lambda url: True)
        self.assertEqual(code, 1)

    def test_unreachable_image_fails_even_out_of_pool(self) -> None:
        result, code = audit.audit_rows([row("BG_OLD", "/uploads/x.png", 0)], lambda url: False)
        self.assertEqual(code, 1)
        self.assertEqual(result["broken_count"], 1)

    def test_trinket_full_art_still_checked(self) -> None:
        trinket = row("BG_T", "/uploads/t.png", 1, "trinket")
        _, code = audit.audit_rows([trinket], lambda url: url == "/uploads/t.png")
        self.assertEqual(code, 1)

    def test_transient_failure_is_rechecked_once(self) -> None:
        calls: dict[str, int] = {}

        def flaky(url: str | None) -> bool:
            calls[url] = calls.get(url, 0) + 1
            return calls[url] > 1

        result, code = audit.audit_rows([row("BG_T", "/uploads/t.png", 1)], flaky, recheck_delay=0)
        self.assertEqual((code, result["broken_count"]), (0, 0))
        self.assertEqual(calls["/uploads/t.png"], 2)


if __name__ == "__main__":
    unittest.main()
