from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.hsguru_meta import hsguru_archetype_analysis

DATASET = {
    "fetched_at": "2026-07-24T00:00:00+00:00",
    "data": {
        "structured": {
            "type": "hsguru_archetype_analysis",
            "archetypes": [
                {
                    "format": "standard",
                    "archetype": "Void Soul DH",
                    "rank": "legend",
                    "period": "past_week",
                    "updated_at": "2026-07-24T01:00:00+00:00",
                    "class_matchups": [{"class_key": "mage", "winrate": 52.3}],
                    "card_stats": [{"card_id": "TOY_330", "mulligan_impact": 4.8}],
                }
            ],
        }
    },
}


class HSGuruArchetypeAnalysisApiTest(unittest.TestCase):
    def test_returns_exact_format_and_case_insensitive_archetype(self) -> None:
        with (
            patch("app.routers.hsguru_meta.load_dataset", return_value=DATASET),
            patch(
                "app.storage.load_status",
                return_value={
                    "state": "ok",
                    "published": True,
                    "serving_cached_dataset": False,
                    "fetched_at": DATASET["fetched_at"],
                },
            ),
        ):
            payload = hsguru_archetype_analysis(
                archetype="void soul dh",
                format_name="standard",
            )

        self.assertEqual(payload["data"]["rank"], "legend")
        self.assertEqual(payload["data"]["class_matchups"][0]["class_key"], "mage")
        self.assertEqual(payload["meta"]["source_id"], "hsguru_archetype_analysis")

    def test_cached_fallback_is_available_but_not_reported_fresh(self) -> None:
        dataset = deepcopy(DATASET)
        now = datetime.now(UTC).isoformat()
        dataset["fetched_at"] = now
        dataset["data"]["structured"]["archetypes"][0]["updated_at"] = now
        latest_status = {
            "source_id": "hsguru_archetype_analysis",
            "state": "partial",
            "published": False,
            "serving_cached_dataset": True,
            "last_refresh_state": "fetch_error",
        }

        with (
            patch("app.routers.hsguru_meta.load_dataset", return_value=dataset),
            patch("app.storage.load_status", return_value=latest_status),
        ):
            payload = hsguru_archetype_analysis(
                archetype="void soul dh",
                format_name="standard",
            )

        self.assertEqual(payload["data"]["rank"], "legend")
        self.assertTrue(payload["meta"]["stale"])
        self.assertTrue(payload["meta"]["serving_cached_dataset"])
        self.assertTrue(payload["meta"]["cached_after_failure"])
        self.assertFalse(payload["meta"]["fresh_candidate_published"])

    def test_successfully_published_candidate_is_reported_fresh(self) -> None:
        dataset = deepcopy(DATASET)
        now = datetime.now(UTC).isoformat()
        dataset["fetched_at"] = now
        dataset["data"]["structured"]["archetypes"][0]["updated_at"] = now
        latest_status = {
            "source_id": "hsguru_archetype_analysis",
            "state": "ok",
            "published": True,
            "serving_cached_dataset": False,
            "fetched_at": now,
        }

        with (
            patch("app.routers.hsguru_meta.load_dataset", return_value=dataset),
            patch("app.storage.load_status", return_value=latest_status),
        ):
            payload = hsguru_archetype_analysis(
                archetype="void soul dh",
                format_name="standard",
            )

        self.assertFalse(payload["meta"]["stale"])
        self.assertFalse(payload["meta"]["serving_cached_dataset"])
        self.assertFalse(payload["meta"]["cached_after_failure"])
        self.assertTrue(payload["meta"]["fresh_candidate_published"])

    def test_unreadable_status_does_not_hide_available_dataset(self) -> None:
        dataset = deepcopy(DATASET)
        now = datetime.now(UTC).isoformat()
        dataset["fetched_at"] = now
        dataset["data"]["structured"]["archetypes"][0]["updated_at"] = now

        with (
            patch("app.routers.hsguru_meta.load_dataset", return_value=dataset),
            patch("app.storage.load_status", side_effect=OSError("status unavailable")),
        ):
            payload = hsguru_archetype_analysis(
                archetype="void soul dh",
                format_name="standard",
            )

        self.assertEqual(payload["data"]["rank"], "legend")
        self.assertTrue(payload["meta"]["stale"])
        self.assertFalse(payload["meta"]["serving_cached_dataset"])
        self.assertFalse(payload["meta"]["cached_after_failure"])
        self.assertFalse(payload["meta"]["fresh_candidate_published"])

    def test_success_for_a_different_dataset_revision_is_not_reported_fresh(
        self,
    ) -> None:
        dataset = deepcopy(DATASET)
        now = datetime.now(UTC).isoformat()
        dataset["fetched_at"] = now
        dataset["data"]["structured"]["archetypes"][0]["updated_at"] = now
        latest_status = {
            "source_id": "hsguru_archetype_analysis",
            "state": "ok",
            "published": True,
            "serving_cached_dataset": False,
            "fetched_at": "2026-08-13T22:00:00+00:00",
        }

        with (
            patch("app.routers.hsguru_meta.load_dataset", return_value=dataset),
            patch("app.storage.load_status", return_value=latest_status),
        ):
            payload = hsguru_archetype_analysis(
                archetype="void soul dh",
                format_name="standard",
            )

        self.assertTrue(payload["meta"]["stale"])
        self.assertFalse(payload["meta"]["cached_after_failure"])
        self.assertFalse(payload["meta"]["fresh_candidate_published"])

    def test_returns_not_found_for_missing_analysis(self) -> None:
        with (
            patch("app.routers.hsguru_meta.load_dataset", return_value=DATASET),
            self.assertRaises(HTTPException) as raised,
        ):
            hsguru_archetype_analysis(
                archetype="Quest Mage",
                format_name="standard",
            )

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
