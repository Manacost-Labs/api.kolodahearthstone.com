from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis
from redis.exceptions import RedisError

from . import config


class CacheBackend(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> bool: ...

    async def delete(self, key: str) -> bool: ...

    async def close(self) -> None: ...


class RedisCacheBackend:
    """Best-effort Redis cache that never makes the API depend on Redis health."""

    def __init__(
        self,
        url: str | None = None,
        *,
        prefix: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._url = url if url is not None else config.redis_url()
        self._prefix = prefix or config.redis_key_prefix()
        self._timeout = timeout_seconds or config.redis_operation_timeout_seconds()
        self._client: redis.Redis | None = None
        self._lock = asyncio.Lock()
        self._retry_after = 0.0

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def _get_client(self) -> redis.Redis | None:
        if not self._url or time.monotonic() < self._retry_after:
            return None
        async with self._lock:
            if self._client is None:
                self._client = redis.from_url(
                    self._url,
                    decode_responses=False,
                    socket_connect_timeout=self._timeout,
                    socket_timeout=self._timeout,
                    max_connections=32,
                    health_check_interval=30,
                )
        return self._client

    async def _failed(self) -> None:
        self._retry_after = time.monotonic() + 5.0
        async with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except RedisError:
                pass

    async def get(self, key: str) -> bytes | None:
        client = await self._get_client()
        if client is None:
            return None
        try:
            value = await asyncio.wait_for(client.get(self._key(key)), self._timeout)
        except (RedisError, TimeoutError):
            await self._failed()
            return None
        return bytes(value) if value is not None else None

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> bool:
        if ttl_seconds < 1:
            return False
        client = await self._get_client()
        if client is None:
            return False
        try:
            return bool(
                await asyncio.wait_for(
                    client.set(self._key(key), value, ex=ttl_seconds),
                    self._timeout,
                )
            )
        except (RedisError, TimeoutError):
            await self._failed()
            return False

    async def delete(self, key: str) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            return bool(
                await asyncio.wait_for(client.delete(self._key(key)), self._timeout)
            )
        except (RedisError, TimeoutError):
            await self._failed()
            return False

    async def close(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()


@dataclass(frozen=True, slots=True)
class _LocalEntry:
    value: bytes
    expires_at: float


class TieredCache:
    """Small process-local hot cache backed by Redis for cross-worker reuse."""

    def __init__(
        self,
        backend: CacheBackend | None = None,
        *,
        max_local_entries: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend or RedisCacheBackend()
        self._max_local_entries = (
            config.graphql_cache_local_entries()
            if max_local_entries is None
            else max(0, max_local_entries)
        )
        self._clock = clock
        self._local: OrderedDict[str, _LocalEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[bytes | None, str]:
        now = self._clock()
        async with self._lock:
            entry = self._local.get(key)
            if entry is not None and entry.expires_at > now:
                self._local.move_to_end(key)
                return entry.value, "local"
            self._local.pop(key, None)
        value = await self._backend.get(key)
        if value is None:
            return None, "miss"
        await self._put_local(key, value, ttl_seconds=5)
        return value, "redis"

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            return
        await self._put_local(key, value, ttl_seconds=ttl_seconds)
        await self._backend.set(key, value, ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._local.pop(key, None)
        await self._backend.delete(key)

    async def _put_local(self, key: str, value: bytes, *, ttl_seconds: int) -> None:
        if self._max_local_entries < 1:
            return
        async with self._lock:
            self._local[key] = _LocalEntry(
                value=value,
                expires_at=self._clock() + ttl_seconds,
            )
            self._local.move_to_end(key)
            while len(self._local) > self._max_local_entries:
                self._local.popitem(last=False)

    async def close(self) -> None:
        async with self._lock:
            self._local.clear()
        await self._backend.close()


_cache = TieredCache()


def get_tiered_cache() -> TieredCache:
    return _cache


async def close_tiered_cache() -> None:
    await _cache.close()
