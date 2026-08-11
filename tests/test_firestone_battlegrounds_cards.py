from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx

from app.firestone_comps import FIRESTONE_CARDS_URL, fetch_firestone_cards
from app.sources import SOURCE_BY_ID


def test_cards_use_explicit_mmr_100_direct_snapshot() -> None:
    source = SOURCE_BY_ID["firestone_battlegrounds_cards"]
    response = httpx.Response(
        200,
        content=json.dumps(
            {
                "cardStats": [
                    {
                        "cardId": "BG_TEST_001",
                        "totalPlayed": 120,
                        "averagePlacement": 3.8,
                        "averagePlacementOther": 4.2,
                    }
                ],
                "lastUpdateDate": "2026-08-11T12:11:35.099Z",
                "dataPoints": 349_261,
            }
        ).encode(),
    )
    fetch = AsyncMock(return_value=response)
    card = {
        "id": "BG_TEST_001",
        "dbfId": 123,
        "name": "Test Minion",
        "techLevel": 3,
        "isBattlegroundsPoolMinion": True,
        "isBattlegroundsPoolSpell": False,
    }

    with (
        patch("app.firestone_comps._get_static_json", fetch),
        patch("app.firestone_comps.card_from_id", return_value=card),
    ):
        result = asyncio.run(fetch_firestone_cards(source))

    fetch.assert_awaited_once()
    assert fetch.await_args.args[0] == FIRESTONE_CARDS_URL
    assert "/card-stats/mmr-100/past-three/" in FIRESTONE_CARDS_URL
    assert result["mmr_bracket"] == "mmr-100"
    assert result["time_period"] == "past-three"
    assert result["total_data_points"] == 349_261
    assert result["_fetch_backend"] == "proxyless_direct"
    assert result["tiers"]["3"][0]["name"] == "Test Minion"
    assert result["tiers"]["3"][0]["impact"] == 0.4
