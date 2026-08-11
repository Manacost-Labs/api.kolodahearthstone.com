from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.hsreplay_archetypes_db import SOURCE, export_latest_archetypes_json
from app.source_state import SourceState
from app.sources import Source
from app.stale_monitor import find_stale_sources
from app.storage import load_dataset, save_dataset, save_status


class HsreplayArchetypesPublicationTest(unittest.TestCase):
    def test_export_advances_canonical_pipeline_dataset_and_freshness(self) -> None:
        source = Source(
            id=SOURCE,
            url="https://hsreplay.net/meta/",
            site="hsreplay",
            category="meta",
            kind="pipeline",
            stale_hours=120,
        )
        page = {
            "total": 1,
            "limit": 500,
            "offset": 0,
            "archetypes": [{"archetype_id": 874, "name": "Troublemaker Rogue"}],
        }
        latest = {"id": 15, "state": "ok", "archetypes_total": 1, "archetypes_ok": 1}

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("app.storage.data_dir", return_value=root),
                patch("app.hsreplay_archetypes_db.data_dir", return_value=root),
                patch("app.db.store_dataset_to_db"),
                patch("app.hsreplay_archetypes_db.latest_run", return_value=latest),
                patch("app.hsreplay_archetypes_db.list_archetype_snapshots", return_value=page),
                patch("app.stale_monitor.SOURCES", [source]),
            ):
                save_dataset(
                    SOURCE,
                    {
                        "source_id": SOURCE,
                        "fetched_at": "2026-01-01T00:00:00+00:00",
                        "data": {"structured": {"type": "hsreplay_archetype_database"}},
                    },
                )
                save_status(
                    SOURCE,
                    {
                        "source_id": SOURCE,
                        "state": SourceState.OK,
                        "fetched_at": "2026-01-01T00:00:00+00:00",
                    },
                )

                compatibility_path = export_latest_archetypes_json()
                canonical = load_dataset(SOURCE)
                assert canonical is not None
                save_status(
                    SOURCE,
                    {
                        "source_id": SOURCE,
                        "state": SourceState.OK,
                        "fetched_at": canonical["fetched_at"],
                    },
                )
                stale = find_stale_sources(include_ok=True)

            self.assertEqual(
                compatibility_path,
                root / "datasets" / "hsreplay_archetypes_db_latest.json",
            )
            self.assertTrue(compatibility_path.is_file())
            self.assertEqual(canonical["source_id"], SOURCE)
            self.assertEqual(canonical["backend"], "hsreplay_api")
            self.assertEqual(
                canonical["data"]["structured"],
                {"type": "hsreplay_archetype_database", "latest_run": latest, **page},
            )
            self.assertEqual(stale, [])

    def test_legacy_export_failure_does_not_undo_canonical_publication(self) -> None:
        page = {"total": 0, "limit": 500, "offset": 0, "archetypes": []}
        latest = {"id": 16, "state": "ok", "archetypes_total": 0}
        with (
            patch("app.hsreplay_archetypes_db.latest_run", return_value=latest),
            patch(
                "app.hsreplay_archetypes_db.list_archetype_snapshots",
                return_value=page,
            ),
            patch("app.hsreplay_archetypes_db.data_dir", return_value=Path("/tmp")),
            patch("app.storage.save_dataset") as save_canonical,
            patch("app.storage.write_json", side_effect=OSError("legacy disk error")),
            patch("app.hsreplay_archetypes_db.log_action") as log_action,
        ):
            path = export_latest_archetypes_json()

        self.assertEqual(path, Path("/tmp/datasets/hsreplay_archetypes_db_latest.json"))
        save_canonical.assert_called_once()
        self.assertTrue(
            any(
                call.args
                and call.args[0] == "hsreplay.archetype_db.legacy_export.fail"
                for call in log_action.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
