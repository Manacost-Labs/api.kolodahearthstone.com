import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import install_card_history as installer


class FakeCursor:
    def __init__(self, version):
        self.version = version
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *args):
        self.executed.append(sql)

    def fetchone(self):
        last = self.executed[-1]
        if "GET_LOCK" in last:
            return {"acquired": 1}
        return {"version": self.version}

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, version):
        self.cursor_obj = FakeCursor(version)

    def cursor(self):
        return self.cursor_obj


class UpgradeTriggersTest(unittest.TestCase):
    def test_upgrade_requires_backup_path(self):
        with self.assertRaises(ValueError):
            installer.install(FakeConnection("10.11.6-MariaDB"), True, upgrade_triggers=True)

    def test_upgrade_requires_mariadb(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                installer.install(
                    FakeConnection("8.0.36"), True, upgrade_triggers=True,
                    backup_path=Path(directory) / "triggers.json",
                )

    def test_backup_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "triggers.json"
            backup.write_text("keep")
            with self.assertRaises(FileExistsError):
                installer.install(
                    FakeConnection("10.11.6-MariaDB"), True, upgrade_triggers=True,
                    backup_path=backup,
                )
            self.assertEqual(backup.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
