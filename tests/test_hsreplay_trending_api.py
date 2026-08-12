from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.fetcher import _fetch_hsreplay_api_source, fetch_source
from app.hsreplay_meta_api import CLASS_RU_NAMES
from app.hsreplay_trending import (
    MAX_EXPECTED_TRENDING_CLASSES,
    MIN_EXPECTED_TRENDING_CLASSES,
    TRENDING_DECKS_API_URL,
    fetch_hsreplay_trending,
    normalize_trending_decks,
)
from app.source_contracts import HSREPLAY_JSON_CHANNELS, get_contract
from app.source_tiers import API_FIRST_SOURCE_IDS, SourceTier, tier_for
from app.sources import SOURCE_BY_ID

SOURCE = SOURCE_BY_ID["hsreplay_decks_trending"]
CLASS_KEYS = tuple(sorted(CLASS_RU_NAMES))


def _row(
    shortid: str,
    archetype_id: int,
    popularity_delta: float,
    *,
    games: int = 100,
    win_rate: float = 52.5,
) -> dict[str, object]:
    return {
        "shortid": shortid,
        "deck_list": "[]",
        "deck_sideboard": "[]",
        "archetype_id": archetype_id,
        "popularity_delta": popularity_delta,
        "total_games": games,
        "win_rate": win_rate,
        "avg_game_length_seconds": 540.0,
        "avg_num_player_turns": 8.0,
    }


def _payload(class_count: int = MAX_EXPECTED_TRENDING_CLASSES) -> dict[str, object]:
    data: dict[str, list[dict[str, object]]] = {}
    for index, class_key in enumerate(CLASS_KEYS[:class_count], start=1):
        data[class_key] = [
            _row(f"{class_key}Low", index, 0.2, games=500),
            _row(f"{class_key}Top", index, 1.5, games=200),
            _row(f"{class_key}Tie", index, 1.5, games=300),
        ]
    return {
        "render_as": "table",
        "series": {"metadata": {}, "data": data},
        "as_of": "2026-08-11T22:45:39Z",
    }


def _archetypes(class_count: int = MAX_EXPECTED_TRENDING_CLASSES) -> dict[int, dict[str, object]]:
    return {
        index: {
            "id": index,
            "name": f"Trending {class_key.title()}",
            "player_class_name": class_key,
            "url": f"/archetypes/{index}/test",
        }
        for index, class_key in enumerate(CLASS_KEYS[:class_count], start=1)
    }


def test_normalize_trending_selects_one_highest_delta_deck_per_class() -> None:
    decks = normalize_trending_decks(_payload(), _archetypes())

    assert len(decks) == MAX_EXPECTED_TRENDING_CLASSES
    assert [deck["class"] for deck in decks] == list(CLASS_KEYS)
    # Equal deltas use games as a stable tie-breaker, not upstream row order.
    assert all(str(deck["hsreplay_deck_id"]).endswith("Tie") for deck in decks)
    assert all(deck["popularity_delta"] == 1.5 for deck in decks)
    assert all(deck["games"] == "300" for deck in decks)
    assert all(deck["winrate"] == "52.5%" for deck in decks)
    assert all(deck["duration"] == "9.0 min" for deck in decks)
    assert all(str(deck["name"]).startswith("Trending ") for deck in decks)
    assert all(
        str(deck["deck_url"]).startswith("/decks/")
        for deck in decks
    )


def test_normalize_trending_skips_invalid_top_row_and_rejects_duplicate_identity() -> None:
    payload = _payload()
    data = payload["series"]["data"]  # type: ignore[index]
    first_class, second_class = CLASS_KEYS[:2]
    data[first_class].append(_row("invalid id!", 1, 99.0))

    decks = normalize_trending_decks(payload, _archetypes())
    assert decks[0]["hsreplay_deck_id"] == f"{first_class}Tie"

    data[second_class] = [_row(f"{first_class}Tie", 2, 2.0)]
    with pytest.raises(ValueError, match="reused one deck"):
        normalize_trending_decks(payload, _archetypes())


def test_fetch_trending_builds_backwards_compatible_structured_dataset() -> None:
    fetch_json = AsyncMock(return_value=_payload())
    name_map = AsyncMock(return_value=_archetypes())

    async def run() -> dict[str, object]:
        with (
            patch("app.hsreplay_trending.fetch_hsreplay_json", fetch_json),
            patch("app.hsreplay_trending._archetype_name_map", name_map),
        ):
            return await fetch_hsreplay_trending(SOURCE)

    structured = asyncio.run(run())

    assert structured["type"] == "trending_decks"
    assert structured["total_decks"] == MAX_EXPECTED_TRENDING_CLASSES
    assert structured["source"]["candidate_decks"] == 33  # type: ignore[index]
    assert structured["source"]["archetype_names_resolved"] == 11  # type: ignore[index]
    assert {
        "name",
        "winrate",
        "games",
        "duration",
        "deck_url",
        "hsreplay_deck_id",
    } <= set(structured["decks"][0])  # type: ignore[index]
    fetch_json.assert_awaited_once_with(
        TRENDING_DECKS_API_URL,
        source_id=SOURCE.id,
        cache_key="hsreplay:trending-decks:popularity",
    )
    name_map.assert_awaited_once_with(SOURCE.id)


def test_fetch_trending_keeps_truthful_names_when_dictionary_is_temporarily_down() -> None:
    async def run() -> dict[str, object]:
        with (
            patch(
                "app.hsreplay_trending.fetch_hsreplay_json",
                new=AsyncMock(return_value=_payload(MIN_EXPECTED_TRENDING_CLASSES)),
            ),
            patch(
                "app.hsreplay_trending._archetype_name_map",
                new=AsyncMock(side_effect=RuntimeError("temporary outage")),
            ),
        ):
            return await fetch_hsreplay_trending(SOURCE)

    structured = asyncio.run(run())

    assert structured["total_decks"] == MIN_EXPECTED_TRENDING_CLASSES
    assert structured["source"]["archetype_names_resolved"] == 0  # type: ignore[index]
    assert all(
        str(deck["name"]).startswith("Архетип #")
        for deck in structured["decks"]  # type: ignore[union-attr]
    )


def test_fetch_trending_rejects_incomplete_class_set_for_browser_fallback() -> None:
    async def run() -> None:
        with (
            patch(
                "app.hsreplay_trending.fetch_hsreplay_json",
                new=AsyncMock(
                    return_value=_payload(MIN_EXPECTED_TRENDING_CLASSES - 1)
                ),
            ),
            patch(
                "app.hsreplay_trending._archetype_name_map",
                new=AsyncMock(return_value=_archetypes()),
            ),
        ):
            await fetch_hsreplay_trending(SOURCE)

    with pytest.raises(RuntimeError, match="incomplete class set"):
        asyncio.run(run())


def test_trending_dispatch_uses_api_adapter_without_changing_protected_tier() -> None:
    structured = {
        "type": "trending_decks",
        "decks": [],
        "source": {"backend": "hsreplay_trending_api"},
    }
    adapter = AsyncMock(return_value=structured)

    async def run() -> dict[str, object] | None:
        with patch("app.hsreplay_trending.fetch_hsreplay_trending", adapter):
            return await _fetch_hsreplay_api_source(SOURCE)

    parsed = asyncio.run(run())

    assert SOURCE.id in API_FIRST_SOURCE_IDS
    assert tier_for(SOURCE.id) is SourceTier.BROWSER_PROTECTED
    contract = get_contract(SOURCE.id)
    assert contract is not None
    assert contract.preferred_channels == HSREPLAY_JSON_CHANNELS
    assert contract.allow_browser_fallback
    assert parsed is not None
    assert parsed["_backend"] == "hsreplay_trending_api"
    assert parsed["structured"] == structured
    adapter.assert_awaited_once_with(SOURCE)


def test_trending_api_failure_falls_through_to_existing_browser_route() -> None:
    parsed = {
        "source_id": SOURCE.id,
        "site": SOURCE.site,
        "category": SOURCE.category,
        "url": SOURCE.url,
        "structured": {
            "type": "trending_decks",
            "decks": [
                {
                    "name": f"Deck {index}",
                    "winrate": "52.0%",
                    "games": "100",
                    "duration": "8.0 min",
                    "deck_url": f"/decks/test{index}/",
                    "hsreplay_deck_id": f"test{index}",
                }
                for index in range(10)
            ],
        },
        "counts": {"api_bytes": 1000},
    }
    browser_result = SimpleNamespace(
        html="<html>" + ("valid trending data " * 200) + "</html>",
        http_status=200,
        final_url=SOURCE.url,
        backend="flaresolverr",
        snapshot=None,
    )
    api_fetch = AsyncMock(side_effect=RuntimeError("temporary API outage"))
    browser_fetch = AsyncMock(return_value=browser_result)
    logged_actions: list[str] = []

    async def run() -> dict[str, object]:
        with (
            patch("app.fetcher.load_status", return_value={}),
            patch("app.fetcher.save_status"),
            patch("app.fetcher._fetch_hsreplay_api_source", api_fetch),
            patch("app.fetcher.fetch_html", browser_fetch),
            patch("app.fetcher.parse_html", return_value=parsed),
            patch(
                "app.fetcher.validate_candidate_for_publish",
                return_value=SimpleNamespace(ok=True, reason="ok", extra={}),
            ),
            patch(
                "app.fetcher.quality_metrics",
                return_value={"quality_score": 1.0, "rows_total": 10},
            ),
            patch(
                "app.fetcher._review_candidate_with_ai",
                new=AsyncMock(return_value=(None, False, None)),
            ),
            patch(
                "app.fetcher._save_dataset_with_checks",
                return_value=(False, None, {}),
            ),
            patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
            patch("app.fetcher.firecrawl_fallback_source_ids", return_value=set()),
            patch("app.fetcher.complete_source_trace"),
            patch(
                "app.fetcher.log_action",
                side_effect=lambda action, **_kwargs: logged_actions.append(action),
            ),
        ):
            return await fetch_source(None, SOURCE)

    status = asyncio.run(run())

    assert status["state"] == "ok"
    assert status["backend"] == "flaresolverr"
    api_fetch.assert_awaited_once_with(SOURCE)
    browser_fetch.assert_awaited_once()
    assert any(
        action == "api.fallback.browser"
        for action in logged_actions
    )
