from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app import cli
from app.hsreplay_archetypes_db import (
    _refresh_hsreplay_archetype_database_unlocked,
    _save_source_status,
    refresh_hsreplay_archetype_database,
)
from app.hsreplay_bg_hero_details import refresh_bg_hero_details
from app.parser_control import _run_pipeline_source
from app.resource_locks import ResourceLocked


class PipelineControlPlaneTest(unittest.IsolatedAsyncioTestCase):
    async def test_control_plane_runs_fun_decks_pipeline(self) -> None:
        upstream = {
            "ok": True,
            "source_id": "hsguru_fun_decks",
            "fun_retained": 18,
            "fetched_at": "2026-08-11T10:00:00+00:00",
        }
        with patch(
            "app.fun_decks.refresh_fun_decks",
            return_value=upstream,
        ) as refresh:
            result = await _run_pipeline_source("hsguru_fun_decks")

        refresh.assert_called_once_with()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["rows_total"], 18)


class PipelineResourceLockTest(unittest.IsolatedAsyncioTestCase):
    async def test_archetype_refresh_reports_lock_contention(self) -> None:
        lock = MagicMock()
        lock.acquire.side_effect = ResourceLocked(
            "hsreplay_archetypes",
            {"operation": "another_refresh"},
        )
        with patch(
            "app.hsreplay_archetypes_db.ResourceLockSet",
            return_value=lock,
        ):
            result = await refresh_hsreplay_archetype_database()

        self.assertTrue(result["ok"])
        self.assertFalse(result["published"])
        self.assertEqual(result["state"], "locked")
        self.assertEqual(result["reason"], "resource_locked")
        lock.release.assert_not_called()

    async def test_bg_hero_refresh_reports_lock_contention(self) -> None:
        lock = MagicMock()
        lock.acquire.side_effect = ResourceLocked(
            "hsreplay_battlegrounds_hero_details"
        )
        with patch(
            "app.hsreplay_bg_hero_details.ResourceLockSet",
            return_value=lock,
        ) as lock_set:
            result = await refresh_bg_hero_details()

        lock_set.assert_called_once_with(
            [
                "hsreplay_battlegrounds_hero_details",
                "hsreplay_battlegrounds_heroes",
            ],
            metadata={"operation": "refresh_bg_hero_details"},
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["published"])
        self.assertEqual(result["state"], "locked")
        self.assertEqual(result["reason"], "resource_locked")
        lock.release.assert_not_called()

    async def test_limited_archetype_refresh_is_read_only_diagnostic(self) -> None:
        lock = MagicMock()
        begin_run = MagicMock()
        store_snapshot = MagicMock()
        finish_run = MagicMock()
        export = MagicMock(return_value=Path("/tmp/should-not-exist.json"))
        snapshot = {"summary": {}, "raw_summary": {}}
        with (
            patch(
                "app.hsreplay_archetypes_db.ResourceLockSet",
                return_value=lock,
            ),
            patch(
                "app.hsreplay_archetypes_db._archetypes_from_index",
                return_value=[{"archetype_id": 42, "name": "Test Archetype"}],
            ),
            patch(
                "app.hsreplay_archetypes_db._card_indexes",
                return_value=({}, {}),
            ),
            patch(
                "app.hsreplay_archetypes_db._fetch_common_payloads",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_archetypes_db._archetype_name_map",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_archetypes_db._fetch_archetype_payloads",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_archetypes_db._build_snapshot",
                return_value=snapshot,
            ),
            patch("app.hsreplay_archetypes_db._begin_run", begin_run),
            patch(
                "app.hsreplay_archetypes_db.store_archetype_snapshot",
                store_snapshot,
            ),
            patch("app.hsreplay_archetypes_db._finish_run", finish_run),
            patch("app.hsreplay_archetypes_db.export_latest_archetypes_json", export),
            patch("app.hsreplay_archetypes_db.log_action"),
        ):
            result = await refresh_hsreplay_archetype_database(limit=1)

        self.assertTrue(result["ok"])
        self.assertTrue(result["diagnostic"])
        self.assertFalse(result["published"])
        self.assertEqual(result["state"], "diagnostic")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["archetypes_ok"], 1)
        begin_run.assert_not_called()
        store_snapshot.assert_not_called()
        finish_run.assert_not_called()
        export.assert_not_called()
        lock.release.assert_called_once_with()

    async def test_complete_archetype_refresh_exports_while_lock_is_held(self) -> None:
        events: list[str] = []
        lock = MagicMock()
        lock.acquire.side_effect = lambda: events.append("acquire")
        lock.release.side_effect = lambda: events.append("release")

        async def refresh_unlocked(**_kwargs: object) -> dict[str, object]:
            events.append("refresh")
            return {
                "ok": True,
                "state": "ok",
                "published": True,
                "run_id": 5,
                "archetypes_ok": 12,
            }

        def export() -> Path:
            events.append("export")
            return Path("/tmp/archetypes.json")

        with (
            patch(
                "app.hsreplay_archetypes_db.ResourceLockSet",
                return_value=lock,
            ),
            patch(
                "app.hsreplay_archetypes_db._refresh_hsreplay_archetype_database_unlocked",
                side_effect=refresh_unlocked,
            ),
            patch(
                "app.hsreplay_archetypes_db.export_latest_archetypes_json",
                side_effect=export,
            ),
            patch(
                "app.hsreplay_archetypes_db._save_source_status",
                side_effect=lambda *_args, **_kwargs: events.append("status"),
            ),
        ):
            result = await refresh_hsreplay_archetype_database()

        self.assertEqual(
            events,
            ["acquire", "refresh", "export", "status", "release"],
        )
        self.assertEqual(result["export_path"], "/tmp/archetypes.json")

    async def test_partial_archetype_refresh_keeps_last_good_export(self) -> None:
        lock = MagicMock()
        partial = {
            "ok": False,
            "state": "partial",
            "published": False,
            "serving_cached_dataset": True,
        }
        with (
            patch(
                "app.hsreplay_archetypes_db.ResourceLockSet",
                return_value=lock,
            ),
            patch(
                "app.hsreplay_archetypes_db._refresh_hsreplay_archetype_database_unlocked",
                new=AsyncMock(return_value=partial),
            ),
            patch(
                "app.hsreplay_archetypes_db.export_latest_archetypes_json"
            ) as export,
        ):
            result = await refresh_hsreplay_archetype_database()

        self.assertFalse(result["ok"])
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])
        export.assert_not_called()
        lock.release.assert_called_once_with()

    async def test_archetype_export_failure_overwrites_premature_success(self) -> None:
        lock = MagicMock()
        complete = {
            "ok": True,
            "state": "ok",
            "published": True,
            "run_id": 15,
            "archetypes_ok": 20,
            "errors": [],
        }
        with (
            patch(
                "app.hsreplay_archetypes_db.ResourceLockSet",
                return_value=lock,
            ),
            patch(
                "app.hsreplay_archetypes_db._refresh_hsreplay_archetype_database_unlocked",
                new=AsyncMock(return_value=complete),
            ),
            patch(
                "app.hsreplay_archetypes_db.export_latest_archetypes_json",
                side_effect=OSError("disk unavailable"),
            ),
            patch("app.hsreplay_archetypes_db._finish_run") as finish_run,
            patch(
                "app.hsreplay_archetypes_db._cached_source_dataset",
                return_value={"fetched_at": "old"},
            ),
        ):
            result = await refresh_hsreplay_archetype_database()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])
        self.assertEqual(result["errors"], [{"error": "publication failed: OSError"}])
        finish_run.assert_called_once_with(
            15,
            state="failed",
            archetypes_ok=20,
            error="publication failed: OSError",
        )

    async def test_partial_archetype_run_is_not_marked_published(self) -> None:
        archetypes = [
            {"archetype_id": 41, "name": "Complete"},
            {"archetype_id": 42, "name": "Failed"},
        ]
        snapshot = {"summary": {}, "raw_summary": {}}
        with (
            patch(
                "app.hsreplay_archetypes_db._archetypes_from_index",
                return_value=archetypes,
            ),
            patch("app.hsreplay_archetypes_db._card_indexes", return_value=({}, {})),
            patch(
                "app.hsreplay_archetypes_db._fetch_common_payloads",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_archetypes_db._archetype_name_map",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_archetypes_db._fetch_archetype_payloads",
                new=AsyncMock(side_effect=[{}, RuntimeError("upstream failed")]),
            ),
            patch(
                "app.hsreplay_archetypes_db._build_snapshot",
                return_value=snapshot,
            ),
            patch("app.hsreplay_archetypes_db._begin_run", return_value=7),
            patch("app.hsreplay_archetypes_db.store_archetype_snapshot", return_value=8),
            patch("app.hsreplay_archetypes_db._finish_run"),
            patch("app.hsreplay_archetypes_db.load_hsreplay_index", return_value={}),
            patch("app.storage.load_dataset", return_value={"fetched_at": "old"}),
            patch("app.hsreplay_archetypes_db.log_action"),
        ):
            result = await _refresh_hsreplay_archetype_database_unlocked()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "partial")
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])

    async def test_empty_archetype_index_never_publishes(self) -> None:
        with (
            patch(
                "app.hsreplay_archetypes_db._archetypes_from_index",
                return_value=[],
            ),
            patch("app.hsreplay_archetypes_db._begin_run", return_value=9),
            patch("app.hsreplay_archetypes_db._finish_run") as finish_run,
            patch("app.storage.load_dataset", return_value={"fetched_at": "old"}),
        ):
            result = await _refresh_hsreplay_archetype_database_unlocked()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])
        finish_run.assert_called_once_with(
            9,
            state="failed",
            archetypes_ok=0,
            error="HSReplay archetype index is empty; refusing to publish",
        )


class ArchetypeStatusTest(unittest.TestCase):
    def test_failed_run_status_serves_previous_dataset_without_false_freshness(self) -> None:
        cached = {
            "fetched_at": "2026-08-10T09:00:00+00:00",
            "backend": "hsreplay_api",
        }
        with (
            patch("app.storage.load_dataset", return_value=cached),
            patch("app.storage.save_status") as save_status,
        ):
            _save_source_status(
                12,
                run_state="partial",
                archetypes_ok=10,
                error="one source failed",
            )

        status = save_status.call_args.args[1]
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["fetched_at"], cached["fetched_at"])
        self.assertTrue(status["serving_cached_dataset"])
        self.assertEqual(status["last_refresh_state"], "partial")


class FunDeckResourceLockTest(unittest.TestCase):
    def test_fun_decks_refresh_reports_lock_contention(self) -> None:
        from app.fun_decks import refresh_fun_decks

        lock = MagicMock()
        lock.acquire.side_effect = ResourceLocked("hsguru_fun_decks")
        with patch("app.fun_decks.ResourceLockSet", return_value=lock):
            result = refresh_fun_decks()

        self.assertTrue(result["ok"])
        self.assertFalse(result["published"])
        self.assertEqual(result["state"], "locked")
        self.assertEqual(result["reason"], "resource_locked")
        lock.release.assert_not_called()


class ScheduledPipelineLockExitTest(unittest.TestCase):
    LOCKED = {
        "ok": True,
        "published": False,
        "state": "locked",
        "skipped": True,
        "reason": "resource_locked",
    }

    def test_archetype_lock_is_handled_degradation(self) -> None:
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsreplay_archetypes_db.refresh_hsreplay_archetype_database",
                new=AsyncMock(return_value=dict(self.LOCKED)),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["refresh-hsreplay-archetypes", "--scheduled"])

        self.assertEqual(exit_code, 10)

    def test_bg_hero_lock_is_handled_degradation(self) -> None:
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
                new=AsyncMock(return_value=dict(self.LOCKED)),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["refresh-bg-hero-details", "--scheduled"])

        self.assertEqual(exit_code, 10)

    def test_fun_deck_lock_is_handled_degradation(self) -> None:
        with (
            patch("app.parser_control.is_source_scheduled_enabled", return_value=True),
            patch(
                "app.fun_decks.refresh_fun_decks",
                return_value=dict(self.LOCKED),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = cli.main(["refresh-fun-decks", "--scheduled"])

        self.assertEqual(exit_code, 10)


if __name__ == "__main__":
    unittest.main()
