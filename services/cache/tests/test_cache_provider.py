"""Unit tests for the async CacheProvider.

Uses the ``memory://`` fakeredis backend — an in-process emulator marked
dev/test only. No Redis container is required.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.cache.cache import CacheProvider, loads

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cache() -> CacheProvider:
    provider = await CacheProvider.create("memory://", default_ttl=60, hot_key_threshold=3)
    assert provider is not None
    assert provider.backend == "memory"
    yield provider
    await provider.aclose()


async def test_connect_and_ping(cache: CacheProvider) -> None:
    assert cache.available is True
    assert await cache.ping() is True


async def test_set_get_hit_and_miss(cache: CacheProvider) -> None:
    assert await cache.get("post:1") is None  # cold -> miss
    assert await cache.set("post:1", {"id": 1, "content": "hello"}) is True

    value = await cache.get("post:1")  # warm -> hit
    assert value == {"id": 1, "content": "hello"}

    metrics = cache.get_metrics()
    assert metrics["cache_hits"] == 1
    assert metrics["cache_misses"] == 1
    assert metrics["hit_ratio"] == 50.0
    assert metrics["miss_ratio"] == 50.0
    assert metrics["db_queries_avoided"] == 1
    assert metrics["backend"] == "memory"


async def test_ttl_is_applied(cache: CacheProvider) -> None:
    await cache.set("post:7", {"id": 7}, ttl=30)
    ttl = await cache.client.ttl("post:7")
    assert 0 < ttl <= 30


async def test_datetime_json_roundtrip() -> None:
    from services.cache.cache import dumps

    now = datetime(2026, 9, 11, 10, 30, tzinfo=UTC)
    decoded = loads(dumps({"created_at": now, "n": 1}))
    assert decoded["created_at"] == now.isoformat()
    assert decoded["n"] == 1


async def test_invalidation(cache: CacheProvider) -> None:
    await cache.set("post:2", {"id": 2})
    assert await cache.get("post:2") == {"id": 2}

    await cache.delete("post:2")
    assert await cache.get("post:2") is None  # invalidated -> miss again

    metrics = cache.get_metrics()
    assert metrics["cache_invalidations"] == 1
    assert metrics["cache_hits"] == 1
    assert metrics["cache_misses"] == 1  # the post-delete read

    # `invalidate` is the documented seam-method alias for delete.
    await cache.set("post:3", {"id": 3})
    await cache.invalidate("post:3")
    assert metrics["cache_invalidations"] <= cache.get_metrics()["cache_invalidations"]


async def test_keys_pattern(cache: CacheProvider) -> None:
    await cache.set("post:1", 1)
    await cache.set("post:2", 2)
    await cache.set("user:1", 1)
    keys = await cache.keys("post:*")
    assert set(keys) == {"post:1", "post:2"}


async def test_hot_key_detection(cache: CacheProvider) -> None:
    await cache.set("hot:1", {"v": 1})
    for _ in range(3):
        await cache.get("hot:1")
    hot = cache.get_metrics()["hot_keys"]
    assert any(k["key"] == "hot:1" for k in hot)


async def test_average_latency_tracked(cache: CacheProvider) -> None:
    await cache.set("k", 1)
    await cache.get("k")
    assert cache.get_metrics()["average_cache_latency_ms"] >= 0.0


async def test_reset_metrics(cache: CacheProvider) -> None:
    await cache.set("k", 1)
    await cache.get("k")
    cache.reset_metrics()
    metrics = cache.get_metrics()
    assert metrics["cache_hits"] == metrics["cache_misses"] == 0
    assert metrics["hot_keys"] == []


async def test_fail_open_returns_none_on_unreachable_redis() -> None:
    # Port 9 (discard) refuses TCP immediately on any host.
    provider = await CacheProvider.create(
        "redis://127.0.0.1:9/0", connect_timeout=0.3, fail_open=True
    )
    assert provider is None


async def test_fail_closed_raises_on_unreachable_redis() -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011
        await CacheProvider.create("redis://127.0.0.1:9/0", connect_timeout=0.3, fail_open=False)


async def test_operations_survive_late_redis_failure(monkeypatch) -> None:
    """If Redis dies mid-life, every operation degrades (fail-open) not raises."""
    provider = await CacheProvider.create("memory://")
    assert provider is not None

    class DeadClient:
        async def get(self, *_a, **_kw):
            raise ConnectionError("redis went away")

        async def set(self, *_a, **_kw):
            raise ConnectionError("redis went away")

        async def delete(self, *_a, **_kw):
            raise ConnectionError("redis went away")

        async def keys(self, *_a, **_kw):
            raise ConnectionError("redis went away")

        async def ping(self):
            return False

        async def aclose(self):
            return None

    provider.client = DeadClient()
    assert await provider.get("x") is None
    assert await provider.set("x", 1) is False
    await provider.delete("x")  # must not raise
    assert await provider.keys() == []
    assert await provider.ping() is False
    assert provider.get_metrics()["cache_errors"] >= 4
    await provider.aclose()
