"""Domain event contracts + SEAMS (extension points) for future members.

Nothing here is a live integration: these interfaces exist so Members 3, 5, 6
and 7+ can plug in denormalized projections, replication selection, Redis
caching, feeds/search/graph/media, and observability WITHOUT touching the
canonical schema or the shard router internals.

Architectural rule preserved by these seams::

    API/Service -> (Cache, Member 6 — future) -> Repository
                                              -> Shard Router (Member 4)
                                              -> (Replica selection, M5 — future)
                                              -> PostgreSQL
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, Protocol, TypeVar

from common.exceptions import NotConfiguredError

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Domain events (canonical -> derived systems). PostgreSQL stays the source
# of truth; events only trigger *projection* updates.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainEvent:
    """Base event. Events are named after past-tense state changes."""

    event_id: str
    event_type: str
    aggregate: str
    aggregate_id: int
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)
    # Populated in SHARDED mode so downstream consumers know the origin.
    shard_id: str | None = None


EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventBus(Protocol):
    """Future contract for the messaging member (RabbitMQ placeholder).

    Member 3 (denormalization), search, feeds, etc. will subscribe handlers;
    the service layer will publish after a successful canonical commit.
    """

    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: str, handler: EventHandler) -> None: ...


class NotConfiguredEventBus:
    """Default no-op bus used until the messaging member is implemented."""

    async def publish(self, event: DomainEvent) -> None:
        # Intentionally a no-op: no derived system is wired in this milestone.
        return None

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        raise NotConfiguredError("Event bus is not implemented in this milestone")


# ---------------------------------------------------------------------------
# Member 6 — cache seam. The shard router MUST NOT know whether a cache exists.
# Usage later: cache -> repository -> shard router -> PostgreSQL.
# ---------------------------------------------------------------------------


class CacheProvider(abc.ABC):
    """Redis cache-aside contract.

    IMPLEMENTED by Member 6 in ``services/cache/cache.py`` (async
    redis.asyncio client, JSON values instead of raw bytes, TTLs,
    invalidation and fail-open). The concrete provider is structurally
    compatible (async ``get`` / ``set`` / ``invalidate``) and composed in
    the API layer; repositories and the shard router stay unaware of it.
    """

    @abc.abstractmethod
    async def get(self, key: str) -> bytes | None:
        raise NotConfiguredError("Caching is owned by Member 6")

    @abc.abstractmethod
    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        raise NotConfiguredError("Caching is owned by Member 6")

    @abc.abstractmethod
    async def invalidate(self, key: str) -> None:
        raise NotConfiguredError("Caching is owned by Member 6")


# ---------------------------------------------------------------------------
# Member 7+ — read model / search / graph / feed / media projection seams
# ---------------------------------------------------------------------------


class ReadModelProjector(abc.ABC):
    """Member 3 contract: denormalized read models are projections."""

    @abc.abstractmethod
    async def project(self, event: DomainEvent) -> None:
        raise NotConfiguredError("Denormalization is owned by Member 3")


class SearchIndex(abc.ABC):
    """Future OpenSearch-backed search contract (NOT IMPLEMENTED)."""

    @abc.abstractmethod
    async def index_document(self, index: str, document: dict[str, Any]) -> None:
        raise NotConfiguredError("Search is a future module")

    @abc.abstractmethod
    async def search(self, index: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotConfiguredError("Search is a future module")


class GraphAccessLayer(abc.ABC):
    """Future TAO-inspired graph access contract (NOT IMPLEMENTED)."""

    @abc.abstractmethod
    async def edge_count(self, node_id: int, edge_type: str) -> int:
        raise NotConfiguredError("TAO-inspired graph layer is a future module")


class FeedService(abc.ABC):
    """Future feed generation contract (NOT IMPLEMENTED)."""

    @abc.abstractmethod
    async def get_feed(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        raise NotConfiguredError("Feed generation is a future module")


class MediaBlobStore(abc.ABC):
    """Future Haystack-inspired blob storage contract (NOT IMPLEMENTED).

    Only media *metadata* exists in PostgreSQL today.
    """

    @abc.abstractmethod
    async def put(self, storage_key: str, data: bytes) -> str:
        raise NotConfiguredError("Haystack-inspired blob store is a future module")

    @abc.abstractmethod
    async def get(self, storage_key: str) -> bytes:
        raise NotConfiguredError("Haystack-inspired blob store is a future module")


class MetricsSink(abc.ABC, Generic[T]):
    """Future observability member contract (Prometheus/Grafana).

    Member 4's MetricsCollector already exposes a snapshot via this shape, so
    the observability member only needs to implement a scraping sink.
    """

    @abc.abstractmethod
    def export(self, snapshot: dict[str, Any]) -> T:
        """Transform a sharding metrics snapshot into the sink's format."""
        raise NotConfiguredError("Metrics export is owned by the observability member")
