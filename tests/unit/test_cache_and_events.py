"""Unit tests for the cache layer (Member 6) and the event bus (Phase 15).

Both are pure in-memory components, so these run without PostgreSQL. The
cache backend is ``memory://`` (fakeredis), which is dev/test ONLY — the
deployment backend is real Redis.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time

import pytest
from events.bus import DomainEvent, EventType, InMemoryEventBus, NullEventBus
from events.contracts import EventBus

from services.cache.cache import CacheProvider


@pytest.fixture
async def cache() -> CacheProvider:
    provider = await CacheProvider.create("memory://", default_ttl=60, hot_key_threshold=3)
    assert provider is not None
    yield provider
    await provider.aclose()


@pytest.mark.asyncio
async def test_cache_aside_hit_miss_and_metrics(cache: CacheProvider) -> None:
    # first read: MISS, loader runs, value is stored under post:{id}
    assert await cache.get("post:7") is None
    await cache.set("post:7", {"post_id": 7, "content": "hello"})

    assert await cache.get("post:7") == {"post_id": 7, "content": "hello"}
    await cache.get("post:7")

    metrics = cache.get_metrics()
    assert metrics["cache_misses"] == 1
    assert metrics["cache_hits"] == 2
    assert metrics["hit_ratio"] == 66.67
    assert metrics["db_queries_avoided"] == 2
    assert metrics["backend"] == "memory"
    assert metrics["average_cache_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_cache_key_is_namespaced_per_entity() -> None:
    """The documented cache key for the hot read path is ``post:{post_id}``."""
    provider = await CacheProvider.create("memory://", default_ttl=60)
    assert provider is not None
    await provider.set("post:1", {"a": 1})
    await provider.set("post:2", {"a": 2})
    assert sorted(await provider.keys("post:*")) == ["post:1", "post:2"]
    assert await provider.get("post:1") == {"a": 1}
    await provider.aclose()


@pytest.mark.asyncio
async def test_write_invalidation_and_metrics(cache: CacheProvider) -> None:
    await cache.set("post:9", {"v": 1})
    await cache.invalidate("post:9")
    assert await cache.get("post:9") is None
    assert cache.get_metrics()["cache_invalidations"] == 1


@pytest.mark.asyncio
async def test_hot_key_detection(cache: CacheProvider) -> None:
    await cache.set("post:hot", {"v": 1})
    for _ in range(3):
        await cache.get("post:hot")
    assert cache.get_metrics()["hot_keys"] == [
        {"key": "post:hot", "accesses": 3}
    ] or "post:hot" in str(cache.get_metrics()["hot_keys"])


@pytest.mark.asyncio
async def test_cache_graceful_degradation_when_backend_down() -> None:
    provider = await CacheProvider.create("memory://", default_ttl=60)
    assert provider is not None
    await provider.set("k", {"v": 1})

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("redis is down")

    original_get = provider.client.get
    provider.client.get = broken  # type: ignore[method-assign]
    try:
        # fail-open: a dead cache returns None, it never raises, and the
        # request is served by the database instead.
        assert await provider.get("k") is None
        assert provider.get_metrics()["cache_errors"] == 1
    finally:
        provider.client.get = original_get  # type: ignore[method-assign]
        await provider.aclose()


@pytest.mark.asyncio
async def test_cache_ttl_is_set_on_write() -> None:
    provider = await CacheProvider.create("memory://", default_ttl=42)
    assert provider is not None
    await provider.set("k", "v")
    assert await provider.client.ttl("k") == 42
    await provider.set("k2", "v", ttl=7)
    assert await provider.client.ttl("k2") == 7
    await provider.aclose()


def test_event_bus_delivers_to_every_subscriber() -> None:
    bus = InMemoryEventBus()
    seen: list[int] = []
    bus.subscribe(EventType.POST_CREATED.value, lambda e: seen.append(e.aggregate_id))
    bus.subscribe(EventType.POST_CREATED.value, lambda e: seen.append(e.aggregate_id * 10))

    event = DomainEvent(
        event_type=EventType.POST_CREATED,
        aggregate="post",
        aggregate_id=42,
        payload={"content": "hi"},
    )
    asyncio.run(bus.publish(event))

    assert sorted(seen) == [42, 420]
    stats = bus.stats()
    assert stats["transport"] == "in_memory"
    assert stats["published"] == 1
    assert stats["delivered"] == 2
    assert stats["subscribers"] == 2  # honest count: no phantom handlers
    assert stats["failures"] == 0


def test_event_bus_ignores_unrelated_event_types() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []
    bus.subscribe(EventType.LIKE_CREATED.value, lambda e: seen.append(e.event_type))
    asyncio.run(
        bus.publish(
            DomainEvent(event_type=EventType.POST_CREATED, aggregate="post", aggregate_id=1)
        )
    )
    assert seen == []
    assert bus.stats()["delivered"] == 0


def test_event_bus_isolates_failing_handlers() -> None:
    bus = InMemoryEventBus()

    async def boom(_event: DomainEvent) -> None:
        raise RuntimeError("handler exploded")

    received: list[int] = []
    bus.subscribe(EventType.LIKE_CREATED.value, boom)
    bus.subscribe(EventType.LIKE_CREATED.value, lambda e: received.append(e.aggregate_id))
    asyncio.run(
        bus.publish(
            DomainEvent(event_type=EventType.LIKE_CREATED, aggregate="post", aggregate_id=5)
        )
    )
    assert received == [5]  # one bad projection must not stop the others
    assert bus.stats()["failures"] == 1


def test_wildcard_subscriber_sees_every_event() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []
    bus.subscribe("*", lambda e: seen.append(str(getattr(e.event_type, "value", e.event_type))))
    for etype in (EventType.POST_CREATED, EventType.FOLLOW_CREATED):
        asyncio.run(bus.publish(DomainEvent(event_type=etype, aggregate="x", aggregate_id=1)))
    assert sorted(seen) == ["FOLLOW_CREATED", "POST_CREATED"]


def test_domain_event_is_immutable_and_timestamped() -> None:
    before = time.time()
    event = DomainEvent(
        event_type=EventType.POST_CREATED, aggregate="post", aggregate_id=1, payload={"a": 1}
    )
    assert event.event_id
    assert before <= event.occurred_at.timestamp() <= time.time() + 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.aggregate_id = 2  # type: ignore[misc]
    round_tripped = DomainEvent.from_dict(event.to_dict())
    assert round_tripped.aggregate_id == 1
    assert round_tripped.payload == {"a": 1}


def test_event_types_cover_the_required_lifecycle() -> None:
    required = {
        "USER_CREATED",
        "POST_CREATED",
        "POST_UPDATED",
        "POST_DELETED",
        "LIKE_CREATED",
        "LIKE_DELETED",
        "COMMENT_CREATED",
        "FOLLOW_CREATED",
        "FOLLOW_DELETED",
        "MEDIA_CREATED",
    }
    assert required <= {member.value for member in EventType}


def test_every_bus_satisfies_the_event_bus_contract() -> None:
    for bus in (InMemoryEventBus(), NullEventBus()):
        assert isinstance(bus, EventBus)  # runtime-checkable Protocol


def test_null_bus_drops_events_when_messaging_is_disabled() -> None:
    bus = NullEventBus()
    asyncio.run(
        bus.publish(
            DomainEvent(event_type=EventType.POST_CREATED, aggregate="post", aggregate_id=1)
        )
    )
    assert bus.stats()["transport"] == "disabled"
