import os
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from card_history_schema import BOOKKEEPING, TABLES, statements


class HistorySchemaTest(unittest.TestCase):
    def test_snapshot_tracks_content_but_not_fetch_bookkeeping(self):
        triggers, baseline = statements("constructed_cards", ["card_id", "mana_cost", "text_ru", "art_image", "fetched_at", "source_payload_json"])
        self.assertEqual(len(triggers), 3)
        comparison = triggers[1].split(" THEN ", 1)[0]
        for field in ("mana_cost", "text_ru", "art_image"):
            self.assertIn("NEW.`" + field + "`", comparison)
        for field in ("fetched_at", "source_payload_json"):
            self.assertNotIn("NEW.`" + field + "`", comparison)
            self.assertIn("NEW.`" + field + "`", triggers[1])
        self.assertIn("v.card_id = `constructed_cards`.`card_id`", baseline)

    def test_library_identity_includes_library(self):
        triggers, baseline = statements("battlegrounds_library_cards", ["library", "card_id", "name_ru"])
        self.assertIn("CONCAT(OLD.`library`, ':', OLD.`card_id`)", triggers[1])
        self.assertIn("`battlegrounds_library_cards`.`library`", baseline)

    def test_unsafe_or_missing_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            statements("constructed_cards", ["card_id", "bad`sql"])
        with self.assertRaises(ValueError):
            statements("constructed_cards", ["name"])
        self.assertIn("hearthstone_coins", TABLES)
        self.assertIn("updated_at", BOOKKEEPING)


@unittest.skipUnless(os.getenv("RUN_CARD_HISTORY_MYSQL_TESTS") == "1", "isolated local MySQL integration is opt-in")
class HistoryMysqlTest(unittest.TestCase):
    @unittest.skipUnless(os.getenv('RUN_CATALOG_SNAPSHOT_TEST') == '1', 'read-only catalog fixture capture is opt-in')
    def test_reconciliation_against_isolated_actual_catalog_schema(self):
        import pymysql
        import subprocess
        import secrets
        import json
        from pymysql.cursors import DictCursor
        from install_card_history import install
        from reconcile_card_catalog import BARTENDER_DDL, read_snapshot, build_plan, apply_plan

        database = 'test_card_catalog_' + uuid.uuid4().hex
        password = secrets.token_hex(32)
        def admin(sql):
            subprocess.run(['mysql', '--batch'], input=sql, text=True, check=True, capture_output=True)
        tables = ['hero_skins','battlegrounds_hero_skins','battlegrounds_heroes',
                  'battlegrounds_library_cards','battlegrounds_timewarped_cards', 'constructed_cards', 'constructed_format_cards', 'battlegrounds_cards']
        sql = "CREATE DATABASE `" + database + "`; CREATE USER '" + database + "'@'localhost' IDENTIFIED BY '" + password + "'; GRANT ALL ON `" + database + "`.* TO '" + database + "'@'localhost';"
        for table in tables:
            sql += 'CREATE TABLE `' + database + '`.`' + table + '` LIKE generator.`' + table + '`;'
            sql += 'INSERT INTO `' + database + '`.`' + table + '` SELECT * FROM generator.`' + table + '`;'
        connection = None
        try:
            admin(sql)
            connection = pymysql.connect(unix_socket='/run/mysqld/mysqld.sock', user=database, password=password,
                                         database=database, cursorclass=DictCursor)
            with connection.cursor() as cursor:
                cursor.execute(BARTENDER_DDL)
                cursor.execute('SELECT COUNT(*) AS n FROM constructed_format_cards')
                formats_before = cursor.fetchone()['n']
            install(connection, True)
            evidence_root = Path(os.environ['CARD_CATALOG_EVIDENCE_ROOT'])
            ru = json.loads((evidence_root / 'upstream-cards-ru.json').read_text())
            en = json.loads((evidence_root / 'upstream-cards-en.json').read_text())
            plan = build_plan(read_snapshot(connection), ru, en)
            self.assertFalse(any(plan['unresolved'].values()))
            apply_plan(connection, plan)
            repeated = build_plan(read_snapshot(connection), ru, en)
            self.assertEqual({key:len(value) for key,value in repeated.items() if key != 'unresolved'},
                             {key:0 for key in plan if key != 'unresolved'})
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) AS n FROM constructed_format_cards')
                self.assertEqual(cursor.fetchone()['n'], formats_before)
                cursor.execute("SELECT COUNT(*) AS n FROM battlegrounds_bartender_skins WHERE parent_card_id='TB_BaconShopBob'")
                self.assertEqual(cursor.fetchone()['n'], 50)
                cursor.execute("SELECT COUNT(*) AS n FROM battlegrounds_library_cards WHERE source='hearthstonejson.com' AND pool_status='unknown' AND in_pool=0")
                self.assertEqual(cursor.fetchone()['n'], 61)
                cursor.execute("SELECT COUNT(*) AS n FROM catalog_card_versions WHERE event_kind='changed' AND entity='constructed_card'")
                self.assertEqual(cursor.fetchone()['n'], len(plan['text_repairs']))
                cursor.execute("SELECT COUNT(*) AS n FROM battlegrounds_cards c LEFT JOIN battlegrounds_cards b ON b.dbf=c.base_dbf WHERE c.variant_kind='golden' AND c.base_dbf IS NOT NULL AND b.card_id IS NULL")
                self.assertEqual(cursor.fetchone()['n'], 0)
            print('Isolated catalog reconciliation verified: ' + str({key:len(value) for key,value in plan.items() if key != 'unresolved'}))
        finally:
            if connection:
                connection.close()
            admin("DROP DATABASE IF EXISTS `" + database + "`; DROP USER IF EXISTS '" + database + "'@'localhost';")

    def test_atomic_versions_bootstrap_repetition_removal_and_library_identity(self):
        import pymysql
        import subprocess
        import secrets
        from pymysql.cursors import DictCursor
        from install_card_history import install

        database = "test_card_history_" + uuid.uuid4().hex
        def admin(sql):
            subprocess.run(['mysql', '--batch'], input=sql, text=True, check=True, capture_output=True)

        # The admin CLI uses its existing authentication; do not inspect it.
        # Grant this disposable identity access only to the test schema.
        password = secrets.token_hex(32)
        admin("CREATE DATABASE `" + database + "`; CREATE USER '" + database + "'@'localhost' IDENTIFIED BY '" + password + "'; GRANT ALL ON `" + database + "`.* TO '" + database + "'@'localhost';")
        connection = None
        try:
            connection = pymysql.connect(unix_socket="/run/mysqld/mysqld.sock", user=database, password=password, database=database, cursorclass=DictCursor)
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE constructed_cards (card_id VARCHAR(191) PRIMARY KEY, mana_cost INT, text_ru TEXT, art_image TEXT, fetched_at DATETIME, source_payload_json LONGTEXT) ENGINE=InnoDB")
                cursor.execute("CREATE TABLE battlegrounds_library_cards (library VARCHAR(40), card_id VARCHAR(191), name_ru TEXT, PRIMARY KEY(library, card_id)) ENGINE=InnoDB")
                cursor.execute("CREATE TABLE battlegrounds_cards (card_id VARCHAR(191) PRIMARY KEY, dbf INT UNIQUE, variant_kind VARCHAR(20), premium_dbf INT, card_type VARCHAR(20), in_pool INT, card_image TEXT, golden_image TEXT, updated_at DATETIME) ENGINE=InnoDB")
                cursor.execute("INSERT INTO battlegrounds_cards VALUES ('BG_FIXTURE',1,'base',2,'minion',1,'/base.png','/old-gold.png',CURRENT_TIMESTAMP),('BG_FIXTURE_G',2,'golden',NULL,'minion',0,'/old-gold.png',NULL,CURRENT_TIMESTAMP)")
                cursor.execute("INSERT INTO constructed_cards VALUES ('A',2,'Два','/a.png',CURRENT_TIMESTAMP,'{}')")
                cursor.execute("INSERT INTO constructed_cards VALUES ('B',1,'Один',NULL,CURRENT_TIMESTAMP,'{}')")
                cursor.execute("INSERT INTO battlegrounds_library_cards VALUES ('reward','X','A'),('trinket','X','B')")
            connection.commit()
            install(connection, True)
            install(connection, True)
            import sync_battlegrounds_images as image_sync
            import tempfile
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "golden.png"
                target.write_bytes(b"previously validated golden render")
                asset = {"target":str(target), "public_path":"/uploads/golden/BG_FIXTURE-sha.png", "card_id":"BG_FIXTURE"}
                self.assertEqual(image_sync.persist_golden_paths(connection,[asset]),2)
                self.assertEqual(image_sync.persist_golden_paths(connection,[asset]),0)
            with connection.cursor() as cursor:
                cursor.execute("SELECT card_id,COUNT(*) AS n FROM catalog_card_versions WHERE entity='card' GROUP BY card_id ORDER BY card_id")
                self.assertEqual(cursor.fetchall(),[{"card_id":"BG_FIXTURE","n":2},{"card_id":"BG_FIXTURE_G","n":2}])
                cursor.execute("SELECT in_pool,card_image,golden_image FROM battlegrounds_cards WHERE card_id='BG_FIXTURE'")
                self.assertEqual(cursor.fetchone(),{"in_pool":1,"card_image":"/base.png","golden_image":"/uploads/golden/BG_FIXTURE-sha.png"})
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM catalog_card_versions WHERE entity='constructed_card'")
                self.assertEqual(cursor.fetchone()["n"], 2)
                cursor.execute("UPDATE constructed_cards SET fetched_at='2026-10-11',source_payload_json='{\"fresh\":true}' WHERE card_id='A'")
                cursor.execute("UPDATE constructed_cards SET mana_cost=3,text_ru='Три' WHERE card_id='A'")
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM catalog_card_versions WHERE card_id='A'")
                self.assertEqual(cursor.fetchone()["n"], 1)
                cursor.execute("UPDATE constructed_cards SET fetched_at='2026-10-11',source_payload_json='{\"fresh\":true}' WHERE card_id='A'")
                cursor.execute("UPDATE constructed_cards SET mana_cost=3,text_ru='Три' WHERE card_id='A'")
                cursor.execute("UPDATE constructed_cards SET mana_cost=2,text_ru='Два' WHERE card_id='A'")
                cursor.execute("SELECT snapshot_hash,event_kind,JSON_VALUE(snapshot_json,'$.mana_cost') AS cost FROM catalog_card_versions WHERE card_id='A' ORDER BY id")
                rows = cursor.fetchall()
                self.assertEqual([int(row['cost']) for row in rows], [2, 3, 2])
                self.assertEqual([row['event_kind'] for row in rows], ['baseline', 'changed', 'changed'])
                self.assertEqual(rows[0]['snapshot_hash'], rows[2]['snapshot_hash'])
                cursor.execute("DELETE FROM constructed_cards WHERE card_id='A'")
                cursor.execute("INSERT INTO constructed_cards VALUES ('A',4,'Четыре','/new.png',CURRENT_TIMESTAMP,'{}')")
                cursor.execute("SELECT event_kind FROM catalog_card_versions WHERE card_id='A' ORDER BY id")
                self.assertEqual([row['event_kind'] for row in cursor.fetchall()], ['baseline', 'changed', 'changed', 'removed', 'new'])
                cursor.execute("UPDATE battlegrounds_library_cards SET name_ru='C' WHERE library='trinket'")
                cursor.execute("SELECT card_id,COUNT(*) AS n FROM catalog_card_versions WHERE entity='library_card' GROUP BY card_id ORDER BY card_id")
                self.assertEqual(cursor.fetchall(), [{'card_id':'reward:X', 'n':1}, {'card_id':'trinket:X', 'n':2}])
                cursor.execute("UPDATE constructed_cards SET card_id='C' WHERE card_id='B'")
                cursor.execute("SELECT event_kind,JSON_VALUE(snapshot_json,'$._previous_card_id') AS previous FROM catalog_card_versions WHERE card_id='C'")
                self.assertEqual(cursor.fetchall(), [{'event_kind':'renamed', 'previous':'B'}])
            connection.commit()
        finally:
            if connection:
                connection.close()
            admin("DROP DATABASE IF EXISTS `" + database + "`; DROP USER IF EXISTS '" + database + "'@'localhost';")
