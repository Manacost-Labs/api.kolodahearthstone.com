"""Compact read routes built on stored datasets: hero compositions, Arena
cards, and the hero list that takes its numbers from the fresher list source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from starlette.testclient import TestClient

from app import hsreplay_bg_hero_details as hero_details
from app.source_catalog import source_summary
from app.sources import SOURCE_BY_ID
from app.storage import dataset_path, status_path, write_json

FILTERS = {
    "mmr_percentile": "TOP_50_PERCENT",
    "time_range": "CURRENT_BATTLEGROUNDS_PATCH",
}
NOW = datetime.now(UTC)
DETAILS_AT = (NOW - timedelta(hours=80)).isoformat()
LIST_AT = (NOW - timedelta(hours=1)).isoformat()


def _composition(comp_id: int, name: str, games: int, avg: float) -> dict[str, Any]:
    return {
        "composition_id": comp_id,
        "name": name,
        "num_games": games,
        "avg_placement": avg,
        "placement_distribution": ["12.50%"] * 8,
        "popularity": "10.00%",
        "lineup": [{"id": "BG_1", "attack": 5}],
        "final_form_minions": [{"id": "BG_2"}],
    }


def _hero(dbf_id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "hero": name,
        "dbfId": dbf_id,
        "id": f"HERO_{dbf_id}",
        "tier": "B",
        "pick_rate": "10.00%",
        "pick_rate_value": 10.0,
        "avg_placement": 4.5,
        "adjusted_avg_placement": 4.4,
        "placement_distribution": ["12.50%"] * 8,
        "best_composition_id": 8,
        "best_composition": _composition(8, "Механизмы", 300, 4.1),
        "key_minions_top3": [{"id": "BG_1"}],
        "anomaly_adjusted": False,
        **extra,
    }


def _details_dataset() -> dict[str, Any]:
    heroes = [_hero(101, "Alpha"), _hero(102, "Beta")]
    details = {
        "101": {
            "hero": heroes[0],
            "tavern_up": [{"turn": 1}],
            "hero_power": [{"turn": 1}],
            "compositions": [
                _composition(8, "Механизмы", 300, 4.1),
                _composition(3, "Звери", 120, 4.6),
                _composition(56, "Aberrations", 40, 5.0),
            ],
        },
        "102": {
            "hero": heroes[1],
            "tavern_up": [],
            "hero_power": [],
            "compositions": [_composition(41, "Свинобраз", 77, 3.9)],
        },
    }
    return {
        "state": "ok",
        "fetched_at": DETAILS_AT,
        "data": {
            "structured": {
                "type": "bg_hero_details",
                "fetched_at": DETAILS_AT,
                "filters": FILTERS,
                "heroes": heroes,
                "details": details,
                "duos": {"mode": "duos", "heroes": [_hero(201, "Duo")]},
                "source": {"backend": "hsreplay_json_api"},
            }
        },
    }


def _list_dataset(
    count: int = 30, *, filters: dict[str, str] | None = None
) -> dict[str, Any]:
    rows = [
        {
            "hero": "Альфа",
            "dbfId": 101,
            "id": "HERO_101",
            "tier": "S",
            "pick_rate": "55.55%",
            "avg_placement": "3.91",
            "placement_distribution": ["20.00%"] + ["11.43%"] * 7,
            "best_composition_id": 3,
        }
    ]
    rows += [
        {
            "hero": f"Новый {index}",
            "dbfId": 300 + index,
            "id": f"HERO_{300 + index}",
            "tier": "C",
            "pick_rate": "5.00%",
            "avg_placement": "4.80",
            "placement_distribution": ["12.50%"] * 8,
            "best_composition_id": 2,
        }
        for index in range(count - 1)
    ]
    return {
        "state": "ok",
        "fetched_at": LIST_AT,
        "data": {
            "structured": {
                "type": "bg_heroes",
                "heroes": rows,
                "blocked": False,
                "filters": filters or FILTERS,
            }
        },
    }


def _compositions_dataset() -> dict[str, Any]:
    return {
        "fetched_at": LIST_AT,
        "data": {
            "structured": {
                "type": "bg_compositions",
                "filters": FILTERS,
                "compositions": [
                    {"composition_id": 8, "type": "Механизмы"},
                    {"composition_id": 3, "type": "Beasts"},
                    {"composition_id": 56, "type": "Aberrations"},
                ],
            }
        },
    }


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.hsreplay_bg_hero_details.cards_by_dbfid",
        lambda: {305: {"id": "HERO_305", "name": "Fresh Hero"}},
    )
    hero_details._hero_compositions_cache.clear()
    return tmp_path


@pytest.fixture()
def client(data_dir):
    from app.main import app

    return TestClient(app)


def _save(source_id: str, payload: dict[str, Any]) -> None:
    write_json(dataset_path(source_id), payload)


# --- GET /v1/battlegrounds/heroes/compositions -----------------------------------


def test_hero_compositions_are_compact_and_carry_tribes(client) -> None:
    _save("hsreplay_battlegrounds_hero_details", _details_dataset())
    _save("hsreplay_battlegrounds_compositions", _compositions_dataset())

    response = client.get("/v1/battlegrounds/heroes/compositions")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {
        "source_id": "hsreplay_battlegrounds_hero_details",
        "fetched_at": DETAILS_AT,
        "stale": False,
        "beta": None,
        "serving_cached_dataset": None,
        "cached_after_failure": None,
        "fresh_candidate_published": None,
        "count": 2,
        "limit": None,
        "offset": None,
        "mmr": "TOP_50_PERCENT",
        "time_range": "CURRENT_BATTLEGROUNDS_PATCH",
        "tribes_source_id": "hsreplay_battlegrounds_compositions",
        "tribes_fetched_at": LIST_AT,
    }
    alpha, beta = body["data"]
    assert (alpha["id"], alpha["dbfId"], alpha["hero"]) == ("HERO_101", 101, "Alpha")
    assert alpha["compositions"] == [
        {
            "composition_id": 8,
            "name": "Механизмы",
            "tribes": ["MECHANICAL"],
            "num_games": 300,
            "avg_placement": 4.1,
            "placement_distribution": ["12.50%"] * 8,
        },
        {
            "composition_id": 3,
            "name": "Звери",
            "tribes": ["BEAST"],
            "num_games": 120,
            "avg_placement": 4.6,
            "placement_distribution": ["12.50%"] * 8,
        },
        {
            "composition_id": 56,
            "name": "Aberrations",
            "tribes": [],
            "num_games": 40,
            "avg_placement": 5.0,
            "placement_distribution": ["12.50%"] * 8,
        },
    ]
    # Composition 41 is absent from the statistics set: the stored name decides.
    assert beta["compositions"][0]["tribes"] == ["QUILBOAR"]
    assert "lineup" not in response.text and "tavern_up" not in response.text


def test_hero_compositions_without_the_statistics_set_use_stored_names(client) -> None:
    _save("hsreplay_battlegrounds_hero_details", _details_dataset())

    body = client.get("/v1/battlegrounds/heroes/compositions?mmr=TOP_50_PERCENT").json()

    assert body["meta"]["tribes_source_id"] is None
    assert body["meta"]["tribes_fetched_at"] is None
    assert [c["tribes"] for c in body["data"][0]["compositions"]] == [
        ["MECHANICAL"],
        ["BEAST"],
        [],
    ]


def test_hero_compositions_reject_a_slice_that_is_not_stored(client) -> None:
    _save("hsreplay_battlegrounds_hero_details", _details_dataset())

    response = client.get("/v1/battlegrounds/heroes/compositions?mmr=TOP_20_PERCENT")

    assert response.status_code == 422
    assert "TOP_50_PERCENT" in response.json()["detail"]
    assert (
        client.get("/v1/battlegrounds/heroes/compositions?mmr=top%2050").status_code
        == 422
    )


def test_hero_compositions_404_without_the_bundle(client) -> None:
    assert client.get("/v1/battlegrounds/heroes/compositions").status_code == 404


def test_hero_compositions_follow_a_new_publication(client) -> None:
    dataset = _details_dataset()
    _save("hsreplay_battlegrounds_hero_details", dataset)
    assert (
        client.get("/v1/battlegrounds/heroes/compositions").json()["meta"]["count"] == 2
    )

    del dataset["data"]["structured"]["details"]["102"]
    dataset["data"]["structured"]["heroes"].pop()
    _save("hsreplay_battlegrounds_hero_details", dataset)

    assert (
        client.get("/v1/battlegrounds/heroes/compositions").json()["meta"]["count"] == 1
    )


# --- hero list numbers from the fresher list source ------------------------------


def test_hero_list_takes_numbers_from_the_fresher_list(client) -> None:
    _save("hsreplay_battlegrounds_hero_details", _details_dataset())
    _save("hsreplay_battlegrounds_heroes", _list_dataset())

    payload = client.get("/api/bg/heroes?mode=solo&mmr=TOP_50_PERCENT").json()

    assert payload["fetched_at"] == LIST_AT
    assert payload["list_fetched_at"] == LIST_AT
    assert payload["details_fetched_at"] == DETAILS_AT
    assert payload["list_source_id"] == "hsreplay_battlegrounds_heroes"
    assert payload["count"] == 31
    by_dbf = {row["dbfId"]: row for row in payload["heroes"]}
    alpha = by_dbf[101]
    # List numbers are the fresh ones, in the types the bundle rows use.
    assert alpha["tier"] == "S"
    assert alpha["pick_rate"] == "55.55%" and alpha["pick_rate_value"] == 55.55
    assert alpha["avg_placement"] == 3.91
    assert alpha["placement_distribution"][0] == "20.00%"
    # The best composition changed: its statistics come from the hero's bundle.
    assert alpha["best_composition_id"] == 3
    assert alpha["best_composition"]["name"] == "Звери"
    # Detail-only fields and the stored name are kept.
    assert alpha["hero"] == "Alpha"
    assert alpha["adjusted_avg_placement"] == 4.4
    assert alpha["key_minions_top3"] == [{"id": "BG_1"}]
    # A hero the list does not carry keeps the bundle numbers.
    assert by_dbf[102]["tier"] == "B" and by_dbf[102]["avg_placement"] == 4.5
    # A hero added after the last detail build is listed with list numbers only.
    fresh = by_dbf[305]
    assert fresh["hero"] == "Fresh Hero" and fresh["tier"] == "C"
    assert fresh["pick_rate_value"] == 5.0 and fresh["best_composition"] is None
    assert payload["heroes"][0]["dbfId"] == 101  # S tier sorts first

    v1 = client.get("/v1/battlegrounds/heroes?limit=500").json()
    assert v1["meta"]["fetched_at"] == LIST_AT and v1["meta"]["stale"] is False
    assert v1["meta"]["list_fetched_at"] == LIST_AT
    assert v1["meta"]["details_fetched_at"] == DETAILS_AT
    assert v1["meta"]["list_source_id"] == "hsreplay_battlegrounds_heroes"
    assert v1["meta"]["source_id"] == "hsreplay_battlegrounds_hero_details"
    assert v1["meta"]["count"] == 31


@pytest.mark.parametrize(
    "list_dataset",
    [
        None,
        _list_dataset(
            filters={"mmr_percentile": "TOP_50_PERCENT", "time_range": "LAST_7_DAYS"}
        ),
        _list_dataset(count=5),
        {**_list_dataset(), "fetched_at": DETAILS_AT},
        {**_list_dataset(), "fetched_at": "not a date"},
    ],
    ids=["absent", "other-slice", "too-small", "not-newer", "bad-date"],
)
def test_hero_list_keeps_bundle_numbers_when_the_list_cannot_be_used(
    client, list_dataset
) -> None:
    _save("hsreplay_battlegrounds_hero_details", _details_dataset())
    if list_dataset is not None:
        _save("hsreplay_battlegrounds_heroes", list_dataset)

    payload = client.get("/api/bg/heroes").json()

    assert payload["fetched_at"] == DETAILS_AT
    assert payload["list_fetched_at"] == DETAILS_AT
    assert payload["details_fetched_at"] == DETAILS_AT
    assert payload["list_source_id"] == "hsreplay_battlegrounds_hero_details"
    assert [row["dbfId"] for row in payload["heroes"]] == [101, 102]
    assert payload["heroes"][0]["tier"] == "B"


def test_duos_list_is_not_touched_by_the_solo_list(client) -> None:
    _save("hsreplay_battlegrounds_hero_details", _details_dataset())
    _save("hsreplay_battlegrounds_heroes", _list_dataset())

    payload = client.get("/api/bg/heroes?mode=duos").json()

    assert [row["dbfId"] for row in payload["heroes"]] == [201]
    assert payload["fetched_at"] == DETAILS_AT
    assert payload["list_fetched_at"] == DETAILS_AT


def test_hero_cache_revision_changes_with_either_dataset(data_dir) -> None:
    from app import public_cache

    _save("hsreplay_battlegrounds_hero_details", _details_dataset())
    before = public_cache.cache_revision("/v1/battlegrounds/heroes", b"")
    public_cache._FETCHED_AT.clear()
    _save("hsreplay_battlegrounds_heroes", _list_dataset())

    assert public_cache.cache_revision("/v1/battlegrounds/heroes", b"") != before
    assert DETAILS_AT in public_cache.cache_revision(
        "/v1/battlegrounds/heroes/compositions", b""
    )


# --- GET /v1/arena/cards ---------------------------------------------------------


def _arena_dataset(game_type: str = "BGT_UNDERGROUND_ARENA") -> dict[str, Any]:
    def card(card_id: str, card_class: str, score: float | None, tier: str, win: float):
        return {
            "id": card_id,
            "dbfId": int(card_id.split("_")[1]),
            "name": f"Card {card_id}",
            "cost": 2,
            "type": "MINION",
            "rarity": "COMMON",
            "cardClass": card_class,
            "arena_class": "ALL",
            "tier": tier,
            "score": score,
            "win_rate": win,
            "deck_winrate": f"{win:.2f}%",
            "winrate_when_drawn": "50.00%",
            "winrate_when_played": "51.00%",
            "popularity": 1.5,
            "times_played": 900,
            "field_availability": {"deck_winrate": {"available": True}},
        }

    return {
        "fetched_at": LIST_AT,
        "data": {
            "structured": {
                "type": "arena_card_tiers",
                "cards": [
                    card("X_1", "MAGE", 54.0, "S", 61.1),
                    card("X_2", "NEUTRAL", None, "C", 49.0),
                    card("X_3", "MAGE", 31.5, "D", 44.2),
                ],
                "source": {
                    "api_url": (
                        "https://hsreplay.net/api/v1/arena/card_stats/"
                        f"?ArenaGameTypeFilter={game_type}"
                        "&ArenaTimestampRangeFilter=LAST_4_DAYS&format=json"
                    )
                },
            }
        },
    }


def test_arena_cards_default_fields_and_slice(client) -> None:
    _save("hsreplay_arena_cards_advanced", _arena_dataset())

    body = client.get("/v1/arena/cards").json()

    assert body["data"] == [
        {
            "id": "X_1",
            "score": 54.0,
            "tier": "S",
            "cardClass": "MAGE",
            "win_rate": 61.1,
        },
        {
            "id": "X_2",
            "score": None,
            "tier": "C",
            "cardClass": "NEUTRAL",
            "win_rate": 49.0,
        },
        {
            "id": "X_3",
            "score": 31.5,
            "tier": "D",
            "cardClass": "MAGE",
            "win_rate": 44.2,
        },
    ]
    meta = body["meta"]
    assert meta["source_id"] == "hsreplay_arena_cards_advanced"
    assert meta["fetched_at"] == LIST_AT and meta["stale"] is False
    assert meta["count"] == 3
    assert meta["game_type"] == "underground" and meta["time_range"] == "LAST_4_DAYS"
    assert meta["fields"] == ["id", "score", "tier", "cardClass", "win_rate"]


def test_arena_cards_selects_fields_filters_class_and_pages(client) -> None:
    _save("hsreplay_arena_cards_advanced", _arena_dataset("BGT_ARENA"))

    body = client.get(
        "/v1/arena/cards",
        params={
            "fields": "id, dbfId,id,winrate_when_played",
            "class": "mage",
            "limit": 1,
            "offset": 1,
        },
    ).json()

    assert body["data"] == [{"id": "X_3", "dbfId": 3, "winrate_when_played": "51.00%"}]
    assert body["meta"]["count"] == 2
    assert body["meta"]["limit"] == 1 and body["meta"]["offset"] == 1
    assert body["meta"]["game_type"] == "normal"
    assert body["meta"]["fields"] == ["id", "dbfId", "winrate_when_played"]


def test_arena_cards_slice_is_null_when_the_dataset_does_not_say(client) -> None:
    dataset = _arena_dataset()
    dataset["data"]["structured"]["source"] = {}
    _save("hsreplay_arena_cards_advanced", dataset)

    meta = client.get("/v1/arena/cards").json()["meta"]

    assert meta["game_type"] is None and meta["time_range"] is None


def test_arena_cards_rejects_unknown_fields_and_missing_dataset(client) -> None:
    assert client.get("/v1/arena/cards").status_code == 404
    _save("hsreplay_arena_cards_advanced", _arena_dataset())

    unknown = client.get("/v1/arena/cards?fields=id,field_availability")
    assert (
        unknown.status_code == 422 and "field_availability" in unknown.json()["detail"]
    )
    assert client.get("/v1/arena/cards?fields=,").status_code == 422
    # Another source id cannot be selected: the route has no such parameter.
    other = client.get("/v1/arena/cards?source_id=firestone_arena_cards_normal").json()
    assert other["meta"]["source_id"] == "hsreplay_arena_cards_advanced"


# --- status-only source in the source inventory ----------------------------------


def test_status_only_source_is_fresh_from_its_status(data_dir) -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_compositions_screenshot"]
    write_json(
        status_path(source.id),
        {"source_id": source.id, "state": "ok", "fetched_at": LIST_AT},
    )

    summary = source_summary(source, now=NOW, next_runs={}, projection=None)

    assert summary["freshness_state"] == "fresh" and summary["stale"] is False
    assert summary["last_success_at"] == LIST_AT

    write_json(
        status_path(source.id),
        {"source_id": source.id, "state": "ok", "fetched_at": DETAILS_AT},
    )
    assert (
        source_summary(source, now=NOW, next_runs={}, projection=None)[
            "freshness_state"
        ]
        == "stale"
    )

    write_json(
        status_path(source.id),
        {"source_id": source.id, "state": "fetch_error", "fetched_at": LIST_AT},
    )
    failed = source_summary(source, now=NOW, next_runs={}, projection=None)
    assert failed["freshness_state"] == "missing" and failed["stale"] is True
