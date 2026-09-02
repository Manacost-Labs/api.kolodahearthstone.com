from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx

from app.firestone_comps import (
    FIRESTONE_CARDS_URL,
    fetch_firestone_arena,
    fetch_firestone_cards,
)
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


def test_arena_parser_keeps_upstream_lineage_and_legendary_filter() -> None:
    cards_response = httpx.Response(
        200,
        content=json.dumps(
            {
                "lastUpdated": "2026-08-30T04:25:57.434Z",
                "context": "global",
                "stats": [
                    {
                        "cardId": "ARENA_LEGENDARY",
                        "stats": {"decksWithCard": 100, "decksWithCardThenWin": 60},
                    },
                    {
                        "cardId": "ARENA_COMMON",
                        "stats": {"decksWithCard": 100, "decksWithCardThenWin": 50},
                    },
                    {
                        "cardId": "ARENA_LOW_SAMPLE_LEGENDARY",
                        "stats": {"decksWithCard": 1, "decksWithCardThenWin": 1},
                    },
                ],
            }
        ).encode(),
    )
    draft_response = httpx.Response(
        200,
        content=json.dumps({"lastUpdateDate": "2026-08-30T04:27:29.311Z", "stats": []}).encode(),
    )
    fetch = AsyncMock(side_effect=[cards_response, draft_response])

    def card(card_id: str, *, locale: str) -> dict[str, object]:
        del locale
        rarity = "COMMON" if card_id == "ARENA_COMMON" else "LEGENDARY"
        return {"id": card_id, "name": card_id, "rarity": rarity}

    source = SOURCE_BY_ID["firestone_arena_legendaries_underground"]
    with (
        patch("app.firestone_comps._get_static_json", fetch),
        patch("app.firestone_comps.card_from_id", side_effect=card),
    ):
        result = asyncio.run(fetch_firestone_arena(source))

    assert len(result["cards"]) == 1
    assert result["cards"][0]["card_id"] == "ARENA_LEGENDARY"
    assert result["upstream_stats_count"] == 3
    assert result["upstream_scope_stats_count"] == 2
    assert result["upstream_context"] == "global"
    assert result["arena_mode"] == "arena-underground"
    assert result["legendary_only"] is True
