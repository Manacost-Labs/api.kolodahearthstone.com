from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from starlette.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_v1_bg_heroes_envelope_and_pagination() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    payload = {
        "count": 2,
        "fetched_at": fetched_at,
        "heroes": [
            {"hero": "A.F. Kay", "dbfId": 1, "pick_rate": "8.0%"},
            {"hero": "Ragnaros", "dbfId": 2, "pick_rate": "7.0%"},
        ],
    }
    with patch("app.hsreplay_bg_hero_details.list_bg_heroes", return_value=payload):
        legacy_response = client.get("/v1/bg/heroes?limit=1&offset=1")
        response = client.get("/v1/battlegrounds/heroes?limit=1&offset=1")

    assert legacy_response.status_code == 200
    assert response.status_code == 200
    assert response.json() == legacy_response.json()
    body = response.json()
    assert [row["hero"] for row in body["data"]] == ["Ragnaros"]
    assert body["meta"]["count"] == 2
    assert body["meta"]["stale"] is False


def test_v1_bg_minions_has_concrete_schema() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    with patch(
        "app.hsreplay_bg_minions_db.list_minion_snapshots",
        return_value={
            "total": 1,
            "minions": [
                {"dbf_id": 42, "name": "Tarecgosa", "tavern_tier": 4, "fetched_at": fetched_at}
            ],
        },
    ):
        legacy_response = client.get("/v1/bg/minions")
        response = client.get("/v1/battlegrounds/minions")

    assert legacy_response.status_code == 200
    assert response.status_code == 200
    assert response.json() == legacy_response.json()
    assert response.json()["data"][0]["dbf_id"] == 42
    schema = client.get("/openapi.json").json()["components"]["schemas"]["BgMinionRow"]
    assert schema["properties"]["dbf_id"]["type"] == "integer"

    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/v1/bg/minions"]["get"]["deprecated"] is True
    assert paths["/v1/battlegrounds/minions"]["get"].get("deprecated") is not True


def test_v1_arena_classes_reads_cached_snapshot() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "fetched_at": fetched_at,
        "data": {
            "structured": {
                "type": "arena_class_pages",
                "classes": [
                    {
                        "class": "Mage",
                        "win_rate": 52.1,
                        "pick_rate": 9.4,
                        "pct_7_plus": 68.0,
                        "num_drafts": 1000,
                    }
                ],
            }
        },
    }
    with patch("app.routers.arena.load_dataset", return_value=dataset):
        response = client.get("/v1/arena/classes")

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "class": "Mage",
                "win_rate": 52.1,
                "pick_rate": 9.4,
                "pct_7_plus": 68.0,
                "num_drafts": 1000,
            }
        ],
        "meta": {
            "source_id": "hsreplay_arena_class_pages_firecrawl",
            "fetched_at": fetched_at,
            "stale": False,
            "count": 1,
            "limit": 20,
            "offset": 0,
        },
    }


def test_v1_arena_rejects_unknown_source() -> None:
    response = client.get("/v1/arena/classes?source_id=unknown")
    assert response.status_code == 422
