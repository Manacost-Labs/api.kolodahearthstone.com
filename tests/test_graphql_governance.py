from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi.testclient import TestClient

from app.graphql_api.repository import PageResult
from app.main import app
from app.redis_cache import TieredCache
from tests.test_graphql_api import FakeRepository
from tests.test_redis_cache import FakeBackend


def _configure(monkeypatch: Any) -> tuple[TestClient, FakeRepository, FakeBackend]:
    repository = FakeRepository()
    backend = FakeBackend()
    cache = TieredCache(backend, max_local_entries=32)
    monkeypatch.setattr(
        "app.graphql_api.router.get_graphql_repository", lambda: repository
    )
    monkeypatch.setattr("app.graphql_api.governance.get_tiered_cache", lambda: cache)
    return TestClient(app), repository, backend


def test_apollo_persisted_query_registers_and_executes_by_hash(
    monkeypatch: Any,
) -> None:
    client, _repository, _backend = _configure(monkeypatch)
    query = "query Health { health { status sourceCount } }"
    digest = hashlib.sha256(query.encode()).hexdigest()
    extensions = {"persistedQuery": {"version": 1, "sha256Hash": digest}}

    registered = client.post(
        "/v1/graphql",
        json={"query": query, "operationName": "Health", "extensions": extensions},
    )
    reused = client.post(
        "/v1/graphql",
        json={"operationName": "Health", "extensions": extensions},
    )

    assert registered.status_code == 200
    assert reused.status_code == 200
    assert reused.json() == registered.json()
    assert reused.headers["x-koloda-cache"] in {"LOCAL", "REDIS"}


def test_persisted_query_unknown_and_hash_mismatch_are_safe(monkeypatch: Any) -> None:
    client, _repository, _backend = _configure(monkeypatch)
    digest = "a" * 64

    missing = client.post(
        "/v1/graphql",
        json={"extensions": {"persistedQuery": {"version": 1, "sha256Hash": digest}}},
    )
    mismatch = client.post(
        "/v1/graphql",
        json={
            "query": "query { health { status } }",
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": digest}},
        },
    )

    assert missing.status_code == 200
    assert missing.json()["errors"][0]["extensions"]["code"] == (
        "PERSISTED_QUERY_NOT_FOUND"
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["errors"][0]["extensions"]["code"] == (
        "PERSISTED_QUERY_HASH_MISMATCH"
    )


def test_graphql_complexity_guard_runs_before_repository(monkeypatch: Any) -> None:
    client, repository, _backend = _configure(monkeypatch)
    monkeypatch.setenv("HS_GRAPHQL_MAX_COMPLEXITY", "100")

    response = client.post(
        "/v1/graphql",
        json={"query": "query { cards(limit: 100) { items { cardId nameRu } } }"},
    )

    assert response.status_code == 200
    assert response.json()["errors"][0]["extensions"]["code"] == "QUERY_TOO_COMPLEX"
    assert repository.last_call is None


def test_graphql_response_cache_skips_second_repository_call(monkeypatch: Any) -> None:
    client, repository, _backend = _configure(monkeypatch)
    calls = 0

    async def cards(**filters: Any) -> PageResult:
        nonlocal calls
        calls += 1
        return await FakeRepository.cards(repository, **filters)

    repository.cards = cards  # type: ignore[method-assign]
    query = "query { cards(limit: 1) { items { cardId } } }"

    first = client.post("/v1/graphql", json={"query": query})
    second = client.post("/v1/graphql", json={"query": query})

    assert first.status_code == second.status_code == 200
    assert first.headers["x-koloda-cache"] == "MISS"
    assert second.headers["x-koloda-cache"] == "LOCAL"
    assert calls == 1


def test_graphql_credentials_bypass_shared_response_cache(monkeypatch: Any) -> None:
    client, repository, _backend = _configure(monkeypatch)
    calls = 0

    async def health() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return await FakeRepository.health(repository)

    repository.health = health  # type: ignore[method-assign]
    request = {
        "json": {"query": "query { health { status } }"},
        "headers": {"Authorization": "Bearer invalid-but-unused-on-public-query"},
    }

    first = client.post("/v1/graphql", **request)
    second = client.post("/v1/graphql", **request)

    assert first.status_code == second.status_code == 200
    assert first.headers["x-koloda-cache"] == "BYPASS"
    assert second.headers["x-koloda-cache"] == "BYPASS"
    assert calls == 2


def test_graphql_execution_has_a_deadline(monkeypatch: Any) -> None:
    client, repository, _backend = _configure(monkeypatch)
    monkeypatch.setenv("HS_GRAPHQL_TIMEOUT_SECONDS", "0.1")

    async def health() -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return await FakeRepository.health(repository)

    repository.health = health  # type: ignore[method-assign]
    response = client.post(
        "/v1/graphql",
        json={"query": "query { health { status } }"},
    )

    assert response.status_code == 504
    assert response.json()["errors"][0]["extensions"]["code"] == "QUERY_TIMEOUT"


def test_graphql_response_size_is_bounded(monkeypatch: Any) -> None:
    client, _repository, _backend = _configure(monkeypatch)
    monkeypatch.setenv("HS_GRAPHQL_MAX_RESPONSE_BYTES", "16384")

    query = 'query { dataset(sourceId: "large") { payload } }'
    from tests.test_graphql_api import FakeRepository as BaseRepository

    repository = BaseRepository()

    async def dataset(source_id: str, dataset_version: str | None) -> dict[str, Any]:
        row = await BaseRepository.dataset(repository, source_id, dataset_version)
        assert row is not None
        row["payload"] = {"content": "x" * 20_000}
        return row

    repository.dataset = dataset  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.graphql_api.router.get_graphql_repository", lambda: repository
    )
    response = client.post("/v1/graphql", json={"query": query})

    assert response.status_code == 413
    assert response.json()["errors"][0]["extensions"]["code"] == ("RESPONSE_TOO_LARGE")
    assert len(response.content) < 1_000
