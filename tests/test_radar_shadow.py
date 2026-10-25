"""Public radar mirror must be atomic, lossless and exclude private tables."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.hsguru_deck_radar import ensure_deck_radar_schema

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "radar_shadow", ROOT / "platform/scripts/radar-shadow.py"
)
radar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(radar)


def make_database(path):
    with sqlite3.connect(path) as connection:
        ensure_deck_radar_schema(connection)
        connection.execute("CREATE TABLE api_tokens (secret TEXT)")
        connection.execute("INSERT INTO api_tokens VALUES ('never-copy-this-secret')")
        connection.execute(
            "INSERT INTO hsguru_deck_radar_snapshots VALUES ('streamers', '2026-10-12', 'now')"
        )
        connection.execute(
            "INSERT INTO hsguru_deck_radar_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "streamers",
                "2026-10-12",
                "abc",
                "AA==",
                "O'Brien\nline\t\\N",
                None,
                "wild",
                "https://example.com",
            ),
        )
        connection.execute(
            "INSERT INTO hsguru_deck_radar_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "abc",
                "new",
                "candidate",
                "AA==",
                "name",
                None,
                "wild",
                None,
                "first",
                "last",
                1,
            ),
        )


def test_lossless_public_only_and_transaction_owned_by_caller(tmp_path):
    path = tmp_path / "radar.sqlite"
    make_database(path)
    original = path.read_bytes()
    fragment = radar.build_fragment(path)
    assert path.read_bytes() == original
    assert "never-copy-this-secret" not in fragment
    assert "api_tokens" not in fragment
    assert "O'Brien\\nline\\t\\\\N" in fragment
    assert "\t\\N\t" in fragment
    assert "BEGIN;" not in fragment and "COMMIT;" not in fragment
    assert fragment.count("COPY ") == 3
    assert fragment.count("ON CONFLICT (") == 3
    assert "DELETE FROM" not in fragment
    assert '"status" = EXCLUDED."status"' in fragment
    assert '"observation_count" = EXCLUDED."observation_count"' in fragment


def test_schema_change_is_rejected_before_output(tmp_path):
    path = tmp_path / "radar.sqlite"
    make_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "ALTER TABLE hsguru_deck_radar_events ADD COLUMN private_data TEXT"
        )
    with pytest.raises(ValueError, match="schema mismatch"):
        radar.build_fragment(path)


def test_missing_source_is_never_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        radar.build_fragment(path)
    assert not path.exists()


def test_copy_null_and_escape_characters():
    assert radar.copy_cell(None) == r"\N"
    assert radar.copy_cell("\\N") == r"\\N"
    assert radar.copy_cell("\t\n\r\\") == r"\t\n\r\\"


def test_atomic_sync_integration():
    sync = (ROOT / "platform/scripts/sync-shadow.php").read_text()
    assert "radar-shadow.py" in sync
    assert (
        sync.index("$radarSql =")
        < sync.index("$mergeOutput =")
        < sync.index("$verifyOutput =")
    )
    # apply-migrations.sh applies every numbered file in platform/sql.
    assert (ROOT / "platform/sql/013_hsguru_deck_radar.sql").is_file()
