"""Opt-in PostgreSQL regression for the MySQL/pgloader media type contract."""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PLATFORM_POSTGRES_TESTS") != "1",
    reason="requires the local PostgreSQL container; no external requests",
)


def postgres(sql):
    return subprocess.run(
        [
            "sudo",
            "-n",
            "docker",
            "exec",
            "-i",
            "hs-data-postgres-1",
            "sh",
            "-eu",
            "-c",
            (
                "psql -X -qAt -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate "
                '-U "$POSTGRES_USER" -d hs_data'
            ),
        ],
        input=sql,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def fragments():
    migration = (ROOT / "platform/sql/014_card_version_media.sql").read_text()
    migration = migration.replace("catalog.", "codex_media_target.").replace(
        "table_schema = 'catalog'", "table_schema = 'codex_media_target'"
    )
    sync = (ROOT / "platform/scripts/sync-shadow.php").read_text()
    match = re.search(r"DO \$media_columns\$.*?\$media_columns\$;", sync, re.DOTALL)
    assert match, "stage normalization must run before the mirror type gate"
    normalize = match[0].replace("catalog_stage.", "codex_media_stage.")
    return migration, normalize


@pytest.mark.parametrize("upgrade", [False, True])
def test_pgloader_types_upgrade_timezone_foreign_key_and_idempotency(upgrade):
    migration, normalize = fragments()
    old = ""
    if upgrade:
        old = """
CREATE TABLE codex_media_target.catalog_card_version_media (
 version_id bigint REFERENCES codex_media_target.catalog_card_versions(id),
 field_name varchar(64), source_url text NOT NULL, status varchar(24) NOT NULL,
 reason varchar(64), archive_url varchar(255), sha256 char(64), byte_size bigint,
 mime_type varchar(64), evidence varchar(40), captured_at timestamp NOT NULL,
 PRIMARY KEY(version_id, field_name)
);
INSERT INTO codex_media_target.catalog_card_version_media
 (version_id,field_name,source_url,status,captured_at)
 VALUES (1,'old','https://example.com/old.png','archived','2026-10-12 12:34:56.123456');
"""
    result = postgres(
        """
BEGIN;
SET LOCAL TIME ZONE 'America/New_York';
CREATE SCHEMA codex_media_target;
CREATE SCHEMA codex_media_stage;
CREATE TABLE codex_media_target.catalog_card_versions(id bigint PRIMARY KEY);
INSERT INTO codex_media_target.catalog_card_versions VALUES (1);
"""
        + old
        + migration
        + migration
        + """
CREATE TABLE codex_media_stage.catalog_card_version_media
 (LIKE codex_media_target.catalog_card_version_media);
ALTER TABLE codex_media_stage.catalog_card_version_media
 ALTER COLUMN version_id TYPE numeric;
INSERT INTO codex_media_stage.catalog_card_version_media
 (version_id,field_name,source_url,status,byte_size,captured_at)
 VALUES (1,'card_image','https://example.com/image.png','archived',
 18446744073709551615,'2026-10-12 14:00:00.123456+02');
"""
        + normalize
        + normalize
        + """
DO $$ BEGIN
 IF (SELECT array_agg(udt_name ORDER BY ordinal_position)
     FROM information_schema.columns WHERE table_schema='codex_media_target'
     AND table_name='catalog_card_version_media') IS DISTINCT FROM
    (SELECT array_agg(udt_name ORDER BY ordinal_position)
     FROM information_schema.columns WHERE table_schema='codex_media_stage'
     AND table_name='catalog_card_version_media') THEN
  RAISE EXCEPTION 'mirror type gate mismatch';
 END IF;
END $$;
INSERT INTO codex_media_target.catalog_card_version_media
 SELECT * FROM codex_media_stage.catalog_card_version_media;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM codex_media_target.catalog_card_version_media
   WHERE field_name='card_image' AND byte_size=18446744073709551615
   AND captured_at='2026-10-12 12:00:00.123456+00'::timestamptz) THEN
  RAISE EXCEPTION 'unsigned size or timezone precision was lost';
 END IF;
 IF EXISTS (SELECT 1 FROM codex_media_target.catalog_card_version_media
   WHERE field_name='old' AND captured_at<>'2026-10-12 12:34:56.123456+00'::timestamptz) THEN
  RAISE EXCEPTION 'existing UTC capture changed';
 END IF;
 BEGIN
  UPDATE codex_media_target.catalog_card_version_media SET version_id=2;
  RAISE EXCEPTION 'foreign key was disabled';
 EXCEPTION WHEN foreign_key_violation THEN NULL;
 END;
END $$;
ROLLBACK;
SELECT to_regnamespace('codex_media_target') IS NULL
 AND to_regnamespace('codex_media_stage') IS NULL;
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "t"


def test_unsigned_version_overflow_aborts_without_leaving_a_schema():
    _, normalize = fragments()
    result = postgres(
        """
BEGIN;
CREATE SCHEMA codex_media_stage;
CREATE TABLE codex_media_stage.catalog_card_version_media(version_id numeric);
INSERT INTO codex_media_stage.catalog_card_version_media VALUES (18446744073709551615);
"""
        + normalize
    )
    assert result.returncode != 0
    assert "22003" in result.stderr
    clean = postgres("SELECT to_regnamespace('codex_media_stage') IS NULL;")
    assert clean.returncode == 0 and clean.stdout.strip() == "t"
