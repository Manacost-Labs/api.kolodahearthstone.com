from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.graphql_api.repository import PageResult, RepositoryUnavailable
from app.main import app

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.last_call: tuple[str, dict[str, Any]] | None = None

    async def health(self) -> dict[str, Any]:
        return {
            "checked_at": NOW,
            "source_count": 7,
            "unhealthy_source_count": 0,
            "latest_sync_at": NOW,
        }

    async def cards(self, **filters: Any) -> PageResult:
        self.last_call = ("cards", filters)
        return PageResult(
            items=[
                {
                    "collection": "constructed",
                    "card_id": "EX1_001",
                    "dbf": 1,
                    "name_ru": "Тестовая карта",
                    "name_en": "Test Card",
                    "card_type": "MINION",
                    "mana_cost": 2,
                    "attack": 2,
                    "health": 3,
                    "image_url": "https://images.example/card.png",
                    "is_active": True,
                    "updated_at": NOW,
                }
            ],
            total=3,
        )

    async def card(self, card_id: str, collection: str | None) -> dict[str, Any] | None:
        result = await self.cards(limit=1, offset=0)
        return result.items[0] if card_id == "EX1_001" else None

    async def battleground_heroes(self, **filters: Any) -> PageResult:
        self.last_call = ("battleground_heroes", filters)
        return PageResult(items=[], total=0)

    async def statistics(self, **filters: Any) -> PageResult:
        self.last_call = ("statistics", filters)
        return PageResult(items=[], total=119_218)

    async def archetypes(self, **filters: Any) -> PageResult:
        self.last_call = ("archetypes", filters)
        return PageResult(items=[], total=69)

    async def battleground_minions(self, **filters: Any) -> PageResult:
        self.last_call = ("battleground_minions", filters)
        return PageResult(items=[], total=498)

    async def sources(self, **filters: Any) -> PageResult:
        self.last_call = ("sources", filters)
        return PageResult(items=[], total=7)

    async def datasets(self, **filters: Any) -> PageResult:
        self.last_call = ("datasets", filters)
        return PageResult(items=[], total=46)

    async def dataset(
        self, source_id: str, dataset_version: str | None
    ) -> dict[str, Any] | None:
        self.last_call = (
            "dataset",
            {"source_id": source_id, "dataset_version": dataset_version},
        )
        return {
            "source_id": source_id,
            "dataset_version": dataset_version or "latest-version",
            "fetched_at": NOW,
            "state": "ok",
            "imported_at": NOW,
            "payload_type": "object",
            "payload_bytes": 42,
            "payload": {"rows": [{"id": 1}]},
        }

    async def collections(self, **filters: Any) -> PageResult:
        self.last_call = ("collections", filters)
        return PageResult(
            items=[
                {
                    "schema_name": "catalog",
                    "name": "cards",
                    "collection": "catalog.cards",
                    "table_type": "BASE TABLE",
                    "estimated_row_count": 10_623,
                    "columns": [
                        {
                            "name": "card_id",
                            "data_type": "text",
                            "database_type": "text",
                            "nullable": False,
                        }
                    ],
                    "primary_key": ["card_id"],
                }
            ],
            total=54,
        )

    async def records(self, **filters: Any) -> PageResult:
        self.last_call = ("records", filters)
        return PageResult(items=[{"card_id": "EX1_001"}], total=10_623)

    async def close(self) -> None:
        return None


def _post(
    query: str,
    fake: FakeRepository,
    monkeypatch: Any,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: fake)
    return TestClient(app).post(
        "/v1/", json={"query": query}, headers=headers, follow_redirects=True
    )


def test_graphql_v1_serves_health_and_paginated_cards(monkeypatch: Any) -> None:
    fake = FakeRepository()
    response = _post(
        """
        query Catalog {
          health { status databaseConnected sourceCount }
          cards(search: " Test ", limit: 1, offset: 1) {
            items { cardId nameRu imageUrl }
            pageInfo { limit offset total hasNextPage }
          }
        }
        """,
        fake,
        monkeypatch,
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "health": {
                "status": "ok",
                "databaseConnected": True,
                "sourceCount": 7,
            },
            "cards": {
                "items": [
                    {
                        "cardId": "EX1_001",
                        "nameRu": "Тестовая карта",
                        "imageUrl": "https://images.example/card.png",
                    }
                ],
                "pageInfo": {
                    "limit": 1,
                    "offset": 1,
                    "total": 3,
                    "hasNextPage": True,
                },
            },
        }
    }
    assert fake.last_call == (
        "cards",
        {
            "search": "Test",
            "collection": None,
            "card_type": None,
            "active": None,
            "limit": 1,
            "offset": 1,
        },
    )


def test_canonical_graphql_endpoint_matches_legacy_contract(monkeypatch: Any) -> None:
    fake = FakeRepository()
    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: fake)
    client = TestClient(app)

    response = client.post(
        "/v1/graphql",
        json={"query": "query { health { status sourceCount } }"},
    )
    discovery = client.get("/v1/graphql")

    assert response.status_code == 200
    assert response.json() == {
        "data": {"health": {"status": "ok", "sourceCount": 7}}
    }
    assert discovery.status_code == 200
    assert discovery.json() == {
        "name": "Koloda Hearthstone GraphQL API",
        "version": "v1",
        "status": "ok",
        "endpoint": "https://api.kolodahearthstone.com/v1/graphql",
        "method": "POST",
    }

    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/v1/graphql"]["post"].get("deprecated") is not True
    assert paths["/v1/"]["post"]["deprecated"] is True


def test_graphql_rejects_unbounded_page(monkeypatch: Any) -> None:
    response = _post(
        "query { cards(limit: 201) { pageInfo { total } } }",
        FakeRepository(),
        monkeypatch,
    )

    assert response.status_code == 200
    error = response.json()["errors"][0]
    assert error["message"] == "limit must be between 1 and 200"
    assert error["extensions"]["code"] == "VALIDATION_ERROR"


def test_graphql_is_read_only_and_get_is_discovery_only(monkeypatch: Any) -> None:
    fake = FakeRepository()
    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: fake)
    client = TestClient(app)

    get_response = client.get("/v1/", params={"query": "{ health { status } }"})
    mutation_response = client.post("/v1/", json={"query": "mutation { refreshAll }"})

    assert get_response.status_code == 200
    assert get_response.json() == {
        "name": "Koloda Hearthstone GraphQL API",
        "version": "v1",
        "status": "ok",
        "endpoint": "https://api.kolodahearthstone.com/v1/",
        "method": "POST",
    }
    assert mutation_response.status_code == 200
    assert (
        "Schema is not configured to execute mutation operation"
        in mutation_response.json()["errors"][0]["message"]
    )


def test_graphql_masks_repository_details(monkeypatch: Any) -> None:
    class UnavailableRepository(FakeRepository):
        async def health(self) -> dict[str, Any]:
            raise RepositoryUnavailable("postgresql://user:secret@postgres/hs_data")

    response = _post(
        "query { health { status } }", UnavailableRepository(), monkeypatch
    )

    error = response.json()["errors"][0]
    assert error["message"] == "The central data store is temporarily unavailable"
    assert error["extensions"]["code"] == "SERVICE_UNAVAILABLE"
    assert "secret" not in response.text


def test_graphql_exposes_complete_dataset_for_legacy_migration(
    monkeypatch: Any,
) -> None:
    fake = FakeRepository()
    response = _post(
        """
        query {
          dataset(sourceId: "hsguru_meta", datasetVersion: "version-1") {
            sourceId
            datasetVersion
            payloadBytes
            payload
          }
        }
        """,
        fake,
        monkeypatch,
    )

    assert response.status_code == 200
    assert response.json()["data"]["dataset"] == {
        "sourceId": "hsguru_meta",
        "datasetVersion": "version-1",
        "payloadBytes": 42,
        "payload": {"rows": [{"id": 1}]},
    }
    assert fake.last_call == (
        "dataset",
        {"source_id": "hsguru_meta", "dataset_version": "version-1"},
    )


def test_graphql_full_database_access_requires_api_key(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.config.api_key", lambda: "expected-key")
    response = _post(
        "query { collections { pageInfo { total } } }",
        FakeRepository(),
        monkeypatch,
    )

    error = response.json()["errors"][0]
    assert error["message"] == "Missing or invalid X-API-Key"
    assert error["extensions"]["code"] == "UNAUTHORIZED"


def test_graphql_lists_and_reads_every_database_collection(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.config.api_key", lambda: "expected-key")
    fake = FakeRepository()
    response = _post(
        """
        query FullDatabase {
          collections(schemaName: "catalog", limit: 1) {
            items {
              collection
              estimatedRowCount
              primaryKey
              columns { name dataType nullable }
            }
            pageInfo { total hasNextPage }
          }
          records(
            collection: "catalog.cards"
            fields: ["card_id"]
            filters: {card_id: "EX1_001"}
            limit: 1
          ) {
            items
            pageInfo { total }
          }
        }
        """,
        fake,
        monkeypatch,
        headers={"X-API-Key": "expected-key"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "collections": {
                "items": [
                    {
                        "collection": "catalog.cards",
                        "estimatedRowCount": 10_623,
                        "primaryKey": ["card_id"],
                        "columns": [
                            {"name": "card_id", "dataType": "text", "nullable": False}
                        ],
                    }
                ],
                "pageInfo": {"total": 54, "hasNextPage": True},
            },
            "records": {
                "items": [{"card_id": "EX1_001"}],
                "pageInfo": {"total": 10_623},
            },
        }
    }
    assert fake.last_call == (
        "records",
        {
            "collection": "catalog.cards",
            "fields": ["card_id"],
            "filters": {"card_id": "EX1_001"},
            "order_by": None,
            "descending": False,
            "limit": 1,
            "offset": 0,
        },
    )
