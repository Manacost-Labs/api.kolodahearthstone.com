from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.canary import _check_firestone_static, _check_hsreplay_arena, run_canary


async def _ok_check() -> dict:
    return {"name": "ok_check", "ok": True}


async def _fail_check() -> dict:
    return {"name": "fail_check", "ok": False, "detail": "temporary failure"}


class CanaryTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _early_arena_cards(count: int) -> dict:
        return {
            "type": "arena_card_tiers",
            "cards": [
                {
                    "card_id": f"CARD_{index}",
                    "deck_winrate": "52%",
                    "winrate_when_drawn": "51%",
                    "tier": "B",
                    "total_games": 10,
                }
                for index in range(count)
            ],
        }

    async def test_arena_canary_accepts_valid_early_sample(self) -> None:
        structured = self._early_arena_cards(20)
        early_time = datetime(2026, 8, 11, 12, tzinfo=UTC)
        with (
            patch.dict(
                os.environ,
                {
                    "HS_ARENA_POST_PATCH_ENABLED": "true",
                    "HS_ARENA_POST_PATCH_FROM": "2026-08-10",
                    "HS_ARENA_POST_PATCH_UNTIL": "2026-08-12",
                },
                clear=False,
            ),
            patch("app.post_patch_policy.current_time", return_value=early_time),
            patch("app.canary._structured_from_dataset", return_value=structured),
            patch(
                "app.hsreplay_arena_api.fetch_arena_card_tiers",
                new=AsyncMock(),
            ) as live_fetch,
        ):
            result = await _check_hsreplay_arena()

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["cached"])
        live_fetch.assert_not_awaited()

    async def test_arena_canary_rejects_twenty_rows_in_stable_mode(self) -> None:
        structured = self._early_arena_cards(20)
        with (
            patch.dict(
                os.environ,
                {"HS_ARENA_POST_PATCH_ENABLED": "false"},
                clear=False,
            ),
            patch("app.canary._structured_from_dataset", return_value=structured),
            patch(
                "app.hsreplay_arena_api.fetch_arena_card_tiers",
                new=AsyncMock(return_value=structured),
            ) as live_fetch,
        ):
            result = await _check_hsreplay_arena()

        self.assertFalse(result["ok"])
        self.assertFalse(result["cached"])
        live_fetch.assert_awaited_once()

    async def test_firestone_canary_uses_same_early_quality_contract(self) -> None:
        structured = self._early_arena_cards(20)
        early_time = datetime(2026, 8, 11, 12, tzinfo=UTC)
        with (
            patch.dict(
                os.environ,
                {
                    "HS_ARENA_POST_PATCH_ENABLED": "true",
                    "HS_ARENA_POST_PATCH_FROM": "2026-08-10",
                    "HS_ARENA_POST_PATCH_UNTIL": "2026-08-12",
                },
                clear=False,
            ),
            patch("app.post_patch_policy.current_time", return_value=early_time),
            patch(
                "app.firestone_comps.fetch_firestone_arena",
                new=AsyncMock(return_value=structured),
            ),
        ):
            result = await _check_firestone_static()

        self.assertTrue(result["ok"], result)

    async def test_run_canary_collects_failures(self) -> None:
        with patch("app.canary.CHECKS", (_ok_check, _fail_check)):
            result = await run_canary(strict=True)

        self.assertFalse(result["ok"])
        self.assertTrue(result["strict"])
        self.assertEqual(result["failures"], ["fail_check"])
        self.assertEqual(len(result["checks"]), 2)


if __name__ == "__main__":
    unittest.main()
