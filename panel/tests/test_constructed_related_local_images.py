"""Issue #100: the related-cards sync must not fight the image mirror for local_*."""
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

MEDIA = "/uploads/card-history-media/" + "a" * 64 + ".png"
RELATED = "/uploads/constructed-related/BE_036.png"
GOLD = "/uploads/card-history-media/" + "b" * 64 + ".png"

COLUMNS = """card_id VARCHAR(191) PRIMARY KEY, dbf INT, collectible INT, name_ru TEXT, name_en TEXT,
 text_ru TEXT, text_en TEXT, flavor_ru TEXT, flavor_en TEXT, card_set VARCHAR(80), card_type VARCHAR(40),
 rarity VARCHAR(40), class_slug VARCHAR(40), multi_class_json LONGTEXT, minion_type VARCHAR(40),
 spell_school VARCHAR(40), mana_cost INT, attack INT, health INT, durability INT, armor INT,
 artist VARCHAR(191), image_url TEXT, local_image_url TEXT, local_gold_image_url TEXT,
 crop_image_url TEXT, local_crop_image_url TEXT, mechanics_json LONGTEXT, referenced_tags_json LONGTEXT,
 wiki_page_title VARCHAR(191), source VARCHAR(80), source_payload_json LONGTEXT, source_hash VARCHAR(64),
 first_seen_at DATETIME, last_seen_at DATETIME, changed_at DATETIME, updated_at DATETIME"""


def docker_ready():
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "image", "inspect", "mariadb:10.11.18"], capture_output=True).returncode == 0


@unittest.skipUnless(os.getenv("RUN_CARD_HISTORY_DOCKER_TESTS") == "1" and docker_ready(),
                     "throwaway MariaDB 10.11 in docker is opt-in")
class RelatedSyncLocalImagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pymysql
        from pymysql.cursors import DictCursor
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.name = "hs-history-test-" + uuid.uuid4().hex[:8]
        subprocess.run(["docker", "run", "-d", "--rm", "--name", cls.name, "-e",
                        "MARIADB_ROOT_PASSWORD=test", "-e", "MARIADB_DATABASE=generator",
                        "-p", "127.0.0.1:%d:3306" % port, "mariadb:10.11.18"],
                       check=True, capture_output=True)
        deadline = time.time() + 90
        while True:
            try:
                cls.conn = pymysql.connect(host="127.0.0.1", port=port, user="root", password="test",
                                           database="generator", cursorclass=DictCursor, autocommit=True)
                break
            except pymysql.err.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(2)
        with cls.conn.cursor() as cursor:
            cursor.execute("CREATE TABLE constructed_cards (%s) ENGINE=InnoDB" % COLUMNS)
        from install_card_history import install
        install(cls.conn, True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        subprocess.run(["docker", "rm", "-f", cls.name], capture_output=True)

    def setUp(self):
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM constructed_cards")
            cursor.execute("DELETE FROM catalog_card_versions")

    def related_sync(self, **override):
        import sync_constructed_related_cards as sync
        ru = {"dbfId": 1, "name": "Тест", "text": "Текст", "cost": 2, "type": "MINION", "set": "X"}
        ru.update(override)
        card = sync.normalize("BE_036", ru, None, "https://cdn/x.png", RELATED, None, None)
        sync.save_card(self.conn, card, False)

    def mirror_publish(self, column, url):
        # The same statement mirror_constructed_images.mirror() issues.
        with self.conn.cursor() as cursor:
            cursor.execute("UPDATE constructed_cards SET %s = %%s, updated_at = NOW() WHERE card_id = 'BE_036'"
                           % column, (url,))

    def versions(self):
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM catalog_card_versions WHERE card_id = 'BE_036'")
            return cursor.fetchone()["n"]

    def local_image(self):
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT local_image_url AS u FROM constructed_cards WHERE card_id = 'BE_036'")
            return cursor.fetchone()["u"]

    def test_repeated_syncs_of_unchanged_card_add_no_versions(self):
        self.related_sync()
        self.mirror_publish("local_image_url", MEDIA)
        self.mirror_publish("local_gold_image_url", GOLD)
        settled = self.versions()
        for _ in range(3):
            self.related_sync()
            self.mirror_publish("local_image_url", MEDIA)
            self.mirror_publish("local_gold_image_url", GOLD)
        self.assertEqual(self.versions(), settled)
        self.assertEqual(self.local_image(), MEDIA)

    def test_sync_still_fills_an_empty_local_image(self):
        self.related_sync()
        self.assertEqual(self.local_image(), RELATED)

    def test_real_changes_still_create_versions(self):
        self.related_sync()
        self.mirror_publish("local_image_url", MEDIA)
        before = self.versions()
        with self.conn.cursor() as cursor:
            cursor.execute("UPDATE constructed_cards SET mana_cost = 3 WHERE card_id = 'BE_036'")
        self.assertEqual(self.versions(), before + 1)
        with self.conn.cursor() as cursor:
            cursor.execute("UPDATE constructed_cards SET text_ru = 'Другой' WHERE card_id = 'BE_036'")
        self.assertEqual(self.versions(), before + 2)

    def set_image_url(self, url):
        with self.conn.cursor() as cursor:
            cursor.execute("UPDATE constructed_cards SET image_url = %s WHERE card_id = 'BE_036'", (url,))

    def test_image_url_change_with_new_mirrored_bytes_creates_a_version(self):
        self.related_sync()
        self.mirror_publish("local_image_url", MEDIA)
        before = self.versions()
        self.set_image_url("https://cdn/new.png")
        # The mirror then publishes the new bytes under their own sha path.
        self.mirror_publish("local_image_url", "/uploads/card-history-media/" + "c" * 64 + ".png")
        self.assertEqual(self.versions(), before + 1)

    def test_image_url_change_with_same_mirrored_bytes_creates_no_version(self):
        self.related_sync()
        self.mirror_publish("local_image_url", MEDIA)
        before = self.versions()
        self.set_image_url("https://cdn/other-host/same-bytes.png")
        self.mirror_publish("local_image_url", MEDIA)
        self.assertEqual(self.versions(), before)


if __name__ == "__main__":
    unittest.main()
