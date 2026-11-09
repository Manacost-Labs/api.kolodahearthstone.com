"""The current-patch archetype catalog: dynamic number, borrowed builds, cost."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from app import hsguru_decks
from app.main import app
from tests.test_hsguru_ban_catalog import BAN
from tests.test_hsguru_ban_catalog import _matrix as _ban_matrix
from tests.test_hsguru_fallback_decks import _catalog, _page, _row
from tests.test_hsguru_fallback_decks import datasets as _datasets

client = TestClient(app)
datasets = _datasets  # the temp-dir dataset storage fixture of the fallback tests


def _patch_catalog(period: str) -> dict:
    return {
        "criteria": {"period": period, "rank": "all"},
        "coverage": {"standard": {"archetypes": 1}},
        "archetypes": [
            {
                "format": "standard",
                "archetype": "Patch Mage",
                "games": 300,
                "winrate": 52.0,
                "popularity_pct": 2.0,
                "period": period,
                "decks": [{"deck_code": "AAECA", "sample_period": period}],
            }
        ],
    }


def _matrix(patch_period: str | None) -> dict:
    matrix = _ban_matrix()
    if patch_period:
        matrix["data"]["structured"]["period_catalogs"][patch_period] = _patch_catalog(
            patch_period
        )
    return matrix


def test_archetypes_serve_the_published_patch_window() -> None:
    with patch(
        "app.routers.hsguru_meta.load_dataset", return_value=_matrix("patch_37.0.2")
    ):
        response = client.get(
            "/v1/hsguru/archetypes?format=standard&period=patch_37.0.2"
        )

    body = response.json()
    assert response.status_code == 200
    assert body["criteria"]["period"] == "patch_37.0.2"
    assert [row["archetype"] for row in body["data"]] == ["Patch Mage"]
    assert body["meta"]["available_periods"] == ["black_empire", BAN, "patch_37.0.2"]
    assert body["meta"]["current_patch_period"] == "patch_37.0.2"


def test_a_new_patch_replaces_the_old_one_without_code_changes() -> None:
    # The matrix run publishes whichever patch it selected; nothing names 37.0.2.
    with patch(
        "app.routers.hsguru_meta.load_dataset", return_value=_matrix("patch_37.0.4")
    ):
        new = client.get("/v1/hsguru/archetypes?period=patch_37.0.4")
        old = client.get("/v1/hsguru/archetypes?period=patch_37.0.2")

    assert new.status_code == 200
    assert new.json()["meta"]["current_patch_period"] == "patch_37.0.4"
    assert old.status_code == 422
    assert old.json()["detail"] == {
        "allowed_periods": ["black_empire", BAN, "patch_37.0.4"]
    }


def test_default_and_ban_answers_keep_their_data_next_to_a_patch_catalog() -> None:
    with patch(
        "app.routers.hsguru_meta.load_dataset", return_value=_matrix("patch_37.0.2")
    ):
        with_patch = (
            client.get("/v1/hsguru/archetypes").json(),
            client.get(f"/v1/hsguru/archetypes?period={BAN}").json(),
        )
    with patch("app.routers.hsguru_meta.load_dataset", return_value=_matrix(None)):
        without = (
            client.get("/v1/hsguru/archetypes").json(),
            client.get(f"/v1/hsguru/archetypes?period={BAN}").json(),
        )

    for after, before in zip(with_patch, without, strict=True):
        assert after["data"] == before["data"]
        assert after["criteria"] == before["criteria"]
    assert without[0]["meta"]["available_periods"] == ["black_empire", BAN]
    assert without[0]["meta"]["current_patch_period"] is None


def test_patch_window_is_not_a_separate_catalog_when_it_is_the_current_one() -> None:
    matrix = _matrix(None)
    structured = matrix["data"]["structured"]
    structured["current_catalog"]["criteria"]["period"] = "patch_37.0.2"
    structured["period_catalogs"]["patch_37.0.2"] = _patch_catalog("patch_37.0.2")
    with patch("app.routers.hsguru_meta.load_dataset", return_value=matrix):
        body = client.get("/v1/hsguru/archetypes").json()

    assert body["meta"]["available_periods"] == ["patch_37.0.2", BAN]
    assert body["meta"]["current_patch_period"] is None


def _refresh_matrix(current: str, patch_period: str) -> dict:
    from app.firecrawl_backend import FirecrawlScrape
    from app.hsguru_meta_matrix import refresh_hsguru_meta_matrix
    from tests.test_hsguru_meta_matrix import HSGURU_TABLE

    async def scrape(spec):
        return FirecrawlScrape(
            html=HSGURU_TABLE,
            markdown="",
            screenshot=None,
            metadata={"creditsUsed": 1},
            status_code=200,
            final_url=spec.url,
        )

    async def scrape_current(format_name, period):
        row = {
            "format": format_name,
            "format_id": 2 if format_name == "standard" else 1,
            "archetype": "Quest Mage",
            "games": 742,
            "winrate": 51.1,
            "popularity_pct": 0.8,
            "period": period,
            "rank": "all",
            "decks": [],
        }
        return [row], {
            "format": format_name,
            "backend": "firecrawl",
            "request_credits": 1,
        }

    async def discover_patch(_cached):
        return current, {"selected_patch_period": patch_period, "backend": "firecrawl"}

    with (
        patch("app.hsguru_meta_matrix.load_dataset", return_value=None),
        patch("app.hsguru_meta_matrix._record_current_history"),
        patch("app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks"),
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
        patch("app.hsguru_meta_matrix.save_status"),
    ):
        asyncio.run(
            refresh_hsguru_meta_matrix(
                concurrency=5,
                attempts=1,
                scrape=scrape,
                scrape_current=scrape_current,
                discover_patch=discover_patch,
            )
        )
    return save_dataset.call_args.args[1]["data"]["structured"]


@pytest.mark.parametrize("patch_period", ["patch_37.0.2", "patch_37.0.4"])
def test_matrix_publishes_the_catalog_of_the_patch_it_selected(patch_period) -> None:
    structured = _refresh_matrix("black_empire", patch_period)

    catalogs = structured["period_catalogs"]
    assert list(catalogs) == [BAN, patch_period]
    catalog = catalogs[patch_period]
    assert catalog["criteria"]["period"] == patch_period
    assert {row["format"] for row in catalog["archetypes"]} == {"standard", "wild"}
    assert all(row["period"] == patch_period for row in catalog["archetypes"])
    # Builds are looked up in the patch's own suffixed catalog files.
    assert catalog["deck_catalog"]["source_ids"] == [
        f"hsguru_deck_catalog_standard_all_{patch_period}",
        f"hsguru_deck_catalog_wild_all_{patch_period}",
    ]
    assert catalog["deck_catalog"]["sample_period"] == patch_period


def test_matrix_builds_no_patch_catalog_when_the_patch_is_the_current_window() -> None:
    structured = _refresh_matrix("patch_37.0.2", "patch_37.0.2")

    assert list(structured["period_catalogs"]) == [BAN]


def test_patch_catalogs_are_stored_beside_the_release_one() -> None:
    with patch.object(
        hsguru_decks, "_current_deck_period", return_value="black_empire"
    ):
        assert (
            hsguru_decks._catalog_source_id("wild", "all", "patch_37.0.4")
            == "hsguru_deck_catalog_wild_all_patch_37.0.4"
        )
        assert (
            hsguru_decks._catalog_source_id("wild", "all", "black_empire")
            == "hsguru_deck_catalog_wild_all"
        )
        assert hsguru_decks._fallback_windows("patch_37.0.4") == (
            ("black_empire", 50),
            ("past_30_days", 50),
        )
        assert hsguru_decks._replay_windows("patch_37.0.4") == (
            "patch_37.0.4",
            "black_empire",
        )
    with patch.object(
        hsguru_decks, "_current_deck_period", return_value="patch_37.0.4"
    ):
        assert (
            hsguru_decks._catalog_source_id("wild", "all", "patch_37.0.4")
            == "hsguru_deck_catalog_wild_all"
        )


def test_published_patch_is_a_catalog_period_and_wild_is_skipped_without_slice() -> (
    None
):
    matrix = _matrix("patch_37.0.4")
    with patch.object(
        hsguru_decks, "load_resolved_public_dataset", return_value=matrix
    ):
        assert hsguru_decks.hsguru_catalog_periods() == (BAN, "patch_37.0.4")
        # Only Standard rows exist for the patch: no Wild pages are bought.
        assert hsguru_decks.hsguru_catalog_period_formats("patch_37.0.4") == (
            "standard",
        )


def test_job_collects_the_patch_after_bans_and_only_for_formats_it_has() -> None:
    from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

    patch_period = "patch_37.0.4"
    events: list[str] = []

    async def refresh(format_name, rank="legend", *, period):
        events.append(f"refresh:{format_name}_{rank}:{period}")
        return [{"deck_code": "x"}]

    async def refresh_fallback(format_name, *, period):
        events.append(f"fallback:{format_name}:{period}")
        return {"state": "ok", "failed_archetypes": []}

    with patch(
        "app.hsguru_deck_catalog_refresh.current_hsguru_deck_period",
        return_value="black_empire",
    ):
        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                refresh_fallback=refresh_fallback,
                join=lambda: {"ok": True},
                load_persisted=lambda _source_id: None,
                catalog_periods=lambda: (BAN, patch_period),
                period_formats=lambda period: (
                    ("standard",) if period == patch_period else ("standard", "wild")
                ),
            )
        )

    assert events[-4:] == [
        f"refresh:wild_all:{BAN}",
        f"fallback:wild:{BAN}",
        f"refresh:standard_all:{patch_period}",
        f"fallback:standard:{patch_period}",
    ]
    assert f"refresh:wild_all:{patch_period}" not in events
    assert f"fallback:wild:{patch_period}" not in events
    assert result["state"] == "ok"
    assert result["secondary_periods"] == [BAN, patch_period]


def test_patch_builds_failure_keeps_bans_and_the_current_join() -> None:
    from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

    patch_period = "patch_37.0.4"

    async def refresh(format_name, rank="legend", *, period):
        if period == patch_period:
            raise RuntimeError("solver down")
        return [{"deck_code": f"{format_name}-{period}"}]

    async def refresh_fallback(format_name, *, period):
        return {"state": "ok", "failed_archetypes": []}

    with patch(
        "app.hsguru_deck_catalog_refresh.current_hsguru_deck_period",
        return_value="black_empire",
    ):
        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                refresh_fallback=refresh_fallback,
                join=lambda: {"ok": True},
                load_persisted=lambda _source_id: None,
                catalog_periods=lambda: (BAN, patch_period),
            )
        )

    assert result["state"] == "partial"
    assert result["datasets"][f"standard_all_{BAN}"]["state"] == "ok"
    assert f"standard_all_{patch_period}" in result["errors"]
    assert result["archetype_join"] == {"ok": True}


PATCH = "patch_37.0.4"


def _patch_fallback(datasets_fixture, *, targets, replay=None):
    """Run the patch fallback with every network helper stubbed and counted."""
    _write, read = datasets_fixture
    chunk = AsyncMock(return_value=_page([]))
    replays = replay or AsyncMock(return_value=([], 0, [], [], None))
    with (
        patch.object(hsguru_decks, "_current_deck_period", return_value="black_empire"),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=targets),
        patch.object(hsguru_decks, "_fetch_catalog_chunk", chunk),
        patch.object(hsguru_decks, "_fetch_replay_decks", replays),
    ):
        summary = asyncio.run(
            hsguru_decks.refresh_hsguru_fallback_deck_catalog("standard", period=PATCH)
        )
    return (
        summary,
        chunk,
        replays,
        read(f"hsguru_deck_catalog_standard_fallback_{PATCH}"),
    )


def test_patch_borrows_the_release_builds_before_paying_for_any_page(datasets) -> None:
    write, _read = datasets
    write(
        "hsguru_deck_catalog_standard_all",
        _catalog(
            "hsguru_deck_catalog_standard_all",
            "black_empire",
            [{**_row("Release Rogue", "AAECARel"), "sample_period": "black_empire"}],
        ),
    )
    month = {
        **_row("Month Druid", "AAECAMon"),
        "sample_period": "past_30_days",
        "sample_fallback_for": "black_empire",
    }
    write(
        "hsguru_deck_catalog_standard_fallback",
        {
            **_catalog(
                "hsguru_deck_catalog_standard_fallback", "black_empire", [month]
            ),
            "replay_checks": {"Dead Hunter": datetime.now(UTC).isoformat()},
        },
    )
    write(
        f"hsguru_deck_catalog_standard_all_{PATCH}",
        _catalog(f"hsguru_deck_catalog_standard_all_{PATCH}", PATCH, []),
    )

    summary, chunk, replays, payload = _patch_fallback(
        datasets,
        targets=["Release Rogue", "Month Druid", "Dead Hunter", "Other Release Deck"],
    )

    # Two builds come from the release files, one empty replay lookup is
    # inherited: nothing is bought for them.
    labels = {
        row["archetype"]: (row["sample_period"], row["sample_fallback_for"])
        for row in payload["data"]
    }
    assert labels == {
        "Release Rogue": ("black_empire", PATCH),
        "Month Druid": ("past_30_days", PATCH),
    }
    # The release window is not asked again: it was read in full already.
    asked = [(call.kwargs["period"], call.args[1]) for call in chunk.await_args_list]
    assert asked == [("past_30_days", ["Other Release Deck"])]
    assert [call.args[1] for call in replays.await_args_list] == ["Other Release Deck"]
    assert summary["with_decks"] == 2


def test_patch_without_a_release_catalog_still_asks_the_release_window(
    datasets,
) -> None:
    _summary, chunk, _replays, _payload = _patch_fallback(
        datasets, targets=["Quiet Mage"]
    )

    assert [call.kwargs["period"] for call in chunk.await_args_list] == [
        "black_empire",
        "past_30_days",
    ]


def test_bans_fallback_does_not_borrow_from_the_release_files(datasets) -> None:
    write, read = datasets
    write(
        "hsguru_deck_catalog_standard_all",
        _catalog(
            "hsguru_deck_catalog_standard_all",
            "black_empire",
            [_row("Release Rogue", "AAECARel")],
        ),
    )
    chunk = AsyncMock(return_value=_page([]))
    with (
        patch.object(hsguru_decks, "_current_deck_period", return_value="black_empire"),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=["Release Rogue"]),
        patch.object(hsguru_decks, "_fetch_catalog_chunk", chunk),
        patch.object(
            hsguru_decks,
            "_fetch_replay_decks",
            AsyncMock(return_value=([], 0, [], [], None)),
        ),
    ):
        asyncio.run(
            hsguru_decks.refresh_hsguru_fallback_deck_catalog("standard", period=BAN)
        )

    assert [call.kwargs["period"] for call in chunk.await_args_list] == [
        "black_empire",
        "past_30_days",
    ]
    assert read(f"hsguru_deck_catalog_standard_fallback_{BAN}")["data"] == []
