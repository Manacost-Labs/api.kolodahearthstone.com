import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import card_image_versions as versions


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PublishImageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.uploads = self.root / "uploads"
        (self.uploads / "cards").mkdir(parents=True)

    def candidate(self, data: bytes) -> Path:
        path = self.root / "candidate.png"
        path.write_bytes(data)
        return path

    def test_same_hash_returns_current_file(self):
        current = self.uploads / "cards" / "A.png"
        current.write_bytes(b"render")
        published, digest = versions.publish_image(self.candidate(b"render"), current, self.uploads)
        self.assertEqual(published, current)
        self.assertEqual(digest, sha(b"render"))
        self.assertFalse((self.uploads / "card-history-media").exists())

    def test_changed_hash_publishes_content_addressed_file_and_keeps_old(self):
        current = self.uploads / "cards" / "A.png"
        current.write_bytes(b"old")
        published, digest = versions.publish_image(self.candidate(b"new"), current, self.uploads)
        self.assertEqual(digest, sha(b"new"))
        self.assertEqual(published, self.uploads / "card-history-media" / (digest + ".png"))
        self.assertEqual(published.read_bytes(), b"new")
        self.assertEqual(current.read_bytes(), b"old")
        self.assertEqual(published.stat().st_mode & 0o777, 0o644)

    def test_missing_current_file_publishes_new_one(self):
        published, digest = versions.publish_image(
            self.candidate(b"new"), self.uploads / "cards" / "missing.png", self.uploads
        )
        self.assertEqual(published.name, digest + ".png")

    def test_republishing_same_content_is_idempotent(self):
        current = self.uploads / "cards" / "A.png"
        current.write_bytes(b"old")
        first, _ = versions.publish_image(self.candidate(b"new"), current, self.uploads)
        second, _ = versions.publish_image(self.candidate(b"new"), current, self.uploads)
        self.assertEqual(first, second)

    def test_hash_conflict_with_existing_target_is_an_error(self):
        digest = sha(b"new")
        target = self.uploads / "card-history-media" / (digest + ".png")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            versions.publish_image(self.candidate(b"new"), self.uploads / "cards" / "A.png", self.uploads)


class CurrentImagePathTest(unittest.TestCase):
    def setUp(self):
        self.uploads = Path("/srv/app/uploads")
        self.fallback = Path("/srv/app/uploads/cards/fallback.png")

    def resolve(self, url):
        return versions.current_image_path(url, self.uploads, self.fallback)

    def test_local_upload_url_maps_into_upload_dir(self):
        for url in ("/uploads/cards/A.png", "https://api.kolodahearthstone.com/uploads/cards/A.png"):
            self.assertEqual(self.resolve(url), self.uploads / "cards" / "A.png")

    def test_percent_encoded_name_is_decoded(self):
        self.assertEqual(self.resolve("/uploads/cards/A%20B.png"), self.uploads / "cards" / "A B.png")

    def test_foreign_host_or_non_upload_path_falls_back(self):
        for url in (
            "https://example.com/uploads/cards/A.png",
            "/images/A.png",
            "",
            None,
        ):
            self.assertEqual(self.resolve(url), self.fallback)

    def test_parent_segments_are_rejected(self):
        for url in ("/uploads/../etc/passwd", "/uploads/cards/%2e%2e/x.png"):
            with self.assertRaises(ValueError):
                self.resolve(url)


if __name__ == "__main__":
    unittest.main()
