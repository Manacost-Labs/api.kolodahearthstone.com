from __future__ import annotations

import asyncio

from app.redis_cache import TieredCache


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.get_calls = 0
        self.closed = False

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self.values.get(key)

    async def set(self, key: str, value: bytes, *, ttl_seconds: int) -> bool:
        self.values[key] = value
        return ttl_seconds > 0

    async def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def close(self) -> None:
        self.closed = True


def test_tiered_cache_uses_local_then_shared_backend() -> None:
    async def scenario() -> None:
        now = [100.0]
        backend = FakeBackend()
        cache = TieredCache(backend, max_local_entries=2, clock=lambda: now[0])

        await cache.set("query", b"payload", ttl_seconds=30)
        value, layer = await cache.get("query")
        assert (value, layer) == (b"payload", "local")
        assert backend.get_calls == 0

        now[0] = 131.0
        value, layer = await cache.get("query")
        assert (value, layer) == (b"payload", "redis")
        assert backend.get_calls == 1

    asyncio.run(scenario())


def test_tiered_cache_is_bounded_and_delete_reaches_backend() -> None:
    async def scenario() -> None:
        backend = FakeBackend()
        cache = TieredCache(backend, max_local_entries=1)

        await cache.set("first", b"1", ttl_seconds=30)
        await cache.set("second", b"2", ttl_seconds=30)
        await cache.delete("first")

        value, layer = await cache.get("first")
        assert (value, layer) == (None, "miss")
        await cache.close()
        assert backend.closed is True

    asyncio.run(scenario())
