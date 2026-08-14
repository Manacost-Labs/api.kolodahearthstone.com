from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.completeness import row_retrieval_evidence
from app.hsreplay_arena_api import (
    ARENA_CARD_STATS_API_URL,
    _class_name,
    _fetch_arena_cards_payload,
    fetch_arena_card_tiers,
    fetch_class_stats,
    normalize_arena_card_row,
)
from app.source_contracts import contract_quality_report
from app.structured_schema import validate_structured_schema


class HsreplayArenaApiTest(unittest.TestCase):
    @staticmethod
    def _valid_card_row() -> dict[str, object]:
        return {
            "card_id": "CARD_1",
            "win_rate": 50,
            "winrate_when_drawn": 51,
            "winrate_when_played": 52,
            "popularity": 10,
            "avg_copies_in_deck": 1.0,
            "num_games": 10,
        }

    def test_advanced_rates_reject_nonfinite_bool_and_out_of_range_values(self) -> None:
        invalid_values = (
            False,
            float("nan"),
            float("inf"),
            -1,
            101,
            "unknown",
        )
        for raw_field in (
            "win_rate",
            "winrate_when_drawn",
            "winrate_when_played",
        ):
            for invalid in invalid_values:
                with self.subTest(field=raw_field, invalid=invalid):
                    raw = self._valid_card_row()
                    raw[raw_field] = invalid
                    with self.assertRaises((TypeError, ValueError)):
                        normalize_arena_card_row(raw, locale="enUS")

    def test_advanced_in_runs_and_average_copies_reject_invalid_present_values(self) -> None:
        for invalid in (False, float("nan"), float("inf"), -1, 101, "unknown"):
            with self.subTest(in_runs=invalid):
                raw = self._valid_card_row()
                raw["popularity"] = invalid
                with self.assertRaises((TypeError, ValueError)):
                    normalize_arena_card_row(raw, locale="enUS")

        for invalid in (False, float("nan"), float("inf"), -1, "1"):
            with self.subTest(avg_copies=invalid):
                raw = self._valid_card_row()
                raw["avg_copies_in_deck"] = invalid
                with self.assertRaises((TypeError, ValueError)):
                    normalize_arena_card_row(raw, locale="enUS")

    def test_invalid_present_rate_is_rejected_even_without_games(self) -> None:
        raw = self._valid_card_row()
        raw["num_games"] = 0
        raw["win_rate"] = float("nan")

        with self.assertRaises(ValueError):
            normalize_arena_card_row(raw, locale="enUS")
    def test_arena_cards_publish_v1_row_retrieval_for_primary_slice(self) -> None:
        raw_rows = [{"card_id": "A"}, {"card_id": "B"}]
        normalized = [
            {"card_id": "A", "deck_winrate": "50%"},
            {"card_id": "B", "deck_winrate": "51%"},
        ]
        with (
            patch(
                "app.hsreplay_arena_api._fetch_arena_cards_payload",
                new=AsyncMock(return_value=({"data": {"ALL": raw_rows}}, "api")),
            ),
            patch(
                "app.hsreplay_arena_api._parse_arena_cards_payload",
                return_value={"ALL": normalized},
            ),
        ):
            result = asyncio.run(fetch_arena_card_tiers())

        self.assertEqual(result["completeness_schema_version"], 1)
        self.assertEqual(result["cards"], normalized)
        self.assertEqual(result["row_retrieval"]["raw_rows"], 2)
        self.assertEqual(result["row_retrieval"]["normalized_rows"], 2)
        self.assertEqual(result["row_retrieval"]["unexplained_drops"], 0)
        self.assertEqual(result["row_retrieval"]["scope"], "primary_class:ALL")
        self.assertEqual(result["primary_class"], "ALL")
        self.assertEqual(result["selected_class"], "ALL")

    def test_arena_cards_default_scope_rejects_payload_without_all_bucket(self) -> None:
        raw_rows = [self._valid_card_row() for _ in range(20)]
        with (
            patch(
                "app.hsreplay_arena_api._fetch_arena_cards_payload",
                new=AsyncMock(
                    return_value=(
                        {"data": {"MAGE": raw_rows}},
                        ARENA_CARD_STATS_API_URL,
                    )
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "advanced API unavailable"),
        ):
            asyncio.run(fetch_arena_card_tiers(locale="enUS"))

    def test_post_patch_minimum_does_not_accept_mismatched_arena_scope(self) -> None:
        cards = []
        for index in range(20):
            row = {
                "name": f"Card {index}",
                "card_id": f"CARD_{index}",
                "deck_winrate": "50.00%",
                "winrate_when_drawn": "51.00%",
                "winrate_when_played": "52.00%",
                "in_runs": "1.00%",
                "avg_copies": 1.0,
                "times_played": 10,
                "field_availability": {
                    field: {"available": True, "reason": None}
                    for field in (
                        "deck_winrate",
                        "winrate_when_drawn",
                        "winrate_when_played",
                    )
                },
            }
            cards.append(row)
        structured = {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "primary_class": "ALL",
            "selected_class": "MAGE",
            "row_retrieval": row_retrieval_evidence(
                raw_rows=len(cards),
                eligible_rows=len(cards),
                normalized_rows=len(cards),
                scope="primary_class:MAGE",
            ),
            "cards": cards,
        }
        window_time = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
        with (
            patch.dict("os.environ", {"HS_ARENA_POST_PATCH_ENABLED": "true"}),
            patch("app.post_patch_policy.current_time", return_value=window_time),
        ):
            report = contract_quality_report(
                "hsreplay_arena_cards_advanced",
                structured,
            )

        self.assertEqual(report["minimum_rows"], 20)
        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertEqual(
            report["class_scope"],
            {
                "primary_class": "ALL",
                "selected_class": "MAGE",
                "exact_match": False,
            },
        )
        self.assertIn(
            "primary_class and selected_class must both be ALL", report["warnings"]
        )
        with self.assertRaisesRegex(ValueError, "selected_class must exactly match"):
            validate_structured_schema(structured)

    def test_contract_rejects_versioned_arena_payload_without_class_scope(self) -> None:
        report = contract_quality_report(
            "hsreplay_arena_cards_advanced",
            {
                "type": "arena_card_tiers",
                "completeness_schema_version": 1,
                "row_retrieval": row_retrieval_evidence(
                    raw_rows=0,
                    eligible_rows=0,
                    normalized_rows=0,
                    scope="test_rows",
                ),
                "cards": [],
            },
        )

        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertEqual(
            report["class_scope"],
            {
                "primary_class": None,
                "selected_class": None,
                "exact_match": False,
            },
        )
        self.assertIn(
            "primary_class and selected_class must both be ALL", report["warnings"]
        )

    def test_contract_rejects_non_global_arena_scope_even_when_exact(self) -> None:
        report = contract_quality_report(
            "hsreplay_arena_cards_advanced",
            {
                "type": "arena_card_tiers",
                "completeness_schema_version": 1,
                "primary_class": "MAGE",
                "selected_class": "MAGE",
                "row_retrieval": row_retrieval_evidence(
                    raw_rows=0,
                    eligible_rows=0,
                    normalized_rows=0,
                    scope="primary_class:MAGE",
                ),
                "cards": [],
            },
        )

        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertIn(
            "primary_class and selected_class must both be ALL", report["warnings"]
        )

    def test_arena_cards_endpoint_forces_json_for_the_recent_sample(self) -> None:
        self.assertEqual(
            ARENA_CARD_STATS_API_URL,
            (
                "https://hsreplay.net/api/v1/arena/card_stats/"
                "?ArenaTimestampRangeFilter=LAST_4_DAYS&format=json"
            ),
        )

    def test_arena_cards_publish_period_filter_and_header_freshness(self) -> None:
        payload = {
            "metadata": {"meta_period_id": 16},
            "selected_params": [
                "ArenaGameTypeFilter.BGT_UNDERGROUND_ARENA",
                "ArenaTimestampRangeFilter.LAST_4_DAYS",
            ],
            "data": {"ALL": [self._valid_card_row()]},
        }
        normalized = normalize_arena_card_row(self._valid_card_row(), locale="enUS")
        assert normalized is not None
        normalized["arena_class"] = "ALL"
        freshness = {
            "status": "fresh",
            "reason": None,
            "observed_at": "2026-08-14T02:20:00+00:00",
            "age_seconds": 60,
            "evidence": ["last_modified"],
            "response_headers": {},
            "meta_period_id": 16,
            "selected_params": payload["selected_params"],
            "filters_match": True,
        }
        with (
            patch(
                "app.hsreplay_arena_api._fetch_arena_cards_payload",
                new=AsyncMock(return_value=(payload, ARENA_CARD_STATS_API_URL)),
            ),
            patch(
                "app.hsreplay_arena_api.get_hsreplay_json_target_headers",
                return_value={"last-modified": "Fri, 14 Aug 2026 02:19:00 GMT"},
            ) as headers,
            patch(
                "app.hsreplay_arena_api.build_hsreplay_arena_upstream_freshness",
                return_value=freshness,
            ) as build,
        ):
            result = asyncio.run(fetch_arena_card_tiers(locale="enUS"))

        self.assertEqual(result["cards"], [normalized])
        self.assertEqual(result["upstream_freshness"], freshness)
        self.assertEqual(result["population_completeness"], "unverifiable")
        headers.assert_called_once_with(ARENA_CARD_STATS_API_URL)
        build.assert_called_once()
        validate_structured_schema(result)

    def test_arena_cards_keep_primary_endpoint_after_first_channel_failure(
        self,
    ) -> None:
        channel_fetch = AsyncMock(
            side_effect=[
                RuntimeError("first channel failed"),
                '{"data": "valid"}',
            ]
        )
        channels = [
            ("curl_cffi", ARENA_CARD_STATS_API_URL),
            ("flaresolverr", ARENA_CARD_STATS_API_URL),
        ]

        with (
            patch("app.hsreplay_client.get_cached_hsreplay_json", return_value=None),
            patch("app.hsreplay_client.set_cached_hsreplay_json"),
            patch("app.hsreplay_client._channel_urls", return_value=channels),
            patch(
                "app.hsreplay_client._channel_uses_residential_proxy",
                return_value=False,
            ),
            patch("app.hsreplay_client._fetch_body_for_channel", new=channel_fetch),
            patch("app.hsreplay_client.asyncio.sleep", new=AsyncMock()),
            patch("app.hsreplay_client.log_action"),
            patch(
                "app.hsreplay_arena_api._parse_arena_cards_payload",
                return_value={"ALL": [{} for _ in range(900)]},
            ),
        ):
            payload, api_url = asyncio.run(
                _fetch_arena_cards_payload("hsreplay_arena_cards_advanced")
            )

        self.assertEqual(payload, {"data": "valid"})
        self.assertEqual(api_url, ARENA_CARD_STATS_API_URL)
        self.assertEqual(
            [call.args[:2] for call in channel_fetch.await_args_list],
            [
                ("curl_cffi", ARENA_CARD_STATS_API_URL),
                ("flaresolverr", ARENA_CARD_STATS_API_URL),
            ],
        )
        self.assertTrue(
            all(
                call.args[1] == ARENA_CARD_STATS_API_URL
                for call in channel_fetch.await_args_list
            )
        )

    def test_class_names_use_titlecased_cardclass_enum_labels(self) -> None:
        # _class_name titles the hearthstone CardClass enum name
        # (app/hsreplay_arena_api.py:74-80): DEATHKNIGHT -> "Deathknight",
        # DEMONHUNTER -> "Demonhunter". This single-word form is the canonical
        # `class` value across the app: app/hsreplay_arena_classes_firecrawl.py:14
        # uses "Deathknight" as `class` (the multiword "Death Knight" lives in the
        # separate display field `class_name`), and app/db.py:40 normalizes it.
        self.assertEqual(_class_name(1), "Deathknight")
        self.assertEqual(_class_name(14), "Demonhunter")
        self.assertIsNone(_class_name(None))
        self.assertIsNone(_class_name(99))

    def test_fetch_class_stats_returns_classes_and_empty_matchups(self) -> None:
        # Dual-class arena was permanently removed from the game, so
        # fetch_class_stats no longer reads "dual_class_data" at all: it parses
        # classes and always returns matchups=[] (kept for dataset-shape
        # compatibility until Phase 8). The quality gate validates classes only.
        payload = {
            "data": [
                {"deck_class": 1, "win_rate": 50.0, "num_drafts": 100, "pick_rate": 10.0},
                {"deck_class": 2, "win_rate": 51.0, "num_drafts": 120, "pick_rate": 12.0},
                {"deck_class": 14, "win_rate": 52.0, "num_drafts": 130, "pick_rate": 13.0},
            ],
            # even a legacy payload that still carries dual_class_data is ignored
            "dual_class_data": [
                {"deck_class": 1, "secondary_deck_class": 2, "win_rate": 53.0},
            ],
        }

        with patch("app.hsreplay_arena_api.fetch_hsreplay_json", new=AsyncMock(return_value=payload)):
            result = asyncio.run(fetch_class_stats())

        self.assertEqual(result["type"], "arena_class_matrix")
        self.assertEqual(result["matchups"], [])
        self.assertEqual(len(result["classes"]), 3)
        # classes are sorted by win_rate desc
        self.assertEqual(
            [row["win_rate"] for row in result["classes"]],
            [52.0, 51.0, 50.0],
        )
        self.assertIn("Deathknight", {row["class"] for row in result["classes"]})
        self.assertIn("Demonhunter", {row["class"] for row in result["classes"]})


if __name__ == "__main__":
    unittest.main()
