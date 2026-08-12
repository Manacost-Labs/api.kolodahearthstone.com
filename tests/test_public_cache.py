from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from starlette.testclient import TestClient

from app.main import app
from app.public_cache import PUBLIC_CACHE_CONTROL, cache_revision
from app.storage import save_dataset

client = TestClient(app)


def test_canonical_v1_get_returns_etag_and_conditional_304() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    payload = {
        "count": 1,
        "fetched_at": fetched_at,
        "heroes": [{"hero": "A.F. Kay", "dbfId": 1}],
    }
    with patch("app.hsreplay_bg_hero_details.list_bg_heroes", return_value=payload):
        first = client.get(
            "/v1/battlegrounds/heroes",
            headers={"Origin": "https://api.kolodahearthstone.com"},
        )
        second = client.get(
            "/v1/battlegrounds/heroes",
            headers={
                "If-None-Match": first.headers["etag"],
                "Origin": "https://api.kolodahearthstone.com",
            },
        )

    assert first.status_code == 200
    assert first.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert first.headers["etag"].startswith('"')
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == first.headers["etag"]
    assert second.headers["access-control-allow-origin"] == "https://api.kolodahearthstone.com"


def test_canonical_battlegrounds_uses_dataset_specific_cache_revision() -> None:
    with (
        patch(
            "app.public_cache._dataset_timestamp",
            return_value="hero-details-revision",
        ) as dataset_timestamp,
        patch("app.public_cache._latest_dataset_timestamp") as latest_timestamp,
    ):
        revision = cache_revision("/v1/battlegrounds/heroes", b"mode=solo")

    assert revision == "hero-details-revision"
    dataset_timestamp.assert_called_once_with(
        "hsreplay_battlegrounds_hero_details"
    )
    latest_timestamp.assert_not_called()


def test_dataset_etag_changes_with_fetched_at() -> None:
    source_id = "hsreplay_arena"
    first_time = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    save_dataset(
        source_id, {"source_id": source_id, "fetched_at": first_time, "data": {}}
    )
    first = client.get(f"/datasets/{source_id}")

    second_time = datetime.now(UTC).isoformat()
    save_dataset(
        source_id, {"source_id": source_id, "fetched_at": second_time, "data": {}}
    )
    second = client.get(f"/datasets/{source_id}")

    assert first.status_code == second.status_code == 200
    assert first.headers["etag"] != second.headers["etag"]


def test_health_and_ui_are_not_publicly_cached() -> None:
    health = client.get("/health")
    assert "etag" not in health.headers
    assert "cache-control" not in health.headers


def test_legacy_api_shape_is_unchanged_and_gets_cache_headers() -> None:
    expected = {
        "type": "bg_heroes",
        "mode": "solo",
        "count": 1,
        "fetched_at": None,
        "filters": {},
        "source": {},
        "heroes": [{"hero": "A.F. Kay"}],
    }
    with patch("app.hsreplay_bg_hero_details.list_bg_heroes", return_value=expected):
        response = client.get("/api/bg/heroes")

    assert response.status_code == 200
    assert response.json() == expected
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert "etag" in response.headers


def test_cache_revision_is_best_effort_when_storage_is_unavailable() -> None:
    with patch("app.public_cache.load_dataset", side_effect=PermissionError("denied")):
        assert cache_revision("/datasets", b"") == "not-cached"


def test_hsguru_legend_deck_revision_reads_only_its_catalog() -> None:
    with (
        patch(
            "app.public_cache._dataset_timestamp", return_value="catalog-revision"
        ) as dataset_timestamp,
        patch("app.public_cache._latest_dataset_timestamp") as latest_timestamp,
        patch(
            "app.hsguru_decks.hsguru_matrix_cache_revision",
            return_value="matrix-revision",
        ),
    ):
        revision = cache_revision(
            "/v1/constructed/hsguru-deck",
            b"archetype=XL+Mill+Druid&format_name=wild&rank=legend",
        )

    assert revision == "catalog-revision:matrix-revision"
    dataset_timestamp.assert_called_once_with("hsguru_deck_catalog_wild_legend")
    latest_timestamp.assert_not_called()


def test_hsguru_all_rank_deck_revision_reads_its_preloaded_catalog() -> None:
    with (
        patch(
            "app.public_cache._dataset_timestamp", return_value="all-catalog-revision"
        ) as dataset_timestamp,
        patch("app.public_cache._latest_dataset_timestamp") as latest_timestamp,
        patch(
            "app.hsguru_decks.hsguru_matrix_cache_revision",
            return_value="matrix-revision",
        ),
    ):
        revision = cache_revision(
            "/v1/constructed/hsguru-deck",
            b"archetype=XL+HL+Exodia+Mage&format_name=wild&rank=all",
        )

    assert revision == "all-catalog-revision:matrix-revision"
    dataset_timestamp.assert_called_once_with("hsguru_deck_catalog_wild_all")
    latest_timestamp.assert_not_called()


def test_hsguru_other_rank_revision_uses_the_all_rank_fallback_catalog() -> None:
    with (
        patch(
            "app.public_cache._dataset_timestamp", return_value="all-catalog-revision"
        ) as dataset_timestamp,
        patch("app.public_cache._latest_dataset_timestamp") as latest_timestamp,
        patch(
            "app.hsguru_decks.hsguru_matrix_cache_revision",
            return_value="matrix-revision",
        ),
    ):
        revision = cache_revision(
            "/v1/constructed/hsguru-deck",
            b"archetype=Weapon+Rogue&format_name=wild&rank=diamond_4to1",
        )

    assert revision == "all-catalog-revision:matrix-revision"
    dataset_timestamp.assert_called_once_with("hsguru_deck_catalog_wild_all")
    latest_timestamp.assert_not_called()


def test_hsguru_archetype_revision_tracks_deck_join() -> None:
    with patch(
        "app.hsguru_decks.hsguru_matrix_cache_revision",
        return_value="patch_36.2.0:joined-at",
    ):
        revision = cache_revision("/v1/hsguru/archetypes", b"format=wild")

    assert revision == "patch_36.2.0:joined-at"


def test_live_hsguru_deck_endpoint_never_returns_disk_based_304() -> None:
    first_row = {
        "archetype": "Big Shaman",
        "format": "Wild",
        "deck_code": "AAECAaoIFirstLiveDeckCode1234567890==",
    }
    second_row = {
        **first_row,
        "deck_code": "AAECAaoISecondLiveDeckCode123456789==",
    }
    url = (
        "/v1/constructed/hsguru-deck?"
        "archetype=Big%20Shaman&format_name=wild&rank=legend"
    )
    with patch(
        "app.routers.constructed.exact_hsguru_decks",
        side_effect=[[first_row], [second_row]],
    ):
        first = client.get(url)
        second = client.get(
            url,
            headers={"If-None-Match": first.headers.get("etag", '"stale"')},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "etag" not in first.headers
    assert first.json()["data"][0]["deck_code"] != second.json()["data"][0]["deck_code"]


def _consumer_decks_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decks (
          id INTEGER, source_id TEXT, title TEXT, archetype TEXT, class TEXT,
          format TEXT, deck_code TEXT, win_rate REAL, updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO decks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            "consumer-test",
            "Spell Mage",
            "Spell Mage",
            "Mage",
            "standard",
            "AAECA-test",
            52.5,
            "2026-07-12T08:00:00+00:00",
        ),
    )
    return conn


def test_deckview_legacy_decks_contract_remains_unchanged() -> None:
    with patch(
        "app.db.get_db_connection",
        side_effect=[_consumer_decks_connection(), _consumer_decks_connection()],
    ):
        response = client.get("/api/db/decks?source_id=consumer-test&limit=50&offset=0")

    assert response.status_code == 200
    assert response.json() == {
        "total": 1,
        "limit": 50,
        "offset": 0,
        "decks": [
            {
                "id": 1,
                "source_id": "consumer-test",
                "title": "Spell Mage",
                "archetype": "Spell Mage",
                "class": "Mage",
                "format": "standard",
                "deck_code": "AAECA-test",
                "win_rate": 52.5,
                "updated_at": "2026-07-12T08:00:00+00:00",
            }
        ],
    }
