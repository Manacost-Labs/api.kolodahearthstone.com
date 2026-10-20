import hashlib
import io
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import archive_card_history_media as media


def image_bytes():
    output = io.BytesIO()
    Image.new("RGBA", (8, 12), (30, 50, 80, 255)).save(output, format="PNG")
    return output.getvalue()


class ImageArchiveTests(unittest.TestCase):
    def test_local_bytes_are_immutable_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uploads").mkdir()
            content = image_bytes()
            (root / "uploads/current.png").write_bytes(content)
            original = media.capture("/uploads/current.png", True, root)
            repeated = media.capture("/uploads/current.png", True, root)
            self.assertEqual(original, repeated)
            self.assertEqual(original["status"], "archived")
            self.assertEqual(original["sha256"], hashlib.sha256(content).hexdigest())
            (root / "uploads/current.png").write_bytes(b"changed")
            self.assertEqual(
                (root / original["archive_url"].lstrip("/")).read_bytes(), content
            )
            self.assertEqual(
                len(list((root / "uploads/card-history-media").glob("*.png"))), 1
            )

    def test_old_mutable_url_is_never_downloaded_as_historical_image(self):
        with patch.object(
            media, "fetch_bytes", side_effect=AssertionError("old image fetched")
        ):
            self.assertEqual(
                media.capture("/uploads/old.png", False, Path("/unread")),
                {"status": "unverified", "reason": "historical_mutable_url"},
            )

    def test_old_content_addressed_source_requires_matching_bytes(self):
        content = image_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uploads").mkdir()
            source = root / ("uploads/A-" + digest + ".png")
            source.write_bytes(content)
            self.assertEqual(
                media.capture("/uploads/" + source.name, False, root)["evidence"],
                "immutable_source_hash",
            )
            bad = root / ("uploads/A-" + "f" * 64 + ".png")
            bad.write_bytes(content)
            self.assertEqual(
                media.capture("/uploads/" + bad.name, False, root)["reason"],
                "source_digest_mismatch",
            )

    def test_invalid_images_and_missing_sources_are_bounded_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uploads").mkdir()
            (root / "uploads/bad.png").write_text("<html>error</html>")
            self.assertEqual(
                media.capture("/uploads/bad.png", True, root)["reason"], "invalid_image"
            )
            self.assertEqual(
                media.capture("/uploads/missing.png", True, root)["reason"],
                "image_unavailable",
            )

    def test_paths_redirects_and_authenticated_urls_cannot_escape(self):
        bad_urls = [
            "file:///etc/passwd",
            "http://127.0.0.1/image.png",
            "https://user:password@art.hearthstonejson.com/a.png",
            "https://untrusted.example/a.png",
            "/uploads/%2e%2e/%2e%2e/etc/passwd",
            "//untrusted.example/a.png",
        ]
        with tempfile.TemporaryDirectory() as directory:
            for url in bad_urls:
                with self.subTest(url=url), self.assertRaises(media.ArchiveError):
                    media.local_file(url, Path(directory))
            with self.assertRaises(media.ArchiveError):
                media.PublicRedirects().redirect_request(
                    None, None, 302, "", {}, "https://127.0.0.1/image.png"
                )

    def test_remote_capture_requires_explicit_opt_in(self):
        with patch.object(
            media.urllib.request,
            "build_opener",
            side_effect=AssertionError("unexpected network"),
        ):
            result = media.capture(
                "https://art.hearthstonejson.com/a.png", True, Path("/unread")
            )
            self.assertEqual(
                result, {"status": "unverified", "reason": "remote_capture_disabled"}
            )

    def test_local_symlink_outside_upload_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uploads").mkdir()
            (root / "uploads/escape.png").symlink_to(root / "private.png")
            with self.assertRaises(media.ArchiveError):
                media.local_file("/uploads/escape.png", root)

    @unittest.skipUnless(
        os.getenv("RUN_CARD_HISTORY_MYSQL_TESTS") == "1",
        "isolated local MySQL integration is opt-in",
    )
    def test_version_guard_archive_records_and_idempotent_retries(self):
        import pymysql
        from card_history_schema import DDL
        from pymysql.cursors import DictCursor

        database = "test_history_media_" + uuid.uuid4().hex
        password = secrets.token_hex(32)

        def admin(sql):
            subprocess.run(
                ["mysql", "--batch"],
                input=sql,
                text=True,
                check=True,
                capture_output=True,
            )

        admin(
            f"CREATE DATABASE `{database}`; CREATE USER '{database}'@'localhost' IDENTIFIED BY '{password}'; GRANT ALL ON `{database}`.* TO '{database}'@'localhost';"
        )
        connection = None
        try:
            connection = pymysql.connect(
                unix_socket="/run/mysqld/mysqld.sock",
                user=database,
                password=password,
                database=database,
                cursorclass=DictCursor,
            )
            with connection.cursor() as cursor:
                cursor.execute(DDL)
                cursor.execute(media.DDL)
                for kind in ("baseline", "changed"):
                    cursor.execute(
                        "INSERT INTO catalog_card_versions (entity,card_id,observed_at,event_kind,snapshot_hash,snapshot_json) VALUES ('card','A',NOW(),%s,%s,%s)",
                        (kind, "a" * 64, '{"card_image":"/uploads/a.png"}'),
                    )
            connection.commit()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "uploads").mkdir()
                (root / "uploads/a.png").write_bytes(image_bytes())
                first = media.archive_batch(connection, root, apply=True)
                self.assertEqual((first["archived"], first["unverified"]), (1, 1))
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT status, archive_url, sha256 FROM catalog_card_version_media WHERE version_id=2 AND field_name='card_image'"
                    )
                    original = cursor.fetchone()
                    cursor.execute("SELECT COUNT(*) AS n FROM catalog_card_versions")
                    self.assertEqual(cursor.fetchone()["n"], 2)
                connection.rollback()
                (root / "uploads/a.png").write_bytes(b"changed")
                repeated = media.archive_batch(connection, root, apply=True)
                self.assertEqual(repeated["already_archived"], 1)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT status, archive_url, sha256 FROM catalog_card_version_media WHERE version_id=2 AND field_name='card_image'"
                    )
                    self.assertEqual(cursor.fetchone(), original)
                    cursor.execute(
                        "INSERT INTO catalog_card_versions (entity,card_id,observed_at,event_kind,snapshot_hash,snapshot_json) VALUES ('card','B',NOW(),'new',%s,%s)",
                        ("b" * 64, '{"card_image":"/uploads/b.png"}'),
                    )
                connection.commit()

                def changed_while_fetching(*args):
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO catalog_card_versions (entity,card_id,observed_at,event_kind,snapshot_hash,snapshot_json) VALUES ('card','B',NOW(),'changed',%s,%s)",
                            ("c" * 64, '{"card_image":"/uploads/b.png"}'),
                        )
                    connection.commit()
                    return {
                        "status": "archived",
                        "archive_url": "/uploads/card-history-media/a.png",
                        "sha256": "a" * 64,
                    }

                with patch.object(media, "capture", side_effect=changed_while_fetching):
                    race = media.archive_batch(
                        connection, root, after=2, limit=1, apply=True
                    )
                self.assertEqual(race["unverified"], 1)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT reason FROM catalog_card_version_media WHERE version_id=3 AND field_name='card_image'"
                    )
                    self.assertEqual(
                        cursor.fetchone()["reason"], "version_changed_during_capture"
                    )
        finally:
            if connection:
                connection.close()
            admin(
                f"DROP DATABASE IF EXISTS `{database}`; DROP USER IF EXISTS '{database}'@'localhost';"
            )


if __name__ == "__main__":
    unittest.main()
