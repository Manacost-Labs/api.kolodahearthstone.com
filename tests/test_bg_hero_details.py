from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.hsreplay_bg_hero_details import (
    _load_valid_bg_hero_details_snapshot,
    _normalize_hero_power,
    _normalize_tavern_up,
    _tavern_recommendations,
    refresh_bg_hero_details,
)
from app.main import app


class BattlegroundsHeroDetailsTest(unittest.TestCase):
    @staticmethod
    def _hero_index(count: int, *, mode: str) -> dict:
        return {
            "mode": mode,
            "heroes": [
                {
                    "hero": f"Hero {idx}",
                    "dbfId": 50_000 + idx,
                    "tier": "A",
                    "pick_rate_value": 1.0,
                    "avg_placement": 4.0,
                    "adjusted_avg_placement": 4.0,
                    "placement_distribution": ["12.50%"] * 8,
                    "key_minions_top3": [],
                }
                for idx in range(count)
            ],
        }

    @staticmethod
    def _cached_snapshot() -> dict:
        return {
            "state": "ok",
            "fetched_at": "2026-08-10T02:35:00+00:00",
            "backend": "hsreplay_json_api",
            "data": {
                "structured": {
                    "type": "bg_hero_details",
                    "heroes": [{"dbfId": 50_000 + idx} for idx in range(30)],
                    "details": {
                        str(50_000 + idx): {
                            "hero": {"dbfId": 50_000 + idx},
                            "tavern_up": [{}],
                            "hero_power": [{}],
                            "combat_winrate": [{}],
                            "compositions": [{}],
                        }
                        for idx in range(30)
                    },
                    "duos": {"heroes": [{"dbfId": idx} for idx in range(20)]},
                }
            },
        }

    def test_tavern_up_recommendations_choose_most_common_tier_per_turn(self) -> None:
        payload = {
            "series": {
                "data": [
                    {
                        "recruit_round": 4,
                        "end_of_recruit_round_tier": 2,
                        "occurrences": 70,
                        "pct_at_tier": 70.0,
                        "num_games": 100,
                    },
                    {
                        "recruit_round": 4,
                        "end_of_recruit_round_tier": 3,
                        "occurrences": 30,
                        "pct_at_tier": 30.0,
                        "num_games": 100,
                    },
                    {
                        "recruit_round": 5,
                        "end_of_recruit_round_tier": 3,
                        "occurrences": 81,
                        "pct_at_tier": 81.25,
                        "num_games": 100,
                    },
                ]
            }
        }

        rows = _normalize_tavern_up(payload)
        recommendations = _tavern_recommendations(rows)

        self.assertEqual(rows[0]["turn"], 4)
        self.assertEqual(rows[0]["tavern_tier"], 2)
        self.assertEqual(recommendations[0]["recommended_tavern_tier"], 2)
        self.assertEqual(recommendations[1]["recommended_tavern_tier"], 3)
        self.assertEqual(recommendations[1]["pct_at_tier"], 81.25)

    def test_hero_power_turn_summary_is_weighted_by_data_points(self) -> None:
        payload = {
            "series": {
                "data": [
                    {
                        "recruit_round": 6,
                        "tavern_period": 2,
                        "gold": 8,
                        "end_of_round_median_tavern_tier": 3,
                        "times_invoked": 60,
                        "invoked_rate": 60.0,
                        "total_data_points": 200,
                    },
                    {
                        "recruit_round": 6,
                        "tavern_period": 3,
                        "gold": 8,
                        "end_of_round_median_tavern_tier": 3,
                        "times_invoked": 20,
                        "invoked_rate": 20.0,
                        "total_data_points": 101,
                    },
                    {
                        "recruit_round": 6,
                        "tavern_period": 4,
                        "gold": 8,
                        "end_of_round_median_tavern_tier": 4,
                        "times_invoked": 100,
                        "invoked_rate": 100.0,
                        "total_data_points": 10,
                    },
                ]
            }
        }

        rows, by_turn = _normalize_hero_power(payload, time_range="CURRENT_BATTLEGROUNDS_PATCH")

        self.assertEqual(len(rows), 3)
        self.assertEqual(by_turn, [{"turn": 6, "invoked_rate": 46.58, "total_data_points": 301}])

    def test_api_list_and_detail_routes_use_cached_payload(self) -> None:
        cached = {
            "type": "bg_hero_details",
            "fetched_at": "2026-06-28T10:00:00+00:00",
            "filters": {"mmr_percentile": "TOP_50_PERCENT", "time_range": "CURRENT_BATTLEGROUNDS_PATCH"},
            "heroes": [
                {"hero": "Test Hero", "dbfId": 57946, "tier": "A", "avg_placement": 4.1, "pick_rate": "3.20%"}
            ],
            "details": {
                "57946": {
                    "hero": {"hero": "Test Hero", "dbfId": 57946, "tier": "A"},
                    "tavern_up": [],
                    "tavern_up_by_turn": [],
                    "hero_power": [],
                    "hero_power_by_turn": [],
                    "compositions": [],
                    "best_composition": None,
                }
            },
            "duos": {
                "mode": "duos",
                "heroes": [{"hero": "Duos Hero", "dbfId": 1, "tier": "S", "avg_placement": 3.8}],
            },
            "source": {"backend": "test"},
        }
        client = TestClient(app)

        with patch("app.hsreplay_bg_hero_details.load_bg_hero_details", return_value=cached):
            solo = client.get("/api/bg/heroes")
            duos = client.get("/api/bg/heroes/duos")
            detail = client.get("/api/bg/heroes/57946")

        self.assertEqual(solo.status_code, 200)
        self.assertEqual(solo.json()["heroes"][0]["hero"], "Test Hero")
        self.assertEqual(duos.status_code, 200)
        self.assertEqual(duos.json()["heroes"][0]["hero"], "Duos Hero")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["hero"]["dbfId"], 57946)

    def test_partial_refresh_preserves_last_known_good_snapshot(self) -> None:
        solo = self._hero_index(40, mode="solo")
        duos = self._hero_index(30, mode="duos")

        async def detail(dbf_id: int, **_kwargs: object) -> dict | None:
            if dbf_id != 50_000:
                raise RuntimeError("upstream detail unavailable")
            return {"hero": {"dbfId": dbf_id}, "best_composition": None}

        with (
            patch("app.hsreplay_bg_hero_details._composition_names", new=AsyncMock(return_value={})),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=[solo, duos]),
            ),
            patch("app.hsreplay_bg_hero_details.fetch_hero_detail", side_effect=detail),
            patch(
                "app.hsreplay_bg_hero_details.load_dataset",
                return_value=self._cached_snapshot(),
            ),
            patch("app.hsreplay_bg_hero_details.save_dataset") as save_dataset,
            patch("app.hsreplay_bg_hero_details.save_status") as save_status,
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "partial")
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])
        self.assertIn("hero detail coverage too low", result["quality_errors"][0])
        save_dataset.assert_not_called()
        status = save_status.call_args.args[1]
        self.assertEqual(status["state"], "ok")
        self.assertTrue(status["serving_cached_dataset"])
        self.assertEqual(status["fetched_at"], "2026-08-10T02:35:00+00:00")
        self.assertEqual(status["last_refresh_state"], "partial")
        self.assertEqual(status["last_refresh_failure_class"], "quality_rejected")

    def test_initial_fetch_failure_preserves_last_known_good_snapshot(self) -> None:
        cached = self._cached_snapshot()

        with (
            patch(
                "app.hsreplay_bg_hero_details._composition_names",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=RuntimeError("upstream index unavailable")),
            ),
            patch("app.hsreplay_bg_hero_details.load_dataset", return_value=cached),
            patch("app.hsreplay_bg_hero_details.save_status") as save_status,
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "fetch_error")
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])
        self.assertEqual(result["details"], 30)
        status = save_status.call_args.args[1]
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["fetched_at"], cached["fetched_at"])
        self.assertEqual(status["last_refresh_state"], "fetch_error")
        self.assertEqual(status["last_refresh_failure_class"], "RuntimeError")
        self.assertTrue(status["serving_cached_dataset"])

    def test_initial_fetch_failure_without_cache_is_a_cold_failure(self) -> None:
        with (
            patch(
                "app.hsreplay_bg_hero_details._composition_names",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=RuntimeError("upstream index unavailable")),
            ),
            patch("app.hsreplay_bg_hero_details.load_dataset", return_value=None),
            patch("app.hsreplay_bg_hero_details.save_status") as save_status,
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertFalse(result["ok"])
        self.assertFalse(result["published"])
        self.assertFalse(result["serving_cached_dataset"])
        self.assertEqual(result["state"], "fetch_error")
        status = save_status.call_args.args[1]
        self.assertEqual(status["state"], "fetch_error")
        self.assertEqual(status["last_refresh_state"], "fetch_error")
        self.assertFalse(status["serving_cached_dataset"])

    def test_corrupt_json_cache_does_not_break_initial_failure_handling(self) -> None:
        with (
            patch(
                "app.hsreplay_bg_hero_details._composition_names",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=RuntimeError("upstream index unavailable")),
            ),
            patch(
                "app.hsreplay_bg_hero_details.load_dataset",
                return_value={
                    "state": "ok",
                    "fetched_at": "2026-08-10T02:35:00+00:00",
                    "data": "corrupt",
                },
            ),
            patch("app.hsreplay_bg_hero_details.save_status") as save_status,
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertFalse(result["ok"])
        self.assertFalse(result["serving_cached_dataset"])
        self.assertEqual(result["state"], "fetch_error")
        status = save_status.call_args.args[1]
        self.assertEqual(status["state"], "fetch_error")

    def test_empty_detail_sections_are_not_accepted_as_last_known_good(self) -> None:
        weak_cache = self._cached_snapshot()
        weak_cache["data"]["structured"]["details"] = {
            str(50_000 + idx): {} for idx in range(30)
        }
        with (
            patch(
                "app.hsreplay_bg_hero_details._composition_names",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=RuntimeError("upstream index unavailable")),
            ),
            patch("app.hsreplay_bg_hero_details.load_dataset", return_value=weak_cache),
            patch("app.hsreplay_bg_hero_details.save_status") as save_status,
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertFalse(result["serving_cached_dataset"])
        self.assertEqual(result["state"], "fetch_error")
        self.assertEqual(save_status.call_args.args[1]["state"], "fetch_error")

    def test_mixed_corrupt_rows_are_not_accepted_as_last_known_good(self) -> None:
        solo_corrupt = self._cached_snapshot()
        solo_corrupt["data"]["structured"]["heroes"][0] = "corrupt"
        detail_corrupt = self._cached_snapshot()
        detail_corrupt["data"]["structured"]["details"]["50000"] = "corrupt"
        section_corrupt = self._cached_snapshot()
        section_corrupt["data"]["structured"]["details"]["50000"][
            "tavern_up"
        ] = "corrupt"
        duos_corrupt = self._cached_snapshot()
        duos_corrupt["data"]["structured"]["duos"]["heroes"][0] = "corrupt"

        for label, snapshot in (
            ("solo hero", solo_corrupt),
            ("hero detail", detail_corrupt),
            ("detail section", section_corrupt),
            ("duos hero", duos_corrupt),
        ):
            with self.subTest(label=label), patch(
                "app.hsreplay_bg_hero_details.load_dataset",
                return_value=snapshot,
            ):
                self.assertIsNone(_load_valid_bg_hero_details_snapshot())

    def test_composition_labels_failure_does_not_block_complete_refresh(self) -> None:
        solo = self._hero_index(30, mode="solo")
        duos = self._hero_index(20, mode="duos")

        async def detail(dbf_id: int, **_kwargs: object) -> dict:
            return {
                "hero": {"dbfId": dbf_id},
                "best_composition": None,
                "tavern_up": [{}],
                "hero_power": [{}],
                "combat_winrate": [{}],
                "compositions": [{}],
            }

        with (
            patch(
                "app.hsreplay_bg_hero_details._composition_names",
                new=AsyncMock(side_effect=RuntimeError("optional labels unavailable")),
            ),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=[solo, duos]),
            ),
            patch("app.hsreplay_bg_hero_details.fetch_hero_detail", side_effect=detail) as fetch_detail,
            patch("app.hsreplay_bg_hero_details.load_dataset", return_value=None),
            patch("app.hsreplay_bg_hero_details.save_dataset"),
            patch("app.hsreplay_bg_hero_details.save_status"),
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["errors"][0]["stage"], "composition_names")
        self.assertTrue(all(call.kwargs["composition_names"] == {} for call in fetch_detail.call_args_list))

    def test_complete_refresh_is_published_atomically(self) -> None:
        solo = self._hero_index(30, mode="solo")
        duos = self._hero_index(20, mode="duos")

        async def detail(dbf_id: int, **_kwargs: object) -> dict:
            return {
                "hero": {"dbfId": dbf_id},
                "best_composition": None,
                "tavern_up": [{}],
                "hero_power": [{}],
                "combat_winrate": [{}],
                "compositions": [{}],
            }

        with (
            patch("app.hsreplay_bg_hero_details._composition_names", new=AsyncMock(return_value={})),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=[solo, duos]),
            ),
            patch("app.hsreplay_bg_hero_details.fetch_hero_detail", side_effect=detail),
            patch("app.hsreplay_bg_hero_details.load_dataset", return_value={"fetched_at": "old"}),
            patch("app.hsreplay_bg_hero_details.save_dataset") as save_dataset,
            patch("app.hsreplay_bg_hero_details.save_status") as save_status,
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertTrue(result["ok"])
        self.assertTrue(result["published"])
        self.assertEqual(result["detail_coverage"], 1.0)
        self.assertEqual(
            result["core_section_counts"],
            {
                "tavern_up": 30,
                "hero_power": 30,
                "combat_winrate": 30,
                "compositions": 30,
            },
        )
        self.assertEqual(save_dataset.call_count, 2)
        mirror_call = save_dataset.call_args_list[1]
        self.assertEqual(mirror_call.args[0], "hsreplay_battlegrounds_heroes")
        mirror = mirror_call.args[1]
        self.assertEqual(mirror["data"]["structured"]["type"], "bg_heroes")
        self.assertEqual(len(mirror["data"]["structured"]["heroes"]), 30)
        self.assertEqual(
            mirror["data"]["structured"]["source"]["backend"],
            "hsreplay_json_api",
        )
        hero_status = next(
            call.args[1]
            for call in save_status.call_args_list
            if call.args[0] == "hsreplay_battlegrounds_heroes"
        )
        self.assertEqual(hero_status["rows_total"], 30)
        detail_status = next(
            call.args[1]
            for call in save_status.call_args_list
            if call.args[0] == "hsreplay_battlegrounds_hero_details"
        )
        self.assertEqual(detail_status["rows_total"], 30)

    def test_post_patch_sparse_sections_are_valid_when_every_detail_was_fetched(self) -> None:
        solo = self._hero_index(115, mode="solo")
        duos = self._hero_index(20, mode="duos")

        async def detail(dbf_id: int, **_kwargs: object) -> dict:
            has_accumulated_stats = dbf_id < 50_069
            rows = [{}] if has_accumulated_stats else []
            return {
                "hero": {"dbfId": dbf_id},
                "best_composition": None,
                "tavern_up": rows,
                "hero_power": rows,
                "combat_winrate": rows,
                "compositions": rows,
            }

        with (
            patch("app.hsreplay_bg_hero_details._composition_names", new=AsyncMock(return_value={})),
            patch(
                "app.hsreplay_bg_hero_details.fetch_hero_index",
                new=AsyncMock(side_effect=[solo, duos]),
            ),
            patch("app.hsreplay_bg_hero_details.fetch_hero_detail", side_effect=detail),
            patch("app.hsreplay_bg_hero_details.load_dataset", return_value=None),
            patch("app.hsreplay_bg_hero_details.save_dataset"),
            patch("app.hsreplay_bg_hero_details.save_status"),
        ):
            result = asyncio.run(refresh_bg_hero_details())

        self.assertTrue(result["ok"])
        self.assertTrue(result["published"])
        self.assertEqual(result["detail_coverage"], 1.0)
        self.assertEqual(
            result["core_section_counts"],
            {
                "tavern_up": 69,
                "hero_power": 69,
                "combat_winrate": 69,
                "compositions": 69,
            },
        )


if __name__ == "__main__":
    unittest.main()
