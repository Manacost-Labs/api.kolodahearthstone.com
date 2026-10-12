#!/usr/bin/env python3
"""Build an atomic mirror fragment for the three public deck-radar tables.

The caller embeds this fragment before COMMIT in the existing shadow transaction.
Only an explicit public allowlist is read; private SQLite tables are never copied.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

TABLES = {
    "hsguru_deck_radar_snapshots": (
        ("source_id", "source_fetched_at", "recorded_at"),
        ("source_id", "source_fetched_at"),
    ),
    "hsguru_deck_radar_observations": (
        (
            "source_id",
            "source_fetched_at",
            "deck_fingerprint",
            "deck_code",
            "deck_name",
            "streamer",
            "format",
            "source_url",
        ),
        ("source_id", "source_fetched_at", "deck_fingerprint"),
    ),
    "hsguru_deck_radar_events": (
        (
            "deck_fingerprint",
            "event_type",
            "status",
            "deck_code",
            "deck_name",
            "streamer",
            "format",
            "source_url",
            "first_seen_at",
            "last_seen_at",
            "observation_count",
        ),
        ("deck_fingerprint",),
    ),
}


def copy_cell(value):
    if value is None:
        return r"\N"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\t", r"\t")
        .replace("\n", r"\n")
        .replace("\r", r"\r")
    )


def build_fragment(database):
    ddl_path = Path(__file__).resolve().parents[1] / "sql/013_hsguru_deck_radar.sql"
    sql = [ddl_path.read_text()]
    with sqlite3.connect(
        Path(database).resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        connection.execute("BEGIN")
        # Validate all shapes before emitting anything, and pin one read snapshot.
        for table, (columns, keys) in TABLES.items():
            shape = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            actual_keys = tuple(
                row[1] for row in sorted(shape, key=lambda row: row[5]) if row[5]
            )
            if tuple(row[1] for row in shape) != columns or actual_keys != keys:
                raise ValueError("Deck radar schema mismatch: " + table)
        for table, (columns, keys) in TABLES.items():
            stage = '"radar_stage_' + table + '"'
            quoted_columns = ", ".join('"' + name + '"' for name in columns)
            quoted_keys = ", ".join('"' + name + '"' for name in keys)
            sql.append(
                f'CREATE TEMP TABLE {stage} (LIKE analytics."{table}" INCLUDING CONSTRAINTS) ON COMMIT DROP;'
            )
            sql.append(f"COPY {stage} ({quoted_columns}) FROM STDIN;")
            for row in connection.execute(
                f'SELECT {quoted_columns} FROM "{table}" ORDER BY {quoted_keys}'
            ):
                sql.append("\t".join(copy_cell(value) for value in row))
            sql.append(r"\.")
            updates = ", ".join(
                f'"{name}" = EXCLUDED."{name}"' for name in columns if name not in keys
            )
            sql.append(
                f'INSERT INTO analytics."{table}" ({quoted_columns}) SELECT {quoted_columns} FROM {stage} '
                f"ON CONFLICT ({quoted_keys}) DO UPDATE SET {updates};"
            )
        connection.rollback()
    return "\n".join(sql) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="/srv/hs-data-api/data/hs_parses.db")
    args = parser.parse_args()
    try:
        fragment = build_fragment(args.database)
    except (OSError, sqlite3.Error, ValueError):
        # Never echo source rows, paths or SQL errors into synchronization logs.
        print(
            "Unable to stage public deck radar: source or schema unavailable",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(fragment)
    return 0


if __name__ == "__main__":
    sys.exit(main())
