from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.fetcher import _save_dataset_with_checks
from app.main import app, public_dataset_payload
from app.parser_control import (
    ParserControlStore,
    load_resolved_public_dataset,
    resolve_public_dataset,
)
from app.post_patch_policy import (
    POST_PATCH_BASELINE_LABEL,
    capture_publication_policy,
)
from app.sources import SOURCE_BY_ID
from app.storage import dataset_path, save_baseline_once, write_json


class ParserControlPublishingTest(unittest.TestCase):
    def test_internal_consumers_resolve_stable_hsguru_baseline(self) -> None:
        from app.fun_decks import build_archetype_popularity
        from app.hsguru_decks import _meta_archetypes

        source_id = "hsguru_meta_standard_legend"
        stable = {
            "fetched_at": "2026-08-01T00:00:00+00:00",
            "data": {
                "tables": [{"rows": [["Stable Archetype", "52%", "2%"]]}],
                "structured": {
                    "type": "meta",
                    "strategies": [
                        {
                            "Archetype": "Stable Archetype",
                            "Winrate↓": "52%",
                            "Popularity": "2%",
                        }
                    ],
                },
            },
        }
        provisional = {
            "fetched_at": "2026-08-02T00:00:00+00:00",
            "data": {
                "tables": [{"rows": [["Early Archetype", "51%", "1%"]]}],
                "structured": {
                    "type": "meta",
                    "strategies": [
                        {
                            "Archetype": "Early Archetype",
                            "Winrate↓": "51%",
                            "Popularity": "1%",
                        }
                    ],
                    "provisional": True,
                    "data_phase": "post_patch_early",
                },
            },
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ):
            save_baseline_once(source_id, POST_PATCH_BASELINE_LABEL, stable)
            write_json(dataset_path(source_id), provisional)
            ParserControlStore(Path(directory)).update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="Раннее окно завершено",
                updated_by="test",
            )

            resolved = load_resolved_public_dataset(source_id)
            archetypes = _meta_archetypes("standard")
            popularity = build_archetype_popularity((source_id,))

        self.assertEqual(resolved, stable)
        self.assertEqual(archetypes, ["Stable Archetype"])
        self.assertEqual(popularity, {"stable archetype": 2.0})

    def test_public_payload_carries_authoritative_stable_publication_metadata(self) -> None:
        source_id = "hsguru_meta_standard_legend"
        stable = {
            "fetched_at": "2026-07-21T10:00:00+00:00",
            "data": {"structured": {"strategies": [{"name": "Stable"}]}},
        }

        payload = public_dataset_payload(source_id, stable)

        self.assertEqual(payload["publication"], {
            "schema_version": 1,
            "source_id": source_id,
            "mode": "stable",
            "channel": "stable",
            "published_at": stable["fetched_at"],
        })
        self.assertNotIn("publication", stable, "response metadata must not mutate stored snapshots")

    def test_public_payload_carries_authoritative_early_publication_metadata(self) -> None:
        source_id = "hsreplay_arena_cards_advanced"
        early = {
            "fetched_at": "2026-07-21T11:00:00+00:00",
            "data": {
                "structured": {
                    "cards": [{"card_id": "EARLY"}],
                    "provisional": True,
                    "data_phase": "post_patch_early",
                }
            },
        }

        payload = public_dataset_payload(source_id, early)

        self.assertEqual(payload["publication"]["mode"], "early")
        self.assertEqual(payload["publication"]["published_at"], early["fetched_at"])

    def test_early_candidate_is_not_saved_if_admin_switches_to_stable_mid_fetch(self) -> None:
        source_id = "hsreplay_arena_cards_advanced"
        candidate = {
            "source_id": source_id,
            "fetched_at": "2026-07-21T12:00:00+00:00",
            "data": {"structured": {"cards": [{"card_id": "EARLY"}]}},
        }
        previous = {
            "source_id": source_id,
            "fetched_at": "2026-07-20T12:00:00+00:00",
            "data": {"structured": {"cards": [{"card_id": "STABLE"}]}},
        }
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ):
            store = ParserControlStore(Path(directory))
            early = store.update_policy(
                expected_revision=1,
                mode="early",
                early_until=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                reason="Балансный патч",
                updated_by="admin:7",
            )
            with patch("app.fetcher.load_dataset", return_value=previous), patch(
                "app.fetcher.save_dataset"
            ) as save_dataset, patch(
                "app.fetcher.check_dataset_regression", return_value=(False, None, {})
            ), patch("app.fetcher.log_action"):
                with capture_publication_policy(source_id):
                    store.update_policy(
                        expected_revision=early["revision"],
                        mode="stable",
                        early_until=None,
                        reason="Достаточная выборка",
                        updated_by="admin:7",
                    )
                    blocked, message, _ = _save_dataset_with_checks(
                        SOURCE_BY_ID[source_id],
                        candidate,
                        fetched_at=candidate["fetched_at"],
                    )

            self.assertTrue(blocked)
            self.assertIn("Publication policy changed", message or "")
            save_dataset.assert_not_called()
    def test_switching_back_to_stable_serves_non_provisional_baseline(self) -> None:
        source_id = "hsreplay_arena_cards_advanced"
        stable = {
            "fetched_at": "2026-07-20T00:00:00+00:00",
            "data": {"structured": {"cards": [{"card_id": "STABLE"}]}}
        }
        provisional = {
            "fetched_at": "2026-07-21T00:00:00+00:00",
            "data": {
                "structured": {
                    "cards": [{"card_id": "EARLY"}],
                    "provisional": True,
                    "data_phase": "post_patch_early",
                }
            },
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ):
            save_baseline_once(source_id, POST_PATCH_BASELINE_LABEL, stable)
            store = ParserControlStore(Path(directory))
            store.update_policy(
                expected_revision=1,
                mode="early",
                early_until=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                reason="Балансный патч",
                updated_by="admin:7",
            )
            state = store.snapshot()
            store.update_policy(
                expected_revision=state["revision"],
                mode="stable",
                early_until=None,
                reason="Выборка стабилизировалась",
                updated_by="admin:7",
            )

            published = resolve_public_dataset(source_id, provisional, store=store)

        self.assertIsNotNone(published)
        structured = published["data"]["structured"]
        self.assertEqual(structured["cards"][0]["card_id"], "STABLE")
        self.assertFalse(structured.get("provisional", False))

    def test_demo_view_uses_stable_baseline_after_mode_switch(self) -> None:
        source_id = "hsreplay_arena_cards_advanced"
        stable = {
            "fetched_at": "2026-07-20T00:00:00+00:00",
            "data": {
                "title": "Stable",
                "structured": {
                    "type": "arena_card_tiers",
                    "cards": [{"card_id": "STABLE", "name": "Stable card"}],
                },
            },
        }
        provisional = {
            "fetched_at": "2026-07-21T00:00:00+00:00",
            "data": {
                "title": "Early",
                "structured": {
                    "type": "arena_card_tiers",
                    "cards": [{"card_id": "EARLY", "name": "Early card"}],
                    "provisional": True,
                },
            },
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ):
            save_baseline_once(source_id, POST_PATCH_BASELINE_LABEL, stable)
            write_json(dataset_path(source_id), provisional)
            ParserControlStore(Path(directory)).update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="Стабильный режим",
                updated_by="admin:7",
            )
            with TestClient(app) as client:
                response = client.get(f"/demo/view/{source_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["view"]["cards"][0]["card_id"], "STABLE")
        self.assertEqual(payload["fetched_at"], stable["fetched_at"])

    def test_metadata_views_describe_the_resolved_stable_publication(self) -> None:
        from app.demo import build_overview
        from app.main import source_payload
        from app.routers.system import datasets as system_datasets
        from app.routers.system import sources as system_sources

        source_id = "hsreplay_arena_cards_advanced"
        source = SOURCE_BY_ID[source_id]
        stable = {
            "fetched_at": "2026-07-20T00:00:00+00:00",
            "backend": "stable-lkg",
            "data": {
                "structured": {
                    "type": "arena_card_tiers",
                    "cards": [{"card_id": "STABLE", "name": "Stable card"}],
                }
            },
        }
        provisional = {
            "fetched_at": "2026-07-21T00:00:00+00:00",
            "backend": "brightdata_web_unlocker",
            "data": {
                "structured": {
                    "type": "arena_card_tiers",
                    "cards": [{"card_id": "EARLY", "name": "Early card"}],
                    "provisional": True,
                }
            },
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ):
            save_baseline_once(source_id, POST_PATCH_BASELINE_LABEL, stable)
            write_json(dataset_path(source_id), provisional)
            ParserControlStore(Path(directory)).update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="Stable metadata",
                updated_by="test",
            )
            with patch("app.demo.SOURCES", (source,)):
                overview = build_overview()
            with patch("app.routers.system.SOURCES", (source,)):
                source_index = system_sources(site=None, category=None)
                dataset_index = system_datasets()
            legacy_source = source_payload(source_id)

        self.assertEqual(overview["sources"][0]["fetched_at"], stable["fetched_at"])
        self.assertEqual(
            source_index.data[0].dataset_fetched_at, stable["fetched_at"]
        )
        self.assertEqual(dataset_index.data[0].fetched_at, stable["fetched_at"])
        self.assertEqual(legacy_source["dataset_fetched_at"], stable["fetched_at"])

    def test_dataset_inventory_hides_expired_provisional_without_baseline(self) -> None:
        from app.main import list_datasets

        source_id = "heartharena_tierlist"
        source = SOURCE_BY_ID[source_id]
        provisional = {
            "fetched_at": "2026-07-21T00:00:00+00:00",
            "data": {
                "structured": {
                    "type": "arena_card_tiers",
                    "cards": [{"card_id": "EARLY", "name": "Early card"}],
                    "provisional": True,
                }
            },
        }

        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HS_API_DATA_DIR": directory}, clear=False
        ):
            write_json(dataset_path(source_id), provisional)
            ParserControlStore(Path(directory)).update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="No stable baseline",
                updated_by="test",
            )
            with patch("app.main.SOURCES", (source,)):
                inventory = list_datasets()

        self.assertFalse(inventory["datasets"][0]["has_dataset"])


if __name__ == "__main__":
    unittest.main()
