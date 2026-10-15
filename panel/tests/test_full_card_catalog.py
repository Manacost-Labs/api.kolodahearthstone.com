import json
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
