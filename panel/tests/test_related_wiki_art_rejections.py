from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_constructed_related_wiki_art as wiki_art


class RejectionTest(unittest.TestCase):
    def test_truncated_payload_is_rejected_not_written(self) -> None:
        info = {"url": "https://hearthstone.wiki.gg/images/x.png", "size": 902604, "mime": "image/png"}
        with tempfile.TemporaryDirectory() as tmp_dir:
            upload = Path(tmp_dir)
            with (
                mock.patch.object(wiki_art, "UPLOAD_DIR", upload),
                mock.patch.object(wiki_art, "scrape_do_fetch", return_value=b"\x89PNG" + b"0" * 190165),
                self.assertRaises(wiki_art.PayloadRejected),
            ):
                wiki_art.download_original({"card_id": "CFM_649"}, info, False, False)
            self.assertEqual(list(upload.iterdir()), [])

    def test_isolated_rejections_do_not_fail_the_job(self) -> None:
        stats = {"errors": [], "rejected": [{"card_id": "CFM_649"}]}
        self.assertEqual(wiki_art.exit_code(stats), 0)

    def test_widespread_rejections_fail_the_job(self) -> None:
        stats = {"errors": [], "rejected": [{"card_id": str(i)} for i in range(wiki_art.MAX_REJECTED + 1)]}
        self.assertEqual(wiki_art.exit_code(stats), 1)

    def test_other_errors_still_fail_the_job(self) -> None:
        self.assertEqual(wiki_art.exit_code({"errors": [{"card_id": "X"}], "rejected": []}), 1)


if __name__ == "__main__":
    unittest.main()
