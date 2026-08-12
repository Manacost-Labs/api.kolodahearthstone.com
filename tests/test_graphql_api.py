from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.graphql_api.repository import PageResult, RepositoryUnavailable
from app.main import app

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _statistic_row(patch: str, win_rate: float) -> dict[str, Any]:
    return {
        "snapshot_id": 42 if patch == "34.0" else 43,
        "source_id": "hsguru_meta",
        "dataset_version": f"version-{patch}",
        "domain": "constructed_meta",
        "snapshot_entity_type": "archetype",
        "format_name": "wild",
        "rank_range": "diamond_legend",
        "period": "current",
        "mode": "default",
        "rating_bracket": "all",
        "patch": patch,
        "snapshot_source_url": "https://example.test/meta",
        "fetched_at": NOW,
        "metadata": {},
        "entity_key": "wild:reno-priest",
        "entity_type": "archetype",
        "card_id": None,
        "dbf_id": None,
        "name": "Reno Priest",
        "name_ru": "Рено Жрец",
        "class_name": "PRIEST",
        "tier": "2",
        "games": 1000,
        "win_rate": win_rate,
        "popularity": 3.5,
        "pick_rate": None,
        "avg_placement": None,
        "score": None,
        "image_url": None,
        "source_url": "https://example.test/archetype",
        "metrics": {},
    }


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
            has_next_page=True,
            next_cursor={"values": ["constructed", "Тестовая карта", "EX1_001"]},
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

    async def statistic_history(self, **filters: Any) -> PageResult:
        self.last_call = ("statistic_history", filters)
        patch = str(filters.get("patch") or "34.1")
        win_rate = 51.0 if patch == "34.0" else 53.5
        return PageResult(items=[_statistic_row(patch, win_rate)], total=2)

    async def archetypes(self, **filters: Any) -> PageResult:
        self.last_call = ("archetypes", filters)
        return PageResult(items=[], total=69)

    async def battleground_minions(self, **filters: Any) -> PageResult:
        self.last_call = ("battleground_minions", filters)
        return PageResult(items=[], total=498)

    async def sources(self, **filters: Any) -> PageResult:
        self.last_call = ("sources", filters)
        return PageResult(items=[], total=7)

    async def search(self, **filters: Any) -> PageResult:
        self.last_call = ("search", filters)
        return PageResult(
            items=[
                {
                    "kind": "card",
                    "entity_id": "constructed:EX1_001",
                    "name": "Тестовая карта",
                    "name_ru": "Тестовая карта",
                    "subtitle": "constructed · MINION",
                    "image_url": "https://images.example/card.png",
                    "source_id": "constructed",
                    "updated_at": NOW,
                    "metadata": {"cardId": "EX1_001"},
                    "search_rank": 0,
                    "search_name": "тестовая карта",
                }
            ],
            total=1,
        )

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
            "after": None,
            "limit": 1,
            "offset": 1,
        },
    )


def test_graphql_cards_cursor_is_opaque_and_forwarded(monkeypatch: Any) -> None:
    fake = FakeRepository()
    first = _post(
        "query { cards(limit: 1) { pageInfo { hasNextPage nextCursor } } }",
        fake,
        monkeypatch,
    )
    cursor = first.json()["data"]["cards"]["pageInfo"]["nextCursor"]

    response = _post(
        f'query {{ cards(limit: 1, after: "{cursor}") '
        "{ items { cardId } pageInfo { nextCursor } } }",
        fake,
        monkeypatch,
    )

    assert response.status_code == 200
    assert "errors" not in response.json()
    assert fake.last_call == (
        "cards",
        {
            "search": None,
            "collection": None,
            "card_type": None,
            "active": None,
            "after": ("constructed", "Тестовая карта", "EX1_001"),
            "limit": 1,
            "offset": 0,
        },
    )


def test_graphql_cursor_is_available_on_every_large_collection(
    monkeypatch: Any,
) -> None:
    fake = FakeRepository()
    method_names = (
        "battleground_heroes",
        "statistics",
        "statistic_history",
        "archetypes",
        "battleground_minions",
        "sources",
        "search",
        "datasets",
        "collections",
        "records",
    )
    for method_name in method_names:
        original = getattr(fake, method_name)

        async def paged(_original: Any = original, **filters: Any) -> PageResult:
            result = await _original(**filters)
            return PageResult(
                items=result.items,
                total=max(1, result.total),
                has_next_page=True,
                next_cursor={"values": ["next"]},
            )

        setattr(fake, method_name, paged)

    monkeypatch.setattr("app.config.api_key", lambda: "expected-key")
    response = _post(
        """
        query {
          battlegroundHeroes(limit: 1) { pageInfo { nextCursor } }
          statistics(limit: 1) { pageInfo { nextCursor } }
          statisticHistory(entityKey: "wild:reno-priest", limit: 1) {
            pageInfo { nextCursor }
          }
          archetypes(limit: 1) { pageInfo { nextCursor } }
          battlegroundMinions(limit: 1) { pageInfo { nextCursor } }
          sources(limit: 1) { pageInfo { nextCursor } }
          search(query: "test", limit: 1) { pageInfo { nextCursor } }
          datasets(limit: 1) { pageInfo { nextCursor } }
          collections(limit: 1) { pageInfo { nextCursor } }
          records(collection: "catalog.cards", limit: 1) {
            pageInfo { nextCursor }
          }
        }
        """,
        fake,
        monkeypatch,
        headers={"X-API-Key": "expected-key"},
    )

    assert response.status_code == 200
    assert "errors" not in response.json()
    assert all(
        connection["pageInfo"]["nextCursor"]
        for connection in response.json()["data"].values()
    )


def test_graphql_cursor_is_bound_to_collection_filters(monkeypatch: Any) -> None:
    fake = FakeRepository()
    first = _post(
        'query { cards(collection: "constructed", limit: 1) '
        "{ pageInfo { nextCursor } } }",
        fake,
        monkeypatch,
    )
    cursor = first.json()["data"]["cards"]["pageInfo"]["nextCursor"]

    response = _post(
        f'query {{ cards(collection: "battlegrounds", after: "{cursor}") '
        "{ pageInfo { total } } }",
        fake,
        monkeypatch,
    )

    assert response.json()["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["message"] == (
        "after is not a valid cards cursor"
    )


def test_graphql_unified_search_filters_kinds_and_returns_common_shape(
    monkeypatch: Any,
) -> None:
    fake = FakeRepository()
    response = _post(
        """
        query {
          search(query: " карта ", kinds: [CARD, HERO], limit: 10) {
            items { kind entityId name nameRu subtitle imageUrl sourceId metadata }
            pageInfo { total hasNextPage }
          }
        }
        """,
        fake,
        monkeypatch,
    )

    assert response.status_code == 200
    assert response.json()["data"]["search"]["items"][0] == {
        "kind": "CARD",
        "entityId": "constructed:EX1_001",
        "name": "Тестовая карта",
        "nameRu": "Тестовая карта",
        "subtitle": "constructed · MINION",
        "imageUrl": "https://images.example/card.png",
        "sourceId": "constructed",
        "metadata": {"cardId": "EX1_001"},
    }
    assert fake.last_call == (
        "search",
        {
            "query": "карта",
            "kinds": ["card", "hero"],
            "after": None,
            "limit": 10,
            "offset": 0,
        },
    )


def test_graphql_compares_statistic_snapshots_between_patches(
    monkeypatch: Any,
) -> None:
    response = _post(
        """
        query {
          compareStatisticPatches(
            entityKey: "wild:reno-priest"
            fromPatch: "34.0"
            toPatch: "34.1"
            formatName: "wild"
          ) {
            entityKey fromPatch toPatch
            before { snapshotId patch winRate }
            after { snapshotId patch winRate }
            deltas { metric beforeValue afterValue absoluteChange percentChange }
          }
        }
        """,
        FakeRepository(),
        monkeypatch,
    )

    assert response.status_code == 200
    comparison = response.json()["data"]["compareStatisticPatches"]
    assert comparison["before"] == {
        "snapshotId": 42,
        "patch": "34.0",
        "winRate": 51.0,
    }
    assert comparison["after"] == {
        "snapshotId": 43,
        "patch": "34.1",
        "winRate": 53.5,
    }
    win_rate = next(
        delta for delta in comparison["deltas"] if delta["metric"] == "win_rate"
    )
    assert win_rate["absoluteChange"] == 2.5
    assert round(win_rate["percentChange"], 2) == 4.9


def test_graphql_cards_rejects_invalid_or_ambiguous_cursor(monkeypatch: Any) -> None:
    invalid = _post(
        'query { cards(after: "not-a-cursor") { pageInfo { total } } }',
        FakeRepository(),
        monkeypatch,
    )
    ambiguous = _post(
        'query { cards(after: "not-a-cursor", offset: 1) { pageInfo { total } } }',
        FakeRepository(),
        monkeypatch,
    )

    assert invalid.json()["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"
    assert invalid.json()["errors"][0]["message"] == "after is not a valid cards cursor"
    assert ambiguous.json()["errors"][0]["message"] == (
        "after and a non-zero offset cannot be combined"
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
    assert response.json() == {"data": {"health": {"status": "ok", "sourceCount": 7}}}
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
            "after": None,
            "limit": 1,
            "offset": 0,
        },
    )
