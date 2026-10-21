from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app import hsguru_decks

QUEST_MAGE_CODE = "AAECAf0EBsDBBu7TBpXwBqrRBqvRBsSiBwyBnwSmoASTyQWcxAbNxAaD5gbR+Qa6/QbVhAewiwexiwfaogcAAA=="


def _row(archetype: str, deck_code: str, games: int = 120) -> dict[str, object]:
    return {
        "archetype": archetype,
        "format": "Standard",
        "deck_code": deck_code,
        "games": games,
        "win_rate": 51.0,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@pytest.fixture
def datasets(tmp_path):
    paths = {}

    def dataset_path(source_id: str):
        return paths.setdefault(source_id, tmp_path / f"{source_id}.json")

    def write(source_id: str, payload: dict[str, object]) -> None:
        hsguru_decks.write_json(dataset_path(source_id), payload)
        hsguru_decks._catalog_memory.clear()

    def read(source_id: str) -> dict[str, object]:
        return hsguru_decks.read_json(dataset_path(source_id))

    hsguru_decks._catalog_memory.clear()
    with patch.object(hsguru_decks, "dataset_path", side_effect=dataset_path):
        yield write, read
    hsguru_decks._catalog_memory.clear()


def _catalog(source_id: str, period: str, rows: list[dict[str, object]]) -> dict:
    return {
        "source_id": source_id,
        "state": "ok",
        "fetched_at": datetime.now(UTC).isoformat(),
        "period": period,
        "backend": "flaresolverr",
        "provider_backends": ["flaresolverr"],
        "data": rows,
    }


def _page(rows: list[dict[str, object]]):
    return (
        rows,
        0,
        "flaresolverr",
        [{"backend": "flaresolverr", "state": "accepted", "request_credits": 0}],
    )


def test_fallback_fills_only_empty_archetypes_from_wider_window(datasets) -> None:
    write, read = datasets
    write(
        "hsguru_deck_catalog_standard_all",
        _catalog(
            "hsguru_deck_catalog_standard_all",
            "black_empire",
            [_row("Popular Mage", "POPULAR")],
        ),
    )
    calls: list[tuple[str, str, int]] = []

    async def fetch_chunk(format_name, archetypes, *, period, min_games, **_kwargs):
        archetype = archetypes[0]
        calls.append((archetype, period, min_games))
        if archetype == "Broken Rogue":
            raise RuntimeError("HSGuru deck catalog fetch failed")
        if archetype == "Quest Mage" and min_games == 10:
            return _page([_row("Quest Mage", QUEST_MAGE_CODE, 236)])
        if archetype == "Rare Druid" and min_games == 1:
            return _page([_row("Rare Druid", "RARE", 4)])
        return _page([])

    with (
        patch.object(
            hsguru_decks,
            "_meta_archetypes",
            return_value=[
                "Popular Mage",
                "Quest Mage",
                "Rare Druid",
                "Silent Warrior",
                "Broken Rogue",
            ],
        ),
        patch.object(hsguru_decks, "_fetch_catalog_chunk", side_effect=fetch_chunk),
    ):
        summary = asyncio.run(
            hsguru_decks.refresh_hsguru_fallback_deck_catalog(
                "standard", period="black_empire"
            )
        )

    assert all(archetype != "Popular Mage" for archetype, _, _ in calls)
    assert ("Quest Mage", "past_30_days", 10) in calls
    assert ("Quest Mage", "past_30_days", 1) not in calls
    assert ("Rare Druid", "past_30_days", 1) in calls
    assert summary["state"] == "partial"
    assert summary["with_decks"] == 2
    assert summary["zero_sample_archetypes"] == ["Silent Warrior"]
    assert summary["failed_archetypes"] == ["Broken Rogue"]

    payload = read("hsguru_deck_catalog_standard_fallback")
    assert payload["period"] == "black_empire"
    by_archetype = {row["archetype"]: row for row in payload["data"]}
    assert set(by_archetype) == {"Quest Mage", "Rare Druid"}
    assert by_archetype["Quest Mage"]["sample_period"] == "past_30_days"
    assert by_archetype["Quest Mage"]["sample_min_games"] == 10
    assert by_archetype["Quest Mage"]["sample_fallback_for"] == "black_empire"
    assert by_archetype["Rare Druid"]["sample_min_games"] == 1


def test_fallback_reuses_fresh_builds_and_refetches_stale_ones(datasets) -> None:
    write, _read = datasets
    write(
        "hsguru_deck_catalog_standard_all",
        _catalog("hsguru_deck_catalog_standard_all", "black_empire", []),
    )
    fresh = {
        **_row("Quest Mage", QUEST_MAGE_CODE),
        "sample_period": "past_30_days",
        "sample_fallback_for": "black_empire",
    }
    stale = {
        **_row("Chef Druid", "CHEF"),
        "sample_period": "past_30_days",
        "sample_fallback_for": "black_empire",
        "updated_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
    }
    write(
        "hsguru_deck_catalog_standard_fallback",
        _catalog(
            "hsguru_deck_catalog_standard_fallback", "black_empire", [fresh, stale]
        ),
    )
    fetch_chunk = AsyncMock(return_value=_page([_row("Chef Druid", "CHEF2")]))

    with (
        patch.object(
            hsguru_decks,
            "_meta_archetypes",
            return_value=["Quest Mage", "Chef Druid"],
        ),
        patch.object(hsguru_decks, "_fetch_catalog_chunk", fetch_chunk),
    ):
        summary = asyncio.run(
            hsguru_decks.refresh_hsguru_fallback_deck_catalog(
                "standard", period="black_empire"
            )
        )

    assert fetch_chunk.await_count == 1
    assert fetch_chunk.await_args.args[1] == ["Chef Druid"]
    assert summary["reused"] == 1
    assert summary["with_decks"] == 2


def test_fallback_is_skipped_when_current_window_is_already_widest() -> None:
    fetch_chunk = AsyncMock()
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=[]),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=["Quest Mage"]),
        patch.object(hsguru_decks, "_fetch_catalog_chunk", fetch_chunk),
        patch.object(hsguru_decks, "write_json") as write_json,
    ):
        summary = asyncio.run(
            hsguru_decks.refresh_hsguru_fallback_deck_catalog(
                "standard", period="past_30_days"
            )
        )

    fetch_chunk.assert_not_awaited()
    write_json.assert_not_called()
    assert summary["targets"] == 1
    assert summary["fetched"] == 0


def test_cached_decks_prefer_current_window_then_fallback(datasets) -> None:
    write, _read = datasets
    write(
        "hsguru_deck_catalog_standard_all",
        _catalog(
            "hsguru_deck_catalog_standard_all",
            "black_empire",
            [_row("Popular Mage", "POPULAR")],
        ),
    )
    write(
        "hsguru_deck_catalog_standard_fallback",
        _catalog(
            "hsguru_deck_catalog_standard_fallback",
            "black_empire",
            [
                {
                    **_row("Popular Mage", "OLD_POPULAR"),
                    "sample_period": "past_30_days",
                },
                {
                    **_row("Quest Mage", QUEST_MAGE_CODE, 236),
                    "sample_period": "past_30_days",
                    "sample_rank": "all",
                },
            ],
        ),
    )

    popular = hsguru_decks.cached_hsguru_catalog_decks(
        "Popular Mage", "standard", "all", period="black_empire"
    )
    quest_all = hsguru_decks.cached_hsguru_catalog_decks(
        "Quest Mage", "standard", "all", period="black_empire"
    )
    quest_legend = hsguru_decks.cached_hsguru_catalog_decks(
        "Quest Mage", "standard", "legend", period="black_empire"
    )
    next_period = hsguru_decks.cached_hsguru_catalog_decks(
        "Quest Mage", "standard", "all", period="next_expansion"
    )

    assert [row["deck_code"] for row in popular] == ["POPULAR"]
    assert quest_all[0]["games"] == 236
    assert quest_all[0]["sample_period"] == "past_30_days"
    assert quest_legend[0]["deck_code"] == QUEST_MAGE_CODE
    assert quest_legend[0]["games"] is None
    assert quest_legend[0]["win_rate"] is None
    assert next_period == []


def test_join_keeps_previous_fallback_builds_for_the_same_period() -> None:
    from app.hsguru_meta_matrix import enrich_current_rows_with_cached_decks

    rows = [{"format": "standard", "archetype": "Quest Mage", "decks": []}]
    previous = {
        "data": {
            "structured": {
                "current_catalog": {
                    "archetypes": [
                        {
                            "format": "standard",
                            "archetype": "Quest Mage",
                            "decks": [
                                {
                                    "deck_code": QUEST_MAGE_CODE,
                                    "sample_period": "past_30_days",
                                    "sample_fallback_for": "black_empire",
                                },
                                {
                                    "deck_code": "OLDER",
                                    "sample_period": "past_30_days",
                                    "sample_fallback_for": "patch_36.6.3",
                                },
                            ],
                        }
                    ]
                }
            }
        }
    }

    with patch("app.hsguru_meta_matrix.cached_hsguru_catalog_decks", return_value=[]):
        enrich_current_rows_with_cached_decks(
            rows, previous, sample_period="black_empire"
        )

    assert rows[0]["has_decks"] is True
    assert [deck["deck_code"] for deck in rows[0]["decks"]] == [QUEST_MAGE_CODE]
    assert rows[0]["decks"][0]["sample_period"] == "past_30_days"


def test_catalog_job_runs_fallback_after_all_rank_and_before_join() -> None:
    from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

    events: list[str] = []

    async def refresh(format_name, rank="legend", *, period):
        events.append(f"refresh:{format_name}_{rank}")
        if (format_name, rank) == ("wild", "all"):
            raise RuntimeError("upstream failure")
        return [{"deck_code": f"{format_name}-{rank}"}]

    async def refresh_fallback(format_name, *, period):
        events.append(f"fallback:{format_name}")
        assert period == "black_empire"
        return {"state": "ok", "with_decks": 20, "failed_archetypes": []}

    def join():
        events.append("join")
        return {"joined": True}

    with patch(
        "app.hsguru_deck_catalog_refresh.current_hsguru_deck_period",
        return_value="black_empire",
    ):
        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                refresh_fallback=refresh_fallback,
                join=join,
                load_persisted=lambda _source_id: None,
            )
        )

    assert events.index("fallback:standard") > events.index("refresh:standard_all")
    assert "fallback:wild" not in events
    assert "join" not in events
    assert result["fallbacks"] == {
        "standard": {"state": "ok", "with_decks": 20, "failed_archetypes": []}
    }


def test_catalog_job_reports_fallback_failures_and_still_joins() -> None:
    from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

    async def refresh(format_name, rank="legend", *, period):
        return [{"deck_code": f"{format_name}-{rank}"}]

    async def refresh_fallback(format_name, *, period):
        if format_name == "wild":
            raise RuntimeError("solver down")
        return {"state": "partial", "failed_archetypes": ["Quest Mage"]}

    with patch(
        "app.hsguru_deck_catalog_refresh.current_hsguru_deck_period",
        return_value="black_empire",
    ):
        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                refresh_fallback=refresh_fallback,
                join=lambda: {"joined": True},
                load_persisted=lambda _source_id: None,
            )
        )

    assert result["state"] == "partial"
    assert result["archetype_join"] == {"joined": True}
    assert result["errors"]["standard_fallback"] == (
        "1 archetypes could not be fetched"
    )
    assert result["errors"]["wild_fallback"].startswith("RuntimeError")
