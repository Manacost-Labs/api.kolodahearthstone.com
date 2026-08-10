from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import cli
from app.resource_locks import ResourceLocked
from app.sources import Source


class CliTest(unittest.TestCase):
    def test_refresh_source_uses_global_source_map_after_freshness_imports(self) -> None:
        with patch("app.cli.refresh_sources", return_value=[]) as refresh:
            exit_code = cli.main(["refresh", "--source", "hsguru_meta_wild_top_legend"])

        self.assertEqual(exit_code, 0)
        refresh.assert_called_once_with(["hsguru_meta_wild_top_legend"], tier=None)

    def test_refresh_require_all_ok_returns_failure_for_rejected_source(self) -> None:
        results = [
            {"source_id": "hsreplay_arena_cards_advanced", "state": "ok"},
            {"source_id": "heartharena_tierlist", "state": "parse_error"},
            {"source_id": "firestone_arena_cards_normal", "state": "ok"},
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--source",
                    "heartharena_tierlist",
                    "--source",
                    "firestone_arena_cards_normal",
                    "--require-all-ok",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_refresh_require_all_ok_rejects_cached_success(self) -> None:
        results = [
            {
                "source_id": "hsreplay_arena_cards_advanced",
                "state": "ok",
                "serving_cached_dataset": True,
            }
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--require-all-ok",
                ]
            )

        self.assertEqual(exit_code, 1)

    def test_refresh_require_all_ok_accepts_fresh_successes(self) -> None:
        results = [
            {"source_id": "hsreplay_arena_cards_advanced", "state": "ok"},
            {"source_id": "heartharena_tierlist", "state": "ok"},
            {"source_id": "firestone_arena_cards_normal", "state": "ok"},
        ]
        with patch("app.cli.refresh_sources", return_value=results):
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "hsreplay_arena_cards_advanced",
                    "--source",
                    "heartharena_tierlist",
                    "--source",
                    "firestone_arena_cards_normal",
                    "--require-all-ok",
                ]
            )

        self.assertEqual(exit_code, 0)

    def test_load_env_file_overrides_stale_hsguru_backend_export(self) -> None:
        with TemporaryDirectory() as td:
            env_path = Path(td) / "hs.env"
            env_path.write_text(
                "HS_HSGURU_FETCH_BACKENDS=flaresolverr,scrapling,curl_cffi\n"
                "VICIOUS_SYNDICATE_STORAGE_PATH=/tmp/vicious-session.json\n",
                encoding="utf-8",
            )
            old = os.environ.get("HS_HSGURU_FETCH_BACKENDS")
            old_vicious = os.environ.get("VICIOUS_SYNDICATE_STORAGE_PATH")
            os.environ["HS_HSGURU_FETCH_BACKENDS"] = "patchright"
            os.environ["VICIOUS_SYNDICATE_STORAGE_PATH"] = "/tmp/stale.json"
            try:
                cli.load_env_file(env_path)
                self.assertEqual(
                    os.environ["HS_HSGURU_FETCH_BACKENDS"],
                    "flaresolverr,scrapling,curl_cffi",
                )
                self.assertEqual(
                    os.environ["VICIOUS_SYNDICATE_STORAGE_PATH"],
                    "/tmp/vicious-session.json",
                )
            finally:
                if old is None:
                    os.environ.pop("HS_HSGURU_FETCH_BACKENDS", None)
                else:
                    os.environ["HS_HSGURU_FETCH_BACKENDS"] = old
                if old_vicious is None:
                    os.environ.pop("VICIOUS_SYNDICATE_STORAGE_PATH", None)
                else:
                    os.environ["VICIOUS_SYNDICATE_STORAGE_PATH"] = old_vicious

    def test_quality_check_returns_nonzero_for_invalid_cached_dataset(self) -> None:
        source = Source("bad_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"bad_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch("app.storage.load_dataset", return_value={"data": {}}):
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 1)

    def test_quality_check_passes_valid_cached_dataset(self) -> None:
        source = Source("ok_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"ok_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch(
            "app.storage.load_dataset",
            return_value={"data": {"title": "ok", "structured": {"type": "legacy_dataset", "rows": []}}},
        ):
            with patch("app.scrapers.quality.validate_parsed_data", return_value=(True, "ok")):
                exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 0)

    def test_quality_check_warn_band_does_not_fail(self) -> None:
        source = Source("warn_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"warn_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch(
            "app.storage.load_dataset",
            return_value={"data": {"title": "ok", "structured": {"type": "legacy_dataset", "rows": []}}},
        ), patch("app.scrapers.quality.validate_parsed_data", return_value=(True, "ok")), patch(
            "app.scrapers.quality.quality_metrics",
            return_value={"quality_score": 0.90},
        ):
            exit_code = cli.main(["quality-check", "--min-quality-score", "0.85", "--warn-quality-score", "0.95"])

        self.assertEqual(exit_code, 0)

    def test_quality_check_reports_validation_exception_as_bad_source(self) -> None:
        source = Source("raises_source", "https://example.test", "hsreplay", "arena")
        with patch("app.cli.SOURCE_BY_ID", {"raises_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "test"},
        ), patch(
            "app.storage.load_dataset",
            return_value={"data": {"title": "ok", "structured": {"type": "arena_class_matrix"}}},
        ), patch(
            "app.scrapers.quality.validate_parsed_data",
            side_effect=ValueError("bad deck_class"),
        ):
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 1)

    def test_quality_check_uses_pipeline_status_and_structured_payload(self) -> None:
        source = Source(
            "pipeline_source",
            "https://example.test",
            "hsreplay",
            "meta",
            kind="pipeline",
        )
        with patch("app.cli.SOURCE_BY_ID", {"pipeline_source": source}), patch(
            "app.storage.load_status",
            return_value={"state": "ok", "backend": "pipeline"},
        ), patch(
            "app.storage.load_dataset",
            return_value={
                "data": {
                    "structured": {
                        "type": "hsreplay_archetype_database",
                        "archetypes": [],
                    }
                }
            },
        ), patch("app.scrapers.quality.validate_parsed_data") as generic_validate:
            exit_code = cli.main(["quality-check"])

        self.assertEqual(exit_code, 0)
        generic_validate.assert_not_called()

    def test_rebuild_index_uses_derived_resource_lock(self) -> None:
        with patch("app.resource_locks.ResourceLockSet") as resource_locks, patch(
            "app.firecrawl_map.build_hsreplay_index",
            return_value={"ok": True},
        ) as build:
            exit_code = cli.main(["rebuild-hsreplay-index"])

        self.assertEqual(exit_code, 0)
        resource_locks.assert_called_once_with(["derived:hsreplay-index"])
        resource_locks.return_value.__enter__.assert_called_once_with()
        build.assert_called_once_with()

    def test_rebuild_index_reports_expected_lock_overlap_as_skipped(self) -> None:
        output = io.StringIO()
        owner = {
            "pid": 321,
            "resource_id": "derived:hsreplay-index",
            "run_id": "owner-run",
        }
        with patch("app.resource_locks.ResourceLockSet") as resource_locks, patch(
            "app.firecrawl_map.build_hsreplay_index",
        ) as build:
            resource_locks.return_value.__enter__.side_effect = ResourceLocked(
                "derived:hsreplay-index",
                owner,
            )
            with redirect_stdout(output):
                exit_code = cli.main(["rebuild-hsreplay-index"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "ok": True,
                "state": "locked",
                "skipped": True,
                "reason": "resource_locked",
                "locked_resource": "derived:hsreplay-index",
                "owner": owner,
            },
        )
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
