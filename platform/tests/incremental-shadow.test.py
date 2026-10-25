#!/usr/bin/env python3
"""Fixture contract for incremental SQLite-to-PostgreSQL analytics shadowing.

Run only against the disposable container created for this task:
  tests/incremental-shadow.test.py --container <full-container-id>

The test deliberately imports ``scripts/stage-analytics.py`` instead of
shelling through the production synchronizer.  This keeps every write inside
the disposable PostgreSQL 17 fixture while exercising the real COPY and merge
boundaries.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STAGE_PATH = ROOT / "scripts" / "stage-analytics.py"
MERGE_PATH = ROOT / "scripts" / "merge-analytics.sql"
SYNC_PATH = ROOT / "scripts" / "sync-shadow.php"
FIXTURE_NAME = "hs-shadow-fixture-20260906"
FIXTURE_LABEL = "shadow-incremental-20260906"
FIXTURE_IMAGE = "sha256:7958605b474b3d264a969cb3a123d6aa00ad1e1fe9da8a69984dabb704d93317"
FIXTURE_TMPFS = "/var/lib/postgresql/data"
FIXTURE_MEMORY = 768 * 1024 * 1024
FIXTURE_CPUS = 1_000_000_000

PUBLIC_TABLES = (
    "archetype_deck_cards",
    "archetype_decks",
    "archetype_matchups",
    "archetype_mulligan",
    "archetype_refresh_runs",
    "archetype_snapshots",
    "archetype_time_series",
    "bg_minion_refresh_runs",
    "bg_minion_round_stats",
    "bg_minion_snapshots",
    "bg_minion_stat_medians",
    "bg_minion_stat_positions",
    "bg_minion_stat_purchases",
    "bg_minion_stat_rounds",
    "bg_minion_stat_snapshots",
    "bg_minion_stat_summaries",
    "bg_minions",
    "card_popularity_history",
    "decks",
    "fetch_log",
    "hearthstone_patches",
    "hsguru_archetype_history",
    "hsguru_class_matchups",
    "hsguru_mulligan_cards",
    "hsguru_stat_runs",
    "hsguru_stat_snapshots",
    "hsguru_stat_targets",
    "hsreplay_archetypes",
)
CONTAINER_ID = ""


def quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def require_fixture(container_id: str) -> None:
    """Refuse a name, an arbitrary container, and every production-shaped target."""
    if len(container_id) != 64 or any(ch not in "0123456789abcdef" for ch in container_id):
        raise RuntimeError("--container must be the full 64-character fixture container ID")
    completed = subprocess.run(
        ["docker", "inspect", container_id], text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError("fixture container does not exist")
    details = json.loads(completed.stdout)[0]
    host = details["HostConfig"]
    mounts = details.get("Mounts", [])
    tmpfs = host.get("Tmpfs") or {}
    ports = details.get("NetworkSettings", {}).get("Ports") or {}
    expected = {
        "id": details["Id"] == container_id,
        "name": details.get("Name") == f"/{FIXTURE_NAME}",
        "image": details.get("Image") == FIXTURE_IMAGE,
        "label": details.get("Config", {}).get("Labels", {}).get("manacost.task") == FIXTURE_LABEL,
        "network": host.get("NetworkMode") == "none",
        "ports": ports == {},
        "mounts": mounts == [],
        "tmpfs": FIXTURE_TMPFS in tmpfs and "size=512m" in tmpfs[FIXTURE_TMPFS],
        "memory": host.get("Memory") == FIXTURE_MEMORY,
        "cpus": host.get("NanoCpus") == FIXTURE_CPUS,
        "running": details.get("State", {}).get("Running") is True,
    }
    failed = [name for name, passed in expected.items() if not passed]
    if failed:
        raise RuntimeError("refusing unsafe fixture target: " + ", ".join(failed))


def psql(sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = [
        "docker", "exec", "-i", CONTAINER_ID,
        "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
    ]
    completed = subprocess.run(command, input=sql, text=True, capture_output=True, check=False)
    if check and completed.returncode:
        raise AssertionError(f"psql failed:\n{completed.stderr}\nSQL:\n{sql}")
    return completed


def scalar(sql: str) -> str:
    completed = psql("\\pset tuples_only on\n\\pset format unaligned\n" + sql)
    values = [line for line in completed.stdout.splitlines() if line]
    if len(values) != 1:
        raise AssertionError(f"expected one scalar, got {values!r} for {sql}")
    return values[0]


def rows(sql: str) -> list[list[str]]:
    completed = psql("\\pset tuples_only on\n\\pset format unaligned\n\\pset fieldsep '\\t'\n" + sql)
    return [line.split("\t") for line in completed.stdout.splitlines() if line]


def pg_command() -> list[str]:
    # The module receives the same process tag shape that sync-shadow preserves.
    return [
        "docker", "exec", "-i", "-e", "PGAPPNAME=hs-shadow-fixture-incremental",
        CONTAINER_ID, "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
    ]


def load_stage_module() -> Any:
    if not STAGE_PATH.is_file():
        raise AssertionError(f"missing incremental stager: {STAGE_PATH}")
    spec = importlib.util.spec_from_file_location("incremental_shadow_stage", STAGE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load incremental stager module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    stage = getattr(module, "stage_analytics", None)
    if not callable(stage):
        raise AssertionError("stage-analytics.py must export stage_analytics(source, pg_command, full=False)")
    return module


def report_table(report: dict[str, Any], table: str) -> dict[str, Any]:
    tables = report.get("tables")
    if not isinstance(tables, list):
        raise AssertionError("stage report tables must be a list")
    matches = [entry for entry in tables if isinstance(entry, dict) and entry.get("table_name") == table]
    if len(matches) != 1:
        raise AssertionError(f"stage report has no unique entry for {table}: {tables!r}")
    return matches[0]


class IncrementalShadowFixture(unittest.TestCase):
    """One real PostgreSQL instance, isolated schemas and a fresh SQLite file per case."""

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="hs-shadow-incremental-"))
        self.source = self.workdir / "analytics.sqlite"
        self.module = load_stage_module()
        self.reset_target()
        self.create_source()

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def reset_target(self) -> None:
        # This is intentionally the first destructive fixture action, after require_fixture().
        definitions = [
            "DROP SCHEMA IF EXISTS analytics_stage CASCADE;",
            "DROP SCHEMA IF EXISTS analytics_sync_stage CASCADE;",
            "DROP SCHEMA IF EXISTS analytics CASCADE;",
            "DROP SCHEMA IF EXISTS platform CASCADE;",
            "CREATE SCHEMA analytics;",
            "CREATE SCHEMA platform;",
        ]
        for table in PUBLIC_TABLES:
            if table == "archetype_deck_cards":
                definitions.append(
                    "CREATE TABLE analytics.archetype_deck_cards "
                    "(archetype_id bigint NOT NULL, card_id bigint NOT NULL, value text, "
                    "PRIMARY KEY (archetype_id, card_id));"
                )
            elif table == "card_popularity_history":
                definitions.append(
                    "CREATE TABLE analytics.card_popularity_history "
                    "(id bigint PRIMARY KEY, source_id bigint NOT NULL, class text NOT NULL, "
                    "archetype text NOT NULL, card_name text NOT NULL, popularity real, recorded_at text NOT NULL, "
                    "UNIQUE (source_id, class, archetype, card_name, recorded_at));"
                )
            elif table == "hearthstone_patches":
                definitions.append(
                    "CREATE TABLE analytics.hearthstone_patches "
                    "(patch text PRIMARY KEY, payload_json jsonb);"
                )
            else:
                definitions.append(f"CREATE TABLE analytics.{quoted(table)} (id bigint PRIMARY KEY, value text);")
        psql("\n".join(definitions))

    def create_source(self) -> None:
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            for table in PUBLIC_TABLES:
                if table == "archetype_deck_cards":
                    connection.execute(
                        "CREATE TABLE archetype_deck_cards "
                        "(archetype_id INTEGER NOT NULL, card_id INTEGER NOT NULL, value TEXT, "
                        "PRIMARY KEY (archetype_id, card_id))"
                    )
                    connection.execute("INSERT INTO archetype_deck_cards VALUES (1, 1, 'initial composite')")
                elif table == "card_popularity_history":
                    connection.execute(
                        "CREATE TABLE card_popularity_history "
                        "(id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, class TEXT NOT NULL, "
                        "archetype TEXT NOT NULL, card_name TEXT NOT NULL, popularity REAL, recorded_at TEXT NOT NULL, "
                        "UNIQUE (source_id, class, archetype, card_name, recorded_at))"
                    )
                    connection.execute(
                        "INSERT INTO card_popularity_history VALUES (1, 7, 'MAGE', 'Arcane', 'Spark', ?, '2026-09-06T00:00:00Z')",
                        (0.123456789,),
                    )
                elif table == "hearthstone_patches":
                    connection.execute("CREATE TABLE hearthstone_patches (patch TEXT PRIMARY KEY, payload_json TEXT)")
                    connection.execute("INSERT INTO hearthstone_patches VALUES ('36.0', '{\"cards\": 1}')")
                else:
                    connection.execute(f"CREATE TABLE {quoted(table)} (id INTEGER PRIMARY KEY, value TEXT)")
                    connection.execute(f"INSERT INTO {quoted(table)} VALUES (1, ?)", (f"initial {table}",))
            # Private source data is deliberately present and must never be read or staged.
            connection.execute("CREATE TABLE api_tokens (id INTEGER PRIMARY KEY, secret TEXT)")
            connection.execute("INSERT INTO api_tokens VALUES (1, 'must-not-transfer')")
            connection.execute("CREATE TABLE api_token_usage_monthly (id INTEGER PRIMARY KEY, secret TEXT)")
            connection.execute("INSERT INTO api_token_usage_monthly VALUES (1, 'must-not-transfer')")
            connection.commit()
        finally:
            connection.close()

    def sqlite(self, statement: str, parameters: tuple[Any, ...] = ()) -> None:
        connection = sqlite3.connect(self.source)
        try:
            connection.execute(statement, parameters)
            connection.commit()
        finally:
            connection.close()

    def stage(self, *, full: bool = False) -> dict[str, Any]:
        # sync-shadow performs this pre-stage cleanup before importing the
        # module. Each direct module invocation gets the same fresh staging
        # namespace; canonical schemas and durable checkpoints are untouched.
        psql("DROP SCHEMA IF EXISTS analytics_stage CASCADE; DROP SCHEMA IF EXISTS analytics_sync_stage CASCADE;")
        result = self.module.stage_analytics(self.source, pg_command(), full=full)
        self.assertIsInstance(result, dict)
        for key in ("rows_read", "rows_staged", "chunks_changed", "tables"):
            self.assertIn(key, result)
        self.assertEqual({entry["table_name"] for entry in result["tables"]}, set(PUBLIC_TABLES))
        return result

    def merge(self, *, commit: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
        if not MERGE_PATH.is_file():
            raise AssertionError(f"missing incremental merge SQL: {MERGE_PATH}")
        ending = "COMMIT;" if commit else "ROLLBACK;"
        return psql(
            "BEGIN;\nSET LOCAL session_replication_role = replica;\n"
            + MERGE_PATH.read_text()
            + "\n"
            + ending
            + "\n",
            check=check,
        )

    def sync(self, *, full: bool = False) -> dict[str, Any]:
        report = self.stage(full=full)
        self.merge()
        return report

    def table_count(self, table: str) -> int:
        return int(scalar(f"SELECT count(*) FROM analytics.{quoted(table)};"))

    def checkpoints(self) -> list[list[str]]:
        return rows(
            "SELECT table_name, generation::text FROM platform.analytics_shadow_state ORDER BY table_name;"
        )

    def canonical_fingerprint(self) -> list[list[str]]:
        return rows(
            "SELECT c.relname, c.oid::text, pg_relation_filenode(c.oid)::text, "
            "(SELECT count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)::text "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'analytics' ORDER BY c.relname;"
        )

    def test_initial_full_stages_only_public_tables_then_merges_all(self) -> None:
        before = self.canonical_fingerprint()
        self.assertEqual(scalar("SELECT (to_regclass('platform.analytics_shadow_state') IS NULL)::text;"), "true")
        report = self.stage()
        self.assertGreater(report["rows_read"], 0)
        self.assertEqual(report["rows_read"], report["rows_staged"])
        self.assertTrue(all(report_table(report, table)["full_reconcile"] for table in PUBLIC_TABLES))
        self.assertEqual(before, self.canonical_fingerprint(), "staging must not mutate canonical analytics tables")
        self.assertEqual(scalar("SELECT (to_regclass('platform.analytics_shadow_state') IS NULL)::text;"), "true")
        staged = rows(
            "SELECT n.nspname, c.relname, c.relpersistence "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname IN ('analytics_stage', 'analytics_sync_stage') ORDER BY 1, 2;"
        )
        self.assertTrue(staged)
        self.assertTrue(all(row[2] == "u" for row in staged), "all staging relations must be UNLOGGED")
        self.assertFalse(any("api_token" in row[1] for row in staged))
        self.merge()
        self.assertEqual(self.checkpoints().__len__(), len(PUBLIC_TABLES))
        self.assertTrue(all(self.table_count(table) == 1 for table in PUBLIC_TABLES))

    def test_second_unchanged_run_stages_nothing_and_keeps_canonical_xmin(self) -> None:
        self.sync()
        before_xmin = scalar("SELECT xmin::text FROM analytics.decks WHERE id = 1;")
        before_state = self.checkpoints()
        report = self.sync()
        self.assertEqual(report["rows_staged"], 0)
        self.assertEqual(report["chunks_changed"], 0)
        self.assertEqual(scalar("SELECT xmin::text FROM analytics.decks WHERE id = 1;"), before_xmin)
        self.assertEqual(len(self.checkpoints()), len(before_state))

    def test_same_count_history_correction_is_transferred(self) -> None:
        self.sync()
        self.sqlite("UPDATE card_popularity_history SET popularity = ? WHERE id = 1", (0.987654321,))
        report = self.sync()
        self.assertGreater(report_table(report, "card_popularity_history")["rows_staged"], 0)
        self.assertEqual(
            scalar("SELECT encode(float4send(popularity), 'hex') FROM analytics.card_popularity_history WHERE id = 1;"),
            struct.pack("!f", 0.987654321).hex(),
        )

    def test_authoritative_delete_handles_last_row_and_empty_chunk(self) -> None:
        self.sync()
        self.sqlite("DELETE FROM decks WHERE id = 1")
        self.sync()
        self.assertEqual(self.table_count("decks"), 0)

    def test_signed_int64_boundary_buckets_insert_correct_and_delete(self) -> None:
        minimum = -(2**63)
        maximum = 2**63 - 1
        self.sync()
        self.sqlite("INSERT INTO decks VALUES (?, ?)", (minimum, "minimum signed key"))
        self.sqlite("INSERT INTO decks VALUES (?, ?)", (maximum, "maximum signed key"))
        inserted = self.sync()
        self.assertEqual(report_table(inserted, "decks")["chunks_changed"], 2)
        self.assertEqual(report_table(inserted, "decks")["rows_staged"], 2)
        self.assertEqual(scalar(f"SELECT value FROM analytics.decks WHERE id = {minimum};"), "minimum signed key")
        self.assertEqual(scalar(f"SELECT value FROM analytics.decks WHERE id = {maximum};"), "maximum signed key")

        self.sqlite("UPDATE decks SET value = ? WHERE id = ?", ("minimum corrected", minimum))
        self.sqlite("DELETE FROM decks WHERE id = ?", (maximum,))
        changed = self.sync()
        self.assertEqual(report_table(changed, "decks")["chunks_changed"], 2)
        self.assertEqual(report_table(changed, "decks")["rows_staged"], 1)
        self.assertEqual(scalar(f"SELECT value FROM analytics.decks WHERE id = {minimum};"), "minimum corrected")
        self.assertEqual(scalar(f"SELECT count(*) FROM analytics.decks WHERE id = {maximum};"), "0")

    def test_history_deletion_is_preserved(self) -> None:
        self.sync()
        self.sqlite("DELETE FROM card_popularity_history WHERE id = 1")
        self.sync()
        self.assertEqual(self.table_count("card_popularity_history"), 1)

    def test_composite_keys_and_string_primary_keys_reconcile(self) -> None:
        self.sync()
        self.sqlite("UPDATE archetype_deck_cards SET value = 'corrected composite' WHERE archetype_id = 1 AND card_id = 1")
        self.sqlite("UPDATE hearthstone_patches SET payload_json = '{\"cards\": 2}' WHERE patch = '36.0'")
        self.sync()
        self.assertEqual(scalar("SELECT value FROM analytics.archetype_deck_cards WHERE archetype_id = 1 AND card_id = 1;"), "corrected composite")
        self.assertEqual(scalar("SELECT payload_json->>'cards' FROM analytics.hearthstone_patches WHERE patch = '36.0';"), "2")

    def test_copy_preserves_null_empty_unicode_and_control_characters(self) -> None:
        self.sync()
        payload = "ёж\tnewline\nbackslash\\end"
        self.sqlite("UPDATE decks SET value = NULL WHERE id = 1")
        self.sqlite("UPDATE fetch_log SET value = '' WHERE id = 1")
        self.sqlite("UPDATE hsreplay_archetypes SET value = ? WHERE id = 1", (payload,))
        self.sync()
        self.assertEqual(scalar("SELECT (value IS NULL)::text FROM analytics.decks WHERE id = 1;"), "true")
        self.assertEqual(scalar("SELECT length(value)::text FROM analytics.fetch_log WHERE id = 1;"), "0")
        self.assertEqual(scalar("SELECT encode(convert_to(value, 'UTF8'), 'hex') FROM analytics.hsreplay_archetypes WHERE id = 1;"), payload.encode().hex())

    def test_float4_precision_jsonb_and_blank_json_normalization(self) -> None:
        self.sync()
        self.sqlite("UPDATE card_popularity_history SET popularity = ? WHERE id = 1", (0.123456789,))
        self.sqlite("UPDATE hearthstone_patches SET payload_json = '' WHERE patch = '36.0'")
        self.sync()
        self.assertEqual(
            scalar("SELECT encode(float4send(popularity), 'hex') FROM analytics.card_popularity_history WHERE id = 1;"),
            struct.pack("!f", 0.123456789).hex(),
        )
        self.assertEqual(scalar("SELECT (payload_json IS NULL)::text FROM analytics.hearthstone_patches WHERE patch = '36.0';"), "true")

    def test_missing_source_or_shape_mismatch_fails_without_canonical_or_checkpoint_advance(self) -> None:
        self.sync()
        baseline_rows = self.rows_state()
        baseline_checkpoints = self.checkpoints()
        self.sqlite("DROP TABLE fetch_log")
        with self.assertRaises(Exception):
            self.stage()
        self.assertEqual(self.rows_state(), baseline_rows)
        self.assertEqual(self.checkpoints(), baseline_checkpoints)

    def test_shape_mismatch_fails_without_canonical_or_checkpoint_advance(self) -> None:
        self.sync()
        baseline_rows = self.rows_state()
        baseline_checkpoints = self.checkpoints()
        self.sqlite("ALTER TABLE decks ADD COLUMN unexpected TEXT")
        with self.assertRaises(Exception):
            self.stage()
        self.assertEqual(self.rows_state(), baseline_rows)
        self.assertEqual(self.checkpoints(), baseline_checkpoints)

    def test_malformed_json_copy_fails_atomically(self) -> None:
        self.sync()
        baseline_rows = self.rows_state()
        baseline_checkpoints = self.checkpoints()
        self.sqlite("UPDATE hearthstone_patches SET payload_json = '{invalid' WHERE patch = '36.0'")
        failed = False
        try:
            self.stage()
            self.merge()
        except Exception:
            failed = True
        self.assertTrue(failed, "invalid JSON must fail stage or merge")
        self.assertEqual(self.rows_state(), baseline_rows)
        self.assertEqual(self.checkpoints(), baseline_checkpoints)

    def test_merge_rollback_leaves_no_checkpoint_and_retry_succeeds(self) -> None:
        self.stage()
        self.merge(commit=False)
        self.assertEqual(scalar("SELECT (to_regclass('platform.analytics_shadow_state') IS NULL)::text;"), "true")
        self.assertEqual(self.table_count("decks"), 0)
        self.merge()
        self.assertEqual(self.table_count("decks"), 1)
        self.assertEqual(len(self.checkpoints()), len(PUBLIC_TABLES))

    def test_truncated_unlogged_stage_neither_deletes_nor_advances_checkpoint(self) -> None:
        self.sync()
        baseline_rows = self.rows_state()
        baseline_checkpoints = self.checkpoints()
        # An update makes the staged row mandatory; truncating it must not be
        # mistaken for an authoritative empty source range.
        self.sqlite("UPDATE decks SET value = 'must-not-publish' WHERE id = 1")
        self.stage()
        psql("TRUNCATE analytics_stage.decks;")
        failed = self.merge(check=False)
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertEqual(self.rows_state(), baseline_rows)
        self.assertEqual(self.checkpoints(), baseline_checkpoints)

    def test_stale_generation_cannot_publish_twice(self) -> None:
        self.sync()
        self.sqlite("UPDATE decks SET value = 'source correction' WHERE id = 1")
        self.stage()
        psql("UPDATE platform.analytics_shadow_state SET generation = 'stale-' || generation WHERE table_name = 'decks';")
        failed = self.merge(check=False)
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertEqual(scalar("SELECT value FROM analytics.decks WHERE id = 1;"), "initial decks")

    def test_read_snapshot_is_not_mixed_with_a_concurrent_sqlite_writer(self) -> None:
        # The writer begins after the stage reader has already executed two reads in its transaction.
        # A correct single read snapshot therefore publishes the old pair, never one old and one new value.
        self.sqlite("INSERT INTO decks VALUES (9000, 'old-9000')")
        original_connect = self.module.sqlite3.connect
        raw_connect = sqlite3.connect
        second_read = threading.Event()
        writer_done = threading.Event()
        trace_state: dict[int, dict[str, int | bool]] = {}

        def observed_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            state: dict[str, int | bool] = {"begun": False, "reads": 0}
            trace_state[id(connection)] = state

            def trace(statement: str) -> None:
                upper = statement.lstrip().upper()
                if upper.startswith("BEGIN"):
                    state["begun"] = True
                elif state["begun"] and (upper.startswith("SELECT") or upper.startswith("PRAGMA")):
                    state["reads"] = int(state["reads"]) + 1
                    if state["reads"] == 2:
                        second_read.set()

            connection.set_trace_callback(trace)
            return connection

        def writer() -> None:
            self.assertTrue(second_read.wait(10), "stager never opened a consistent read transaction")
            connection = raw_connect(self.source)
            try:
                connection.execute("UPDATE decks SET value = 'new-1' WHERE id = 1")
                connection.execute("UPDATE decks SET value = 'new-9000' WHERE id = 9000")
                connection.commit()
            finally:
                connection.close()
                writer_done.set()

        worker = threading.Thread(target=writer, daemon=True)
        worker.start()
        self.module.sqlite3.connect = observed_connect
        try:
            self.stage()
        finally:
            self.module.sqlite3.connect = original_connect
        worker.join(10)
        self.assertTrue(writer_done.is_set())
        self.merge()
        published = rows("SELECT id::text, value FROM analytics.decks ORDER BY id;")
        self.assertEqual(published, [["1", "initial decks"], ["9000", "old-9000"]])

    def test_force_full_repairs_same_count_out_of_band_target_edit(self) -> None:
        self.sync()
        psql("UPDATE analytics.decks SET value = 'manual target mutation' WHERE id = 1;")
        normal = self.sync()
        self.assertEqual(normal["rows_staged"], 0)
        self.assertEqual(scalar("SELECT value FROM analytics.decks WHERE id = 1;"), "manual target mutation")
        forced = self.sync(full=True)
        self.assertTrue(report_table(forced, "decks")["full_reconcile"])
        self.assertEqual(scalar("SELECT value FROM analytics.decks WHERE id = 1;"), "initial decks")

    def test_missing_checkpoint_source_inode_and_target_storage_or_count_mismatch_force_full(self) -> None:
        self.sync()
        psql("DELETE FROM platform.analytics_shadow_state WHERE table_name = 'decks';")
        missing_checkpoint = self.stage()
        self.assertTrue(report_table(missing_checkpoint, "decks")["full_reconcile"])
        self.merge()

        psql("DELETE FROM analytics.decks WHERE id = 1;")
        count_mismatch = self.sync()
        self.assertTrue(report_table(count_mismatch, "decks")["full_reconcile"])
        self.assertEqual(self.table_count("decks"), 1)

        psql("VACUUM FULL analytics.decks;")
        storage_changed = self.stage()
        self.assertTrue(report_table(storage_changed, "decks")["full_reconcile"])
        self.merge()

        replacement = self.workdir / "replacement.sqlite"
        old = sqlite3.connect(self.source)
        new = sqlite3.connect(replacement)
        try:
            old.backup(new)
        finally:
            new.close()
            old.close()
        for suffix in ("-wal", "-shm"):
            candidate = self.source.with_name(self.source.name + suffix)
            if candidate.exists():
                candidate.unlink()
        os.replace(replacement, self.source)
        source_replaced = self.stage()
        self.assertTrue(all(report_table(source_replaced, table)["full_reconcile"] for table in PUBLIC_TABLES))
        self.merge()

    def test_ordinary_recovery_preserves_surviving_state_when_chunk_checkpoint_is_lost(self) -> None:
        self.sync()
        before_generation = scalar(
            "SELECT generation FROM platform.analytics_shadow_state WHERE table_name = 'decks';"
        )
        psql("DROP TABLE platform.analytics_shadow_chunks;")
        self.sqlite("UPDATE decks SET value = 'ordinary recovery after chunk loss' WHERE id = 1")
        recovered = self.sync()
        self.assertTrue(report_table(recovered, "decks")["full_reconcile"])
        self.assertEqual(
            scalar("SELECT value FROM analytics.decks WHERE id = 1;"),
            "ordinary recovery after chunk loss",
        )
        self.assertNotEqual(
            scalar("SELECT generation FROM platform.analytics_shadow_state WHERE table_name = 'decks';"),
            before_generation,
        )
        self.assertEqual(
            scalar("SELECT count(*) FROM platform.analytics_shadow_chunks WHERE table_name = 'decks';"),
            "1",
        )

    def test_explicit_full_recovery_preserves_surviving_state_when_chunk_checkpoint_is_lost(self) -> None:
        self.sync()
        psql("DROP TABLE platform.analytics_shadow_chunks;")
        self.sqlite("UPDATE decks SET value = 'explicit recovery after chunk loss' WHERE id = 1")
        recovered = self.sync(full=True)
        self.assertTrue(report_table(recovered, "decks")["full_reconcile"])
        self.assertEqual(
            scalar("SELECT value FROM analytics.decks WHERE id = 1;"),
            "explicit recovery after chunk loss",
        )
        self.assertEqual(
            scalar("SELECT count(*) FROM platform.analytics_shadow_chunks WHERE table_name = 'decks';"),
            "1",
        )

    def test_recovery_rebuilds_missing_state_when_chunk_checkpoint_survives(self) -> None:
        self.sync()
        psql("DROP TABLE platform.analytics_shadow_state;")
        self.sqlite("UPDATE decks SET value = 'recovered after state loss' WHERE id = 1")
        recovered = self.sync()
        self.assertTrue(report_table(recovered, "decks")["full_reconcile"])
        self.assertEqual(scalar("SELECT value FROM analytics.decks WHERE id = 1;"), "recovered after state loss")
        self.assertEqual(
            scalar("SELECT count(*) FROM platform.analytics_shadow_state WHERE table_name = 'decks';"),
            "1",
        )

    def test_process_tag_contract_is_preserved_by_the_synchronizer(self) -> None:
        self.assertTrue(SYNC_PATH.is_file())
        synchronizer = SYNC_PATH.read_text()
        self.assertIn("PGAPPNAME=", synchronizer)
        self.assertIn("syncApplicationName()", synchronizer)

    def rows_state(self) -> list[list[str]]:
        snapshot: list[list[str]] = []
        for table in PUBLIC_TABLES:
            if table == "archetype_deck_cards":
                order = "archetype_id, card_id"
            elif table == "hearthstone_patches":
                order = "patch"
            else:
                order = "id"
            value = scalar(
                "SELECT coalesce(jsonb_agg(to_jsonb(source_row) ORDER BY " + order + ")::text, '[]') "
                + f"FROM analytics.{quoted(table)} AS source_row;"
            )
            snapshot.append([table, value])
        return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="full ID of hs-shadow-fixture-20260906")
    args, selected = parser.parse_known_args()
    global CONTAINER_ID
    CONTAINER_ID = args.container
    try:
        require_fixture(CONTAINER_ID)
    except RuntimeError as error:
        parser.error(str(error))
    unittest.main(argv=[sys.argv[0], *selected], verbosity=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
