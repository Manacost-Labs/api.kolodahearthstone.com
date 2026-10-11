from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.material_embeddings import DIMENSIONS, EmbeddingsUnavailable
from app.material_search import search_materials
from app.material_search_mcp import material_api_lifespan, mcp_app
from app.routers.material_search import router


@pytest.fixture(autouse=True)
def clean_result_cache():
    from app.material_search import _results

    _results.clear()
    yield
    _results.clear()


def row(id="constructed:CARD", name="Тестовая карта"):
    return {
        "kind": "card",
        "entity_id": id,
        "name": name,
        "content": "Предсмертный хрип: призывает существо.",
        "metadata": {"cardId": id.split(":")[-1]},
        "image_url": "https://example.test/card.png",
    }


def test_exact_name_uses_no_openrouter_call(monkeypatch):
    monkeypatch.setenv("HS_SEARCH_ENABLED", "true")
    lexical = AsyncMock(return_value=[row()])
    query = AsyncMock(side_effect=AssertionError("Exact lookup must not embed"))
    monkeypatch.setattr("app.material_search.material_repository.lexical", lexical)
    monkeypatch.setattr(
        "app.material_search.material_repository.documents",
        AsyncMock(return_value=[row()]),
    )
    monkeypatch.setattr("app.material_search.embedding_client.query", query)
    result = asyncio.run(search_materials("Тестовая карта"))
    assert result["items"][0]["id"] == "card:constructed:CARD"
    assert result["meta"]["mode"] == "lexical"
    query.assert_not_called()


def test_hybrid_hydrates_live_results_and_drops_deleted_materials(monkeypatch):
    monkeypatch.setenv("HS_SEARCH_ENABLED", "true")
    monkeypatch.setattr(
        "app.material_search.material_repository.lexical", AsyncMock(return_value=[])
    )
    documents = AsyncMock(return_value=[row(name="Новое название")])
    monkeypatch.setattr("app.material_search.material_repository.documents", documents)
    monkeypatch.setattr(
        "app.material_search.material_index.load", lambda: ([], None, time.time())
    )
    monkeypatch.setattr(
        "app.material_search.material_index.candidates",
        lambda *args: (["card:deleted", "card:constructed:CARD"], time.time()),
    )
    monkeypatch.setattr(
        "app.material_search.embedding_client.query",
        AsyncMock(return_value=np.ones(DIMENSIONS)),
    )
    result = asyncio.run(search_materials("призывает после смерти", ["card"]))
    assert [item["title"] for item in result["items"]] == ["Новое название"]
    assert result["items"][0]["image_url"]
    assert result["meta"]["mode"] == "hybrid"


def test_openrouter_failure_returns_lexical_results(monkeypatch):
    monkeypatch.setenv("HS_SEARCH_ENABLED", "true")
    monkeypatch.setattr(
        "app.material_search.material_repository.lexical",
        AsyncMock(return_value=[row()]),
    )
    monkeypatch.setattr(
        "app.material_search.material_repository.documents",
        AsyncMock(return_value=[row()]),
    )
    monkeypatch.setattr(
        "app.material_search.material_index.load", lambda: ([], None, time.time())
    )
    monkeypatch.setattr(
        "app.material_search.embedding_client.query",
        AsyncMock(side_effect=EmbeddingsUnavailable()),
    )
    result = asyncio.run(search_materials("поиск похожих карт"))
    assert result["items"]
    assert result["meta"]["fallback_reason"] == "semantic_unavailable"


def test_rest_validation_and_current_material(monkeypatch):
    monkeypatch.setattr(
        "app.material_search.material_repository.documents",
        AsyncMock(return_value=[row()]),
    )
    api = FastAPI()
    api.include_router(router)
    client = TestClient(api)
    assert (
        client.get("/v1/search", params={"q": "test", "kind": "private"}).status_code
        == 422
    )
    result = client.get("/v1/materials/card:constructed:CARD")
    assert result.status_code == 200
    assert result.json()["text"].startswith("Предсмертный")
    assert result.headers["cache-control"] == "no-store"


def test_mcp_protocol_tools_and_repeated_lifespan(monkeypatch):
    monkeypatch.setenv("HS_SEARCH_ENABLED", "false")
    monkeypatch.setattr(
        "app.material_search.material_repository.lexical",
        AsyncMock(return_value=[row()]),
    )
    monkeypatch.setattr(
        "app.material_search.material_repository.documents",
        AsyncMock(return_value=[row()]),
    )
    # Exercise the actual SDK transport through FastAPI, including a restart.
    api = FastAPI(lifespan=material_api_lifespan)
    api.mount("/v1/mcp", mcp_app)
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    for _ in range(2):
        with TestClient(api) as client:
            initialized = client.post(
                "/v1/mcp/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200
            assert initialized.json()["result"]["capabilities"]["tools"] is not None
            listed = client.post(
                "/v1/mcp/",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
            assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
                "search",
                "fetch",
            }
            result = client.post(
                "/v1/mcp/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {"query": "карта"}},
                },
            )
            assert not result.json()["result"].get("isError", False)
            assert result.json()["result"]["structuredContent"]["results"][0][
                "image_url"
            ]
            rejected = client.post(
                "/v1/mcp/",
                headers={**headers, "Origin": "https://untrusted.example"},
                json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            )
            assert rejected.status_code == 403


def test_repeated_and_concurrent_searches_share_database_work(monkeypatch):
    monkeypatch.setenv("HS_SEARCH_ENABLED", "false")
    lexical = AsyncMock(return_value=[row()])
    documents = AsyncMock(return_value=[row()])
    monkeypatch.setattr("app.material_search.material_repository.lexical", lexical)
    monkeypatch.setattr("app.material_search.material_repository.documents", documents)

    async def scenario():
        results = await asyncio.gather(
            *(search_materials("кэшируемый поиск") for _ in range(8))
        )
        results[0]["items"][0]["title"] = "client mutation"
        cached = await search_materials("кэшируемый поиск")
        assert cached["meta"]["cache"] == "hit"
        assert cached["items"][0]["title"] == "Тестовая карта"
        lexical.assert_awaited_once()
        documents.assert_awaited_once()

    asyncio.run(scenario())


def test_result_cache_expires_and_new_index_invalidates_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_SEARCH_ENABLED", "false")
    path = tmp_path / "snapshot"
    monkeypatch.setattr("app.material_search.index_path", lambda: path)
    clock = [100.0]
    monkeypatch.setattr(
        "app.material_search.time",
        SimpleNamespace(monotonic=lambda: clock[0], time=time.time),
    )
    documents = AsyncMock(return_value=[row(name="Старое название")])
    monkeypatch.setattr(
        "app.material_search.material_repository.lexical",
        AsyncMock(return_value=[row()]),
    )
    monkeypatch.setattr("app.material_search.material_repository.documents", documents)

    async def scenario():
        await search_materials("проверка свежести")
        documents.return_value = [row(name="Новое название")]
        clock[0] += 6
        refreshed = await search_materials("проверка свежести")
        assert refreshed["items"][0]["title"] == "Новое название"
        documents.return_value = [row(name="После перестройки индекса")]
        path.write_bytes(b"new snapshot")
        updated = await search_materials("проверка свежести")
        assert updated["items"][0]["title"] == "После перестройки индекса"
        assert updated["meta"]["cache"] == "miss"

    asyncio.run(scenario())
