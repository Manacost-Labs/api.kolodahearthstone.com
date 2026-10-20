from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_wiki_heroes as heroes


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self.conn = conn
        self.result = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.statements.append(" ".join(sql.split()))
        self.result = self.conn.row if sql.lstrip().upper().startswith("SELECT") else None

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, row: dict | None) -> None:
        self.row = row
        self.statements: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


ENTRY = {"card_id": "BG22_HERO_007", "dbf_id": 1, "name": "Hero"}


class SaveErrorTest(unittest.TestCase):
    def writes(self, conn: FakeConnection) -> list[str]:
        return [s for s in conn.statements if not s.upper().startswith("SELECT")]

    def test_rate_limit_keeps_last_good_snapshot(self) -> None:
        conn = FakeConnection({"status": "ok"})
        heroes.save_error(conn, ENTRY, "error", "HTTP Error 429: Too Many Requests", False)
        self.assertEqual(self.writes(conn), [])

    def test_unknown_hero_still_records_error(self) -> None:
        conn = FakeConnection(None)
        heroes.save_error(conn, ENTRY, "error", "HTTP Error 429: Too Many Requests", False)
        self.assertEqual(len(self.writes(conn)), 1)
        self.assertIn("INSERT INTO battlegrounds_heroes", self.writes(conn)[0])

    def test_previous_error_row_is_refreshed(self) -> None:
        conn = FakeConnection({"status": "error"})
        heroes.save_error(conn, ENTRY, "error", "boom", False)
        self.assertEqual(len(self.writes(conn)), 1)


if __name__ == "__main__":
    unittest.main()
