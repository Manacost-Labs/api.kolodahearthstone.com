import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_full_card_catalog as sync


class FullCardCatalogTest(unittest.TestCase):
    def fixtures(self):
        return [
            {
                "id": "BE_222t",
                "dbfId": 130406,
                "name": "Безднокрыл",
                "type": "MINION",
                "set": "BE",
                "collectible": False,
            },
            {"id": "ENCHANTMENT", "dbfId": 2, "name": "Эффект", "type": "ENCHANTMENT"},
        ]

    def test_tokens_and_non_playable_records_are_retained(self):
        rows = sync.catalog_rows(self.fixtures(), self.fixtures(), minimum=1)
        self.assertEqual([r["card_id"] for r in rows], ["BE_222t", "ENCHANTMENT"])
        self.assertEqual(rows[0]["collectible"], 0)
        self.assertEqual(json.loads(rows[0]["payload_ru"])["dbfId"], 130406)
        self.assertEqual(
            sync.plan(rows, {}), {"insert": 2, "update": 0, "unchanged": 0}
        )
        self.assertEqual(
            sync.plan(rows, {r["card_id"]: r["source_hash"] for r in rows}),
            {"insert": 0, "update": 0, "unchanged": 2},
        )

    def test_rejects_truncated_mismatched_and_conflicting_snapshots(self):
        rows = self.fixtures()
        for ru, en in [
            (rows, rows[:1]),
            (rows + rows[:1], rows),
            (rows, [dict(rows[0], dbfId=7), rows[1]]),
            (
                [rows[0], dict(rows[1], dbfId=130406)],
                [rows[0], dict(rows[1], dbfId=130406)],
            ),
        ]:
            with self.subTest(ru=ru, en=en), self.assertRaises(ValueError):
                sync.catalog_rows(ru, en, minimum=1)
        with self.assertRaises(ValueError):
            sync.catalog_rows(rows, rows)

    def test_failed_batch_rolls_back_without_committing(self):
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def executemany(self, *args):
                raise RuntimeError("write failed")

        class Connection:
            committed = rolled_back = False

            def begin(self):
                pass

            def cursor(self):
                return Cursor()

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

        connection = Connection()
        with self.assertRaises(RuntimeError):
            sync.publish(
                connection,
                sync.catalog_rows(self.fixtures(), self.fixtures(), minimum=1),
            )
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)




FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture_snapshots():
    return {
        locale: json.loads((FIXTURES / f"hsj-{locale}.json").read_text("utf-8"))
        for locale in sync.LOCALES
    }


class FakeCursor:
    """Records statements; answers the loader's SELECTs from `connection.tables`."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, *args):
        self.connection.statements.append(sql)
        self.sql = sql
        if "hearthstone_card_localizations" in sql and self.connection.no_table:
            raise RuntimeError(1146, "Table 'generator.hearthstone_card_localizations' doesn't exist")

    def fetchall(self):
        if "FROM hearthstone_card_localizations" in self.sql:
            return list(self.connection.localizations)
        if "FROM hearthstone_card_catalog" in self.sql:
            return list(self.connection.catalog)
        raise AssertionError(self.sql)

    def executemany(self, sql, rows):
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("write failed")
        self.connection.writes.append((sql, list(rows)))


class FakeConnection:
    def __init__(self, catalog=(), localizations=(), no_table=False, fail_on=None):
        self.catalog, self.localizations = list(catalog), list(localizations)
        self.no_table, self.fail_on = no_table, fail_on
        self.statements, self.writes = [], []
        self.begun = self.committed = self.rolled_back = 0

    def begin(self):
        self.begun += 1

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def writes_to(self, table):
        return [rows for sql, rows in self.writes if f"INTO {table} " in sql]


def published_state(snapshots):
    """What the DB holds after a successful apply of `snapshots`."""
    rows = sync.catalog_rows(snapshots["ruRU"], snapshots["enUS"], minimum=1)
    loc = sync.localization_rows(snapshots, minimum=1)
    catalog = [
        {"card_id": r["card_id"], "dbf": r["dbf"], "source_hash": r["source_hash"]}
        for r in rows
    ]
    localizations = [
        {"card_id": r["card_id"], "locale": r["locale"], "dbf": r["dbf"], "source_hash": r["source_hash"]}
        for rs in loc.values()
        for r in rs
    ]
    return catalog, localizations


def run(connection, snapshots, **kwargs):
    rows = sync.catalog_rows(snapshots["ruRU"], snapshots["enUS"], minimum=1)
    loc = sync.localization_rows(snapshots, minimum=1)
    options = {"apply": False, "refresh": False, "expect_total": None, "min_published": 1}
    options.update(kwargs)
    return sync.execute(connection, rows, loc, **options)


class LocalizationRowsTest(unittest.TestCase):
    def test_every_id_gets_one_canonical_row_per_locale(self):
        snapshots = load_fixture_snapshots()
        loc = sync.localization_rows(snapshots, minimum=1)
        self.assertEqual(list(loc), list(sync.LOCALES))
        ids = sorted(c["id"] for c in snapshots["enUS"])
        for locale, rows in loc.items():
            self.assertEqual([r["card_id"] for r in rows], ids)
            for row in rows:
                card = next(c for c in snapshots[locale] if c["id"] == row["card_id"])
                self.assertEqual(row["locale"], locale)
                self.assertEqual(row["dbf"], card["dbfId"])
                self.assertEqual(row["name"], card["name"])
                self.assertEqual(
                    row["payload"],
                    json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
                self.assertEqual(
                    row["source_hash"], hashlib.sha256(row["payload"].encode()).hexdigest()
                )

    def test_ru_and_en_payloads_equal_catalog_columns(self):
        snapshots = load_fixture_snapshots()
        catalog = {
            r["card_id"]: r
            for r in sync.catalog_rows(snapshots["ruRU"], snapshots["enUS"], minimum=1)
        }
        loc = sync.localization_rows(snapshots, minimum=1)
        for row in loc["ruRU"]:
            self.assertEqual(row["payload"], catalog[row["card_id"]]["payload_ru"])
        for row in loc["enUS"]:
            self.assertEqual(row["payload"], catalog[row["card_id"]]["payload_en"])

    def test_locales_really_differ_and_empty_fields_are_fine(self):
        snapshots = load_fixture_snapshots()
        loc = sync.localization_rows(snapshots, minimum=1)
        names = {l: {r["card_id"]: r["name"] for r in rows} for l, rows in loc.items()}
        self.assertEqual(
            {names[l]["BE_222t"] for l in sync.LOCALES},
            {"Безднокрыл", "Voidwing", "Pustkoskrzydły", "Alavacua"},
        )
        # the Battlegrounds minion has no howToEarn/collectionText in HSJ: not an error
        bg = next(c for c in snapshots["plPL"] if c["id"] == "BG25_354")
        self.assertNotIn("howToEarn", bg)

    def test_rejects_missing_id_other_dbf_duplicates_and_unknown_set(self):
        good = load_fixture_snapshots()
        cases = {}
        missing = copy.deepcopy(good)
        missing["plPL"] = missing["plPL"][:-1]
        cases["missing id"] = missing
        other_dbf = copy.deepcopy(good)
        other_dbf["esES"][0]["dbfId"] = 7
        cases["other dbf"] = other_dbf
        extra = copy.deepcopy(good)
        extra["ruRU"].append(dict(extra["ruRU"][0], id="ZZ_EXTRA", dbfId=999999))
        cases["extra id"] = extra
        dup = copy.deepcopy(good)
        dup["plPL"].append(dup["plPL"][0])
        cases["duplicate"] = dup
        absent = {k: v for k, v in good.items() if k != "esES"}
        cases["absent locale"] = absent
        for name, snapshots in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                sync.localization_rows(snapshots, minimum=1)

    def test_publication_gate_rejects_truncated_locale_by_default(self):
        # default minimum is 10 000 ids: the 5-card fixtures must not publish
        with self.assertRaises(ValueError):
            sync.localization_rows(load_fixture_snapshots())

    def test_oversized_snapshot_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.json"
            path.write_bytes(b" " * (sync.MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                sync.load_snapshot("plPL", str(path))


class PlanTest(unittest.TestCase):
    def test_unchanged_and_single_changed_locale_row(self):
        snapshots = load_fixture_snapshots()
        loc = sync.localization_rows(snapshots, minimum=1)
        _, existing_rows = published_state(snapshots)
        existing = {}
        for r in existing_rows:
            existing.setdefault(r["locale"], {})[r["card_id"]] = r["source_hash"]
        self.assertEqual(
            sync.plan_localizations(loc, existing)["plPL"],
            {"insert": 0, "update": 0, "unchanged": 5},
        )
        changed = copy.deepcopy(snapshots)
        changed["plPL"][1]["text"] = "Zmieniony tekst"
        plan = sync.plan_localizations(sync.localization_rows(changed, minimum=1), existing)
        self.assertEqual(plan["plPL"], {"insert": 0, "update": 1, "unchanged": 4})
        for locale in ("ruRU", "enUS", "esES"):
            self.assertEqual(plan[locale], {"insert": 0, "update": 0, "unchanged": 5})


class ExecuteTest(unittest.TestCase):
    def setUp(self):
        self.snapshots = load_fixture_snapshots()

    def test_dry_run_reports_every_locale_and_writes_nothing(self):
        connection = FakeConnection()
        result = run(connection, self.snapshots)
        self.assertFalse(result["applied"])
        self.assertEqual(result["total"], 5)
        self.assertEqual(
            result["localizations"],
            {l: {"insert": 5, "update": 0, "unchanged": 0} for l in sync.LOCALES},
        )
        self.assertEqual(connection.writes, [])
        self.assertEqual((connection.begun, connection.committed), (0, 0))

    def test_apply_writes_catalog_and_all_locales_in_one_transaction(self):
        connection = FakeConnection()
        result = run(connection, self.snapshots, apply=True, expect_total=5)
        self.assertTrue(result["applied"])
        self.assertEqual((connection.begun, connection.committed, connection.rolled_back), (1, 1, 0))
        self.assertEqual(sum(len(r) for r in connection.writes_to("hearthstone_card_catalog")), 5)
        written = connection.writes_to("hearthstone_card_localizations")
        self.assertEqual(sum(len(r) for r in written), 20)
        self.assertEqual({r["locale"] for rows in written for r in rows}, set(sync.LOCALES))

    def test_repeat_with_unchanged_hsj_writes_no_localization_rows(self):
        catalog, localizations = published_state(self.snapshots)
        connection = FakeConnection(catalog, localizations)
        result = run(connection, self.snapshots, apply=True, refresh=True)
        self.assertEqual(
            result["localizations"],
            {l: {"insert": 0, "update": 0, "unchanged": 5} for l in sync.LOCALES},
        )
        self.assertEqual(result["update"], 0)
        self.assertEqual(connection.writes_to("hearthstone_card_localizations"), [])

    def test_changed_text_in_one_locale_writes_exactly_that_row(self):
        catalog, localizations = published_state(self.snapshots)
        changed = copy.deepcopy(self.snapshots)
        changed["plPL"][2]["text"] = "Nowy tekst"
        connection = FakeConnection(catalog, localizations)
        run(connection, changed, apply=True, refresh=True)
        written = connection.writes_to("hearthstone_card_localizations")
        self.assertEqual(len(written), 1)
        self.assertEqual([(r["locale"], r["card_id"]) for r in written[0]], [("plPL", changed["plPL"][2]["id"])])

    def test_failed_write_rolls_back_catalog_and_localizations(self):
        connection = FakeConnection(fail_on="hearthstone_card_localizations")
        with self.assertRaises(RuntimeError):
            run(connection, self.snapshots, apply=True, expect_total=5)
        self.assertEqual((connection.committed, connection.rolled_back), (0, 1))

    def test_initial_apply_requires_expected_total(self):
        with self.assertRaises(ValueError):
            run(FakeConnection(), self.snapshots, apply=True, expect_total=4)

    def test_refresh_on_empty_localization_table_fails_clearly(self):
        catalog, _ = published_state(self.snapshots)
        connection = FakeConnection(catalog, [])
        with self.assertRaisesRegex(ValueError, "initial"):
            run(connection, self.snapshots, apply=True, refresh=True)
        self.assertEqual(connection.writes, [])

    def test_refresh_requires_95_percent_of_published_ids_per_locale(self):
        catalog, localizations = published_state(self.snapshots)
        # 20 published ids per locale, snapshot keeps only the 5 fixture ids
        extra = [
            {"card_id": f"OLD_{i}", "locale": l, "dbf": 500000 + i, "source_hash": "x" * 64}
            for l in sync.LOCALES
            for i in range(15)
        ]
        connection = FakeConnection(catalog, localizations + extra)
        with self.assertRaisesRegex(ValueError, "95%"):
            run(connection, self.snapshots, apply=True, refresh=True)
        self.assertEqual(connection.writes, [])

    def test_refresh_requires_minimum_published_rows_per_locale(self):
        catalog, localizations = published_state(self.snapshots)
        connection = FakeConnection(catalog, localizations)
        with self.assertRaises(ValueError):
            run(connection, self.snapshots, apply=True, refresh=True, min_published=6)

    def test_missing_localization_table_is_a_clear_error(self):
        connection = FakeConnection(no_table=True)
        with self.assertRaisesRegex(RuntimeError, "card_localizations.sql"):
            run(connection, self.snapshots)
        self.assertEqual(connection.writes, [])

    def test_published_dbf_conflict_in_localizations_is_rejected(self):
        catalog, localizations = published_state(self.snapshots)
        # the same (locale, dbf) is already published under a different card id
        localizations[0] = dict(localizations[0], card_id="OTHER_CARD")
        connection = FakeConnection(catalog, localizations)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            run(connection, self.snapshots, apply=True, refresh=True)


class SqlContractTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_mariadb_ddl_keys_match_the_contract(self):
        ddl = (self.ROOT / "panel/sql/card_localizations.sql").read_text()
        for fragment in (
            "CREATE TABLE IF NOT EXISTS hearthstone_card_localizations",
            "PRIMARY KEY (card_id, locale)",
            "UNIQUE KEY uq_card_localizations_dbf_locale (dbf, locale)",
            "KEY idx_card_localizations_locale_name (locale, name)",
            "payload MEDIUMTEXT NOT NULL CHECK (JSON_VALID(payload))",
        ):
            self.assertIn(fragment, ddl)
        code = "\n".join(l for l in ddl.splitlines() if not l.startswith("--"))
        self.assertNotIn("ENUM", code)
        self.assertNotIn("locale IN", code)

    def test_postgres_mirror_has_the_same_columns_in_the_same_order(self):
        import re

        def columns(text, table):
            body = text.split(table, 1)[1].split("(", 1)[1].split("\n);", 1)[0]
            names = []
            for line in body.splitlines():
                m = re.match(r"\s+([a-z_]+)\s+[A-Za-z]", line)
                if m and m.group(1) not in ("primary", "unique", "key"):
                    names.append(m.group(1))
            return names

        maria = (self.ROOT / "panel/sql/card_localizations.sql").read_text()
        pg = (self.ROOT / "platform/sql/021_card_localizations.sql").read_text()
        self.assertEqual(
            columns(maria, "hearthstone_card_localizations"),
            columns(pg, "catalog.hearthstone_card_localizations"),
        )
        self.assertEqual(
            columns(pg, "catalog.hearthstone_card_localizations"),
            ["card_id", "locale", "dbf", "name", "payload", "source_hash", "updated_at"],
        )


@unittest.skipUnless(
    os.getenv("RUN_CARD_LOCALIZATION_MYSQL_TESTS") == "1",
    "throwaway MariaDB integration is opt-in",
)
class LocalizationMariaDbTest(unittest.TestCase):
    """Real MariaDB 10.11 in docker, never the production `generator` database."""

    IMAGE = os.getenv("CARD_LOCALIZATION_MARIADB_IMAGE", "mariadb:10.11")

    @classmethod
    def setUpClass(cls):
        import pymysql

        cls.name = "hs-locales-test-" + uuid.uuid4().hex[:10]
        cls.password = uuid.uuid4().hex
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", cls.name, "-p", "127.0.0.1::3306",
             "-e", "MARIADB_ROOT_PASSWORD=" + cls.password, "-e", "MARIADB_DATABASE=generator",
             cls.IMAGE, "--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci"],
            check=True, capture_output=True,
        )
        port = subprocess.run(
            ["docker", "port", cls.name, "3306/tcp"], check=True, capture_output=True, text=True
        ).stdout.strip().rsplit(":", 1)[1]
        import time

        deadline = time.time() + 120
        while True:
            try:
                cls.connection = pymysql.connect(
                    host="127.0.0.1", port=int(port), user="root", password=cls.password,
                    database="generator", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                )
                break
            except pymysql.err.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        root = Path(__file__).resolve().parents[1]
        with cls.connection.cursor() as cursor:
            for ddl in ("scripts/full_card_catalog.sql", "sql/card_localizations.sql"):
                cursor.execute((root / ddl).read_text())

    @classmethod
    def tearDownClass(cls):
        try:
            cls.connection.close()
        finally:
            subprocess.run(["docker", "rm", "-f", cls.name], capture_output=True, check=False)

    def setUp(self):
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM hearthstone_card_localizations")
            cursor.execute("DELETE FROM hearthstone_card_catalog")

    def rows(self, sql):
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    def test_apply_repeat_and_single_locale_change(self):
        import time

        snapshots = load_fixture_snapshots()
        result = run(self.connection, snapshots, apply=True, expect_total=5)
        self.assertTrue(result["applied"])
        counts = self.rows("SELECT locale, COUNT(*) n FROM hearthstone_card_localizations GROUP BY locale")
        self.assertEqual({r["locale"]: r["n"] for r in counts}, {l: 5 for l in sync.LOCALES})
        mismatches = self.rows(
            "SELECT COUNT(*) n FROM hearthstone_card_localizations l "
            "JOIN hearthstone_card_catalog c USING (card_id) "
            "WHERE (l.locale='ruRU' AND l.payload<>c.payload_ru) "
            "OR (l.locale='enUS' AND l.payload<>c.payload_en)"
        )
        self.assertEqual(mismatches[0]["n"], 0)
        before = self.rows("SELECT card_id, locale, updated_at FROM hearthstone_card_localizations ORDER BY 1, 2")
        catalog_before = self.rows("SELECT * FROM hearthstone_card_catalog ORDER BY card_id")
        time.sleep(1.1)

        again = run(self.connection, snapshots, apply=True, refresh=True)
        self.assertEqual(
            again["localizations"], {l: {"insert": 0, "update": 0, "unchanged": 5} for l in sync.LOCALES}
        )
        self.assertEqual(
            before,
            self.rows("SELECT card_id, locale, updated_at FROM hearthstone_card_localizations ORDER BY 1, 2"),
        )

        changed = copy.deepcopy(snapshots)
        changed["plPL"][0]["text"] = "Zmieniony tekst"
        run(self.connection, changed, apply=True, refresh=True)
        after = self.rows("SELECT card_id, locale, updated_at FROM hearthstone_card_localizations ORDER BY 1, 2")
        moved = [(a["card_id"], a["locale"]) for a, b in zip(after, before) if a["updated_at"] != b["updated_at"]]
        self.assertEqual(moved, [(changed["plPL"][0]["id"], "plPL")])
        self.assertEqual(
            catalog_before, self.rows("SELECT * FROM hearthstone_card_catalog ORDER BY card_id")
        )

    def test_rejected_snapshot_leaves_database_unchanged(self):
        snapshots = load_fixture_snapshots()
        run(self.connection, snapshots, apply=True, expect_total=5)
        before = self.rows("SELECT card_id, locale, source_hash, updated_at FROM hearthstone_card_localizations ORDER BY 1, 2")
        broken = copy.deepcopy(snapshots)
        broken["esES"] = broken["esES"][:-1]
        with self.assertRaises(ValueError):
            sync.localization_rows(broken, minimum=1)
        self.assertEqual(
            before,
            self.rows("SELECT card_id, locale, source_hash, updated_at FROM hearthstone_card_localizations ORDER BY 1, 2"),
        )


if __name__ == "__main__":
    unittest.main()
