"""The Bans-window archetype catalog and fuller HSGuru deck coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app import hsguru_decks
from app.main import app

client = TestClient(app)

BAN = "black_empire_ban"


def _slice(format_name: str, rank: str, period: str, rows: list[dict]) -> dict:
    format_id = 2 if format_name == "standard" else 1
    return {
        "key": f"{format_name}|{rank}|{period}|any_player",
        "format": format_name,
        "rank": rank,
        "period": period,
        "coin": "any_player",
        "source_url": (
            f"https://www.hsguru.com/meta?format={format_id}&rank={rank}"
            f"&period={period}&min_games=10"
        ),
        "fetched_at": "2026-10-25T00:00:00+00:00",
        "rows": rows,
        "quality": {"duplicate_rows_merged": 0, "duplicate_groups": []},
    }


def _meta_row(archetype: str, games: int) -> dict:
    return {
        "archetype": archetype,
        "winrate": 51.0,
        "popularity": 1.0,
        "games": games,
        "turns": 8.0,
        "duration_minutes": 7.0,
        "climbing_speed": 0.5,
    }


def test_ban_catalog_is_built_from_the_all_rank_slices_with_ban_builds() -> None:
    from app.hsguru_meta_matrix import build_period_catalog

    slices = [
        _slice(
            "standard",
            "all",
            BAN,
            [_meta_row("Zoolock", 900), _meta_row("Egg Paladin", 12)],
        ),
        _slice("wild", "all", BAN, [_meta_row("Pirate Rogue", 300)]),
        # Another rank or window never leaks into the Bans catalog.
        _slice("standard", "legend", BAN, [_meta_row("Legend Only", 500)]),
        _slice("standard", "all", "black_empire", [_meta_row("Release Only", 500)]),
    ]
    periods: list[str] = []

    def cached_decks(archetype, format_name, rank, *, period):
        periods.append(period)
        if archetype == "Zoolock":
            return [{"deck_code": "AAECAZoo", "games": 84, "win_rate": 55.0}]
        return []

    with patch(
        "app.hsguru_meta_matrix.cached_hsguru_catalog_decks",
        side_effect=cached_decks,
    ):
        catalog = build_period_catalog(
            BAN, slices, None, joined_at="2026-10-25T00:00:00+00:00"
        )

    assert catalog["criteria"] == {
        "period": BAN,
        "rank": "all",
        "minimum_games": 10,
        "formats": ["standard", "wild"],
    }
    assert [(row["format"], row["archetype"]) for row in catalog["archetypes"]] == [
        ("standard", "Zoolock"),
        ("standard", "Egg Paladin"),
        ("wild", "Pirate Rogue"),
    ]
    assert all(row["period"] == BAN for row in catalog["archetypes"])
    assert set(periods) == {BAN}
    zoolock = catalog["archetypes"][0]
    assert zoolock["has_decks"] is True
    assert zoolock["decks"][0]["sample_period"] == BAN
    assert catalog["coverage"]["standard"] == {
        "archetypes": 2,
        "with_decks": 1,
        "decks": 1,
        "games": 912,
    }
    assert catalog["deck_catalog"]["source_ids"] == [
        f"hsguru_deck_catalog_standard_all_{BAN}",
        f"hsguru_deck_catalog_wild_all_{BAN}",
    ]


def test_deck_join_also_refreshes_builds_of_the_ban_catalog() -> None:
    from app.hsguru_meta_matrix import refresh_current_catalog_deck_join

    def catalog(period: str) -> dict:
        return {
            "criteria": {"period": period},
            "archetypes": [
                {
                    "format": "standard",
                    "archetype": "Zoolock",
                    "games": 900,
                    "decks": [],
                }
            ],
        }

    dataset = {
        "data": {
            "structured": {
                "current_catalog": catalog("black_empire"),
                "period_catalogs": {BAN: catalog(BAN)},
            }
        }
    }
    joined: list[str] = []

    def attach(rows, _cached, *, sample_period):
        joined.append(sample_period)
        rows[0]["deck_count"] = 2 if sample_period == BAN else 1
        rows[0]["has_decks"] = True

    with (
        patch("app.hsguru_meta_matrix.load_dataset", return_value=dataset),
        patch(
            "app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks",
            side_effect=attach,
        ),
        patch("app.hsguru_meta_matrix.save_dataset"),
    ):
        result = refresh_current_catalog_deck_join()

    assert joined == ["black_empire", BAN]
    assert result["period_coverage"][BAN]["standard"]["decks"] == 2
    ban = dataset["data"]["structured"]["period_catalogs"][BAN]
    assert ban["deck_catalog"]["sample_period"] == BAN


def _matrix(period_catalogs: dict | None = None) -> dict:
    def row(archetype: str, games: int, period: str) -> dict:
        return {
            "format": "standard",
            "archetype": archetype,
            "games": games,
            "winrate": 50.0,
            "popularity_pct": 1.0,
            "period": period,
            "decks": [{"deck_code": "AAECA"}] if games >= 50 else [],
        }

    return {
        "fetched_at": datetime.now(UTC).isoformat(),
        "data": {
            "structured": {
                "current_catalog": {
                    "criteria": {"period": "black_empire", "rank": "all"},
                    "archetypes": [row("Release Mage", 500, "black_empire")],
                },
                "period_catalogs": period_catalogs
                if period_catalogs is not None
                else {
                    BAN: {
                        "criteria": {"period": BAN, "rank": "all"},
                        "coverage": {"standard": {"archetypes": 2}},
                        "archetypes": [
                            row("Zoolock", 900, BAN),
                            row("Egg Paladin", 12, BAN),
                        ],
                    }
                },
            }
        },
    }


def test_v1_hsguru_archetypes_serves_the_ban_catalog_by_period() -> None:
    with patch("app.routers.hsguru_meta.load_dataset", return_value=_matrix()):
        ban = client.get(f"/v1/hsguru/archetypes?period={BAN}")
        current = client.get("/v1/hsguru/archetypes")
        busy = client.get(f"/v1/hsguru/archetypes?period={BAN}&min_games=50")

    assert ban.status_code == 200
    body = ban.json()
    # The default floor is everything collected, down to 10 games.
    assert [row["archetype"] for row in body["data"]] == ["Zoolock", "Egg Paladin"]
    assert body["criteria"]["period"] == BAN
    assert body["meta"]["available_periods"] == ["black_empire", BAN]
    assert body["meta"]["current_period"] == "black_empire"
    assert [row["archetype"] for row in current.json()["data"]] == ["Release Mage"]
    assert [row["archetype"] for row in busy.json()["data"]] == ["Zoolock"]


def test_v1_hsguru_archetypes_rejects_an_unpublished_period() -> None:
    with patch("app.routers.hsguru_meta.load_dataset", return_value=_matrix()):
        response = client.get("/v1/hsguru/archetypes?period=past_day")

    assert response.status_code == 422
    assert response.json()["detail"] == {"allowed_periods": ["black_empire", BAN]}


def test_v1_hsguru_archetypes_reports_a_missing_ban_catalog_as_unavailable() -> None:
    with patch(
        "app.routers.hsguru_meta.load_dataset",
        return_value=_matrix(period_catalogs={}),
    ):
        response = client.get(f"/v1/hsguru/archetypes?period={BAN}")

    assert response.status_code == 422
    assert response.json()["detail"] == {"allowed_periods": ["black_empire"]}


def test_v1_hsguru_meta_serves_bronze_plat_and_low_min_games_views() -> None:
    dataset = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "data": {
            "structured": {
                "dimensions": {"periods": [BAN]},
                "slices": [
                    _slice(
                        "standard",
                        "bronze_plat",
                        BAN,
                        [_meta_row("Zoolock", 900), _meta_row("Egg Paladin", 12)],
                    )
                ],
            }
        },
    }
    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        low = client.get(f"/v1/hsguru/meta?rank=bronze_plat&period={BAN}&min_games=10")
        default = client.get(f"/v1/hsguru/meta?rank=bronze_plat&period={BAN}")

    assert low.status_code == 200
    assert [row["archetype"] for row in low.json()["data"]["items"]] == [
        "Zoolock",
        "Egg Paladin",
    ]
    # The default view keeps its former 100-game floor.
    assert [row["archetype"] for row in default.json()["data"]["items"]] == ["Zoolock"]


def test_ban_deck_catalog_has_its_own_storage_and_targets() -> None:
    assert (
        hsguru_decks._catalog_source_id("standard", "all", BAN)
        == f"hsguru_deck_catalog_standard_all_{BAN}"
    )
    assert (
        hsguru_decks._catalog_source_id("standard", "all", "black_empire")
        == "hsguru_deck_catalog_standard_all"
    )
    with patch.object(
        hsguru_decks,
        "load_resolved_public_dataset",
        return_value=_matrix(),
    ):
        ban_targets = hsguru_decks._meta_archetypes("standard", period=BAN)
        current_targets = hsguru_decks._meta_archetypes(
            "standard", period="black_empire"
        )
        periods = hsguru_decks.hsguru_catalog_periods()

    # Egg Paladin (12 games) is listed, but HSGuru lists no build below 50 games.
    assert ban_targets == ["Zoolock"]
    assert current_targets == ["Release Mage"]
    assert periods == (BAN,)


def _snapshot(rows: list[dict], fetched_at: datetime) -> hsguru_decks._CatalogSnapshot:
    return hsguru_decks._CatalogSnapshot(
        rows=rows,
        period="black_empire",
        fetched_at=fetched_at.isoformat(),
        backend="flaresolverr",
        provider_backends=("flaresolverr",),
        collection_version=hsguru_decks._CATALOG_COLLECTION_VERSION,
    )


def _run_all_rank(snapshot, existing, targets, fetched):
    fetch = AsyncMock(return_value=(fetched, 0, ["flaresolverr"], []))
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=snapshot),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=targets),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", fetch),
        patch.object(hsguru_decks, "_write_catalog") as write,
    ):
        rows = asyncio.run(
            hsguru_decks._refresh_all_rank_catalog("standard", period="black_empire")
        )
    return rows, fetch, write


def test_covered_all_rank_catalog_is_reread_once_it_is_a_day_old() -> None:
    old = {"archetype": "Zoolock", "deck_code": "AAECAOld", "games": 60}
    fresh = {"archetype": "Zoolock", "deck_code": "AAECANew", "games": 90}

    stale = _snapshot([old], datetime.now(UTC) - timedelta(hours=21))
    rows, fetch, write = _run_all_rank(stale, [old], ["Zoolock"], [fresh])

    assert fetch.await_args.args[1] == ["Zoolock"]
    # The stale build does not survive next to the re-read archetype.
    assert rows == [fresh]
    assert write.call_args.args[2] == [fresh]

    recent = _snapshot([old], datetime.now(UTC) - timedelta(hours=2))
    rows, fetch, write = _run_all_rank(recent, [old], ["Zoolock"], [fresh])

    assert rows == [old]
    fetch.assert_not_awaited()
    write.assert_not_called()


def test_catalog_pages_ask_scrape_do_first_for_the_full_50_game_page() -> None:
    page = hsguru_decks._CatalogPage(html="", backend="scrape_do", request_credits=10)
    fetch_page = AsyncMock(return_value=page)
    with (
        patch.object(hsguru_decks, "_fetch_catalog_page", fetch_page),
        patch.object(hsguru_decks, "parse_hsguru_decks_html", return_value=[]),
        patch.object(hsguru_decks, "_validate_parsed_catalog_rows"),
        patch.object(hsguru_decks, "_canonicalize_catalog_archetypes", AsyncMock()),
    ):
        asyncio.run(
            hsguru_decks._fetch_catalog_chunk(
                "standard", ["Zoolock", "Egg Paladin"], batch_number=1, period=BAN
            )
        )

    call = fetch_page.await_args
    assert "min_games=50" in call.args[0].url
    assert "limit=200" in call.args[0].url
    assert call.args[0].id == f"hsguru_deck_catalog_standard_all_{BAN}"
    assert call.kwargs["prefer_local_solver"] is False
    assert call.kwargs["timeout_ms"] == 90_000


def test_catalog_pages_share_the_configured_scrape_do_concurrency(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HS_HSGURU_DECK_CATALOG_CONCURRENCY", "15")
    active = 0
    peak = 0

    async def fetch_page(source, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return hsguru_decks._CatalogPage(
            html="", backend="scrape_do", request_credits=0
        )

    hsguru_decks.reset_hsguru_catalog_provider_state()
    with (
        patch.object(hsguru_decks, "_fetch_catalog_page", side_effect=fetch_page),
        patch.object(hsguru_decks, "parse_hsguru_decks_html", return_value=[]),
        patch.object(hsguru_decks, "_validate_parsed_catalog_rows"),
    ):
        asyncio.run(
            hsguru_decks._fetch_catalog_chunks(
                "wild",
                [f"Archetype {index}" for index in range(40)],
                period=BAN,
                size=1,
            )
        )

    assert peak == 15


def test_deck_catalog_concurrency_settings_are_bounded(monkeypatch) -> None:
    from app import config

    monkeypatch.delenv("HS_HSGURU_DECK_CATALOG_CONCURRENCY", raising=False)
    monkeypatch.delenv("HS_HSGURU_DECK_CATALOG_LOCAL_FIRST", raising=False)
    assert config.hsguru_deck_catalog_concurrency() == 15
    assert config.hsguru_deck_catalog_local_concurrency() == 2
    assert config.hsguru_deck_catalog_local_first() is False
    monkeypatch.setenv("HS_HSGURU_DECK_CATALOG_CONCURRENCY", "500")
    monkeypatch.setenv("HS_HSGURU_DECK_CATALOG_LOCAL_FIRST", "1")
    assert config.hsguru_deck_catalog_concurrency() == 50
    assert config.hsguru_deck_catalog_local_first() is True


def test_catalog_written_under_older_rules_is_reread_in_full() -> None:
    old = {"archetype": "Zoolock", "deck_code": "AAECAOld", "games": 60}
    fresh = {"archetype": "Zoolock", "deck_code": "AAECANew", "games": 90}
    recent_but_old_rules = hsguru_decks._CatalogSnapshot(
        rows=[old],
        period="black_empire",
        fetched_at=datetime.now(UTC).isoformat(),
        backend="flaresolverr",
        provider_backends=("flaresolverr",),
    )
    rows, fetch, _write = _run_all_rank(
        recent_but_old_rules, [old], ["Zoolock"], [fresh]
    )

    assert fetch.await_args.args[1] == ["Zoolock"]
    assert rows == [fresh]


def test_catalog_job_refreshes_ban_builds_after_the_current_window() -> None:
    from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

    events: list[str] = []

    async def refresh(format_name, rank="legend", *, period):
        events.append(f"refresh:{format_name}_{rank}:{period}")
        return [{"deck_code": f"{format_name}-{rank}-{period}"}]

    async def refresh_fallback(format_name, *, period):
        events.append(f"fallback:{format_name}:{period}")
        return {"state": "ok", "failed_archetypes": []}

    def join():
        events.append("join")
        return {"ok": True}

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
                catalog_periods=lambda: (BAN,),
            )
        )

    assert events == [
        "refresh:standard_legend:black_empire",
        "refresh:wild_legend:black_empire",
        "refresh:standard_all:black_empire",
        "refresh:wild_all:black_empire",
        "fallback:standard:black_empire",
        "fallback:wild:black_empire",
        f"refresh:standard_all:{BAN}",
        f"refresh:wild_all:{BAN}",
        "join",
    ]
    assert result["state"] == "ok"
    assert result["secondary_periods"] == [BAN]
    assert result[f"standard_all_{BAN}_decks"] == 1


def test_ban_build_failure_degrades_the_job_but_still_joins() -> None:
    from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

    async def refresh(format_name, rank="legend", *, period):
        if period == BAN and format_name == "wild":
            raise RuntimeError("solver down")
        return [{"deck_code": f"{format_name}-{rank}"}]

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
                catalog_periods=lambda: (BAN,),
            )
        )

    assert result["state"] == "partial"
    assert f"wild_all_{BAN}" in result["errors"]
    assert result["datasets"][f"standard_all_{BAN}"] == {"decks": 1, "state": "ok"}
    assert result["archetype_join"] == {"ok": True}


def test_rune_spread_deck_names_join_the_requested_archetype() -> None:
    rows = [
        {"archetype": "Some custom title", "deck_code": "AAECAEgg"},
        {"archetype": "BBB Harold DK", "deck_code": "AAECAHarold"},
        {"archetype": "XL Druid", "deck_code": "AAECAXL"},
        {"archetype": "Unrequested Mage", "deck_code": "AAECAMage"},
    ]
    info = {
        "AAECAEgg": {"archetype": "BUU Egg DK"},
        "AAECAHarold": {"archetype": "BBB Harold DK"},
        "AAECAXL": {"archetype": "XL Druid"},
        "AAECAMage": {"archetype": "Burn Mage"},
    }

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return info

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    with patch.object(hsguru_decks.httpx, "AsyncClient", Client):
        asyncio.run(
            hsguru_decks._canonicalize_catalog_archetypes(
                rows, ["Egg DK", "Harold DK", "XL Druid", "Druid"]
            )
        )

    assert [row["archetype"] for row in rows] == [
        "Egg DK",
        "Harold DK",
        "XL Druid",
        "Burn Mage",
    ]
