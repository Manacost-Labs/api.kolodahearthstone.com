"""OpenRouter embeddings with bounded query cost and no credential logging."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from typing import Any

import httpx
import numpy as np

from .config import openrouter_api_key

MODEL = "perplexity/pplx-embed-v1-0.6b"
DIMENSIONS = 1024


class EmbeddingsUnavailable(RuntimeError):
    pass


def normalize_vectors(values: Any, count: int) -> np.ndarray:
    try:
        vectors = np.asarray(values, dtype=np.float32)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EmbeddingsUnavailable("Invalid embedding response") from exc
    if vectors.shape != (count, DIMENSIONS) or not np.isfinite(vectors).all():
        raise EmbeddingsUnavailable("Invalid embedding dimensions or values")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms == 0).any():
        raise EmbeddingsUnavailable("Invalid embedding norms")
    return vectors / norms


class EmbeddingClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._cache: OrderedDict[str, tuple[float, np.ndarray]] = OrderedDict()
        self._pending: dict[str, asyncio.Task[np.ndarray]] = {}
        self._requests: deque[float] = deque()
        self._retry_at = 0.0

    async def encode(self, texts: list[str]) -> np.ndarray:
        key = openrouter_api_key()
        if not key:
            raise EmbeddingsUnavailable("OpenRouter is not configured")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        try:
            response = await self._client.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": MODEL,
                    "input": texts,
                    "dimensions": DIMENSIONS,
                    "encoding_format": "float",
                    "provider": {"sort": "latency"},
                },
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("Missing embeddings")
            if sorted(row["index"] for row in rows) != list(range(len(texts))):
                raise ValueError("Invalid embedding indexes")
            rows = sorted(rows, key=lambda row: row["index"])
            return normalize_vectors([row["embedding"] for row in rows], len(texts))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise EmbeddingsUnavailable("OpenRouter embeddings unavailable") from exc

    async def _query(self, query: str) -> np.ndarray:
        try:
            vectors = await asyncio.wait_for(self.encode([query]), timeout=1.5)
            self._cache[query] = (time.monotonic(), vectors[0])
            self._cache.move_to_end(query)
            while len(self._cache) > 1024:
                self._cache.popitem(last=False)
            return vectors[0]
        except (EmbeddingsUnavailable, TimeoutError):
            self._retry_at = time.monotonic() + 30
            raise EmbeddingsUnavailable("Query embedding unavailable") from None
        finally:
            self._pending.pop(query, None)

    async def query(self, query: str) -> np.ndarray:
        now = time.monotonic()
        cached = self._cache.get(query)
        if cached and now - cached[0] < 3600:
            self._cache.move_to_end(query)
            return cached[1]
        if query in self._pending:
            return await asyncio.shield(self._pending[query])
        while self._requests and now - self._requests[0] >= 60:
            self._requests.popleft()
        # A per-worker ceiling also bounds anonymous search expenditure.
        if now < self._retry_at or len(self._pending) >= 4 or len(self._requests) >= 60:
            raise EmbeddingsUnavailable("Query embedding temporarily unavailable")
        self._requests.append(now)
        task = asyncio.create_task(self._query(query))
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        self._pending[query] = task
        return await asyncio.shield(task)

    async def close(self) -> None:
        for task in tuple(self._pending.values()):
            task.cancel()
        await asyncio.gather(*tuple(self._pending.values()), return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None


embedding_client = EmbeddingClient()
