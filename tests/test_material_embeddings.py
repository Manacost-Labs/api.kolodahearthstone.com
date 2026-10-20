from __future__ import annotations

import asyncio

import httpx
import numpy as np
import pytest

from app.material_embeddings import DIMENSIONS, EmbeddingClient, EmbeddingsUnavailable
from app.material_search_index import MaterialIndex


def test_concurrent_queries_share_one_request_and_cache(monkeypatch) -> None:
    monkeypatch.setenv("HS_OPENROUTER_API_KEY", "test-only")
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0] * DIMENSIONS}]}
        )

    async def scenario():
        client = EmbeddingClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        try:
            vectors = await asyncio.gather(
                *(client.query("предсмертный хрип") for _ in range(8))
            )
            assert all(np.allclose(v, vectors[0]) for v in vectors)
            assert np.isclose(np.linalg.norm(vectors[0]), 1)
            await client.query("предсмертный хрип")
            assert len(calls) == 1
            import json

            body = json.loads(calls[0].content)
            assert body["provider"] == {"sort": "latency"}
            assert body["model"] == "perplexity/pplx-embed-v1-0.6b"
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "embedding", [[1], [0] * DIMENSIONS, [float("nan")] * DIMENSIONS]
)
def test_invalid_vectors_do_not_enter_cache(monkeypatch, embedding) -> None:
    monkeypatch.setenv("HS_OPENROUTER_API_KEY", "test-only")

    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"data": [{"index": 0, "embedding": embedding}]}
            )
        )
        # NaN is deliberately sent as raw JSON since httpx forbids encoding it.
        if np.isnan(embedding).any():
            import json

            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=json.dumps(
                        {"data": [{"index": 0, "embedding": embedding}]}
                    ),
                )
            )
        client = EmbeddingClient(httpx.AsyncClient(transport=transport))
        try:
            with pytest.raises(EmbeddingsUnavailable):
                await client.query("test")
            assert not client._cache
        finally:
            await client.close()

    asyncio.run(scenario())


def test_index_filters_kinds_and_failed_publish_preserves_snapshot(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_SEARCH_INDEX_PATH", str(tmp_path / "materials.npz"))
    index = MaterialIndex()
    documents = [
        {"kind": kind, "entity_id": str(i), "content": "test"}
        for i, kind in enumerate(["hero", "card", "card"])
    ]
    vectors = np.zeros((3, DIMENSIONS), dtype=np.float32)
    vectors[0, 0], vectors[1, 1], vectors[2, 0] = 1, 1, 1
    index.publish(documents, vectors)
    keys, _ = index.candidates(vectors[0], ["card"], 2)
    assert keys == ["card:2", "card:1"]
    with pytest.raises(EmbeddingsUnavailable):
        index.publish(documents, vectors[:, :2])
    assert index.candidates(vectors[0], ["hero"], 1)[0] == ["hero:0"]
