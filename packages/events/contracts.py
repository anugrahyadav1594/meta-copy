"""Domain event contracts + module SEAMS for the integrated MetaScale system.

This module is the *contract registry*: every cross-module extension point is
declared here once, with a pointer to the module that implements it. Nothing
here talks to a database, a broker or a cache — implementations live in
``packages/db`` (repositories) and ``services/*`` (per-member modules).

Canonical read/write path preserved by these seams::

    Client
      -> FastAPI
      -> Service layer
      -> Cache adapter (Member 6, cache-aside)
      -> Repository (canonical | sharded)
      -> Shard Router (Member 4)
      -> Replication provider (Member 5)
      -> PostgreSQL (source of truth)

Derived (rebuildable) systems hang off the event bus, never off the write
path: denormalized read model (M3), feed (M7), media metadata (M8),
TAO-inspired graph (M9), search index (M10), observability (M11).
"""

from __future__ import annotations

import abc
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from events.bus import DomainEvent, EventHandler, EventType

# One event model for the whole codebase (defined in events.bus).
__all__ = ["DomainEvent", "EventHandler", "EventType", "EventBus"]

T = TypeVar("T")


@runtime_checkable
class EventBus(Protocol):
    """Messaging contract (shared infrastructure).

    IMPLEMENTED:
      * ``events.bus.InMemoryEventBus`` — default, always available.
      * ``events.bus.RabbitMQEventBus`` — OPTIONAL, ``EVENT_BUS=rabbitmq``.
      * ``events.bus.NullEventBus``     — ``EVENT_BUS=off``.

    Services publish after a successful canonical commit; projections
    subscribe. Swapping transports never changes business logic.
    """

    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: str, handler: EventHandler) -> None: ...


# ---------------------------------------------------------------------------
# Member 6 — cache seam. The shard router MUST NOT know whether a cache exists.
# ---------------------------------------------------------------------------


class CacheProvider(abc.ABC):
    """Redis cache-aside contract.

    IMPLEMENTED by ``services/cache/cache.py`` (async ``redis.asyncio``
    client, JSON values, TTLs, invalidation, hit/miss metrics, hot-key
    detection and fail-open). Composed in the API/service layer; repositories
    and the shard router stay unaware of it.
    """

    @abc.abstractmethod
    async def get(self, key: str) -> Any: ...

    @abc.abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool: ...

    @abc.abstractmethod
    async def invalidate(self, key: str) -> bool: ...


# ---------------------------------------------------------------------------
# Member 3 / 7 / 9 / 10 / 8 / 11 — derived-system seams
# ---------------------------------------------------------------------------


class ReadModelProjector(abc.ABC):
    """Member 3 contract: denormalized read models are projections.

    IMPLEMENTED by ``services/denormalization`` — maintains
    ``denormalized_post_feed`` from domain events and can be rebuilt from
    canonical PostgreSQL at any time.
    """

    @abc.abstractmethod
    async def project(self, event: DomainEvent) -> None: ...

    @abc.abstractmethod
    async def rebuild(self) -> int:
        """Rebuild the projection from canonical tables; returns row count."""


class SearchIndex(abc.ABC):
    """Search contract — IMPLEMENTED by ``services/search``.

    ``InMemorySearchProvider`` (inverted index, default) and
    ``OpenSearchProvider`` (OPTIONAL, ``search`` compose profile).
    """

    @abc.abstractmethod
    async def index_document(self, index: str, document: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    async def search(self, index: str, query: str, limit: int = 20) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def autocomplete(self, index: str, prefix: str, limit: int = 10) -> list[str]: ...


class GraphAccessLayer(abc.ABC):
    """TAO-inspired graph contract — IMPLEMENTED by ``services/graph``.

    Reads the canonical ``follows`` table through repositories; the in-memory
    adjacency cache is a rebuildable projection, never a source of truth.
    """

    @abc.abstractmethod
    async def edge_count(self, node_id: int, edge_type: str) -> int: ...


class FeedService(abc.ABC):
    """Feed contract — IMPLEMENTED by ``services/feed``.

    Pull-based (follow graph -> candidate posts -> rank) by default, with an
    OPTIONAL fan-out-on-write projection (``user_feed``) that is rebuilt from
    canonical data.
    """

    @abc.abstractmethod
    async def get_feed(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]: ...


class MediaBlobStore(abc.ABC):
    """Haystack-inspired blob storage contract — IMPLEMENTED by
    ``services/media``.

    ``LocalBlobStore`` (default) and ``MinioBlobStore`` (OPTIONAL,
    ``MEDIA_BACKEND=minio``). PostgreSQL stores metadata only; blobs are
    content-addressed by SHA-256 and de-duplicated.
    """

    @abc.abstractmethod
    async def put(self, storage_key: str, data: bytes, content_type: str) -> str: ...

    @abc.abstractmethod
    async def get(self, storage_key: str) -> bytes: ...

    @abc.abstractmethod
    async def delete(self, storage_key: str) -> bool: ...

    @abc.abstractmethod
    async def exists(self, storage_key: str) -> bool: ...


class MetricsSink(abc.ABC, Generic[T]):
    """Observability contract — IMPLEMENTED by ``services/observability``
    (Prometheus text exposition at ``/metrics``, JSON at
    ``/api/v1/metrics``)."""

    @abc.abstractmethod
    def export(self, snapshot: dict[str, Any]) -> T:
        """Transform a metrics snapshot into the sink's format."""


class ReplicationProvider(abc.ABC):
    """Member 5 contract: replica selection below the shard router.

    IMPLEMENTED by ``services/replication`` — primary for writes, replica for
    read-only traffic where a healthy replica is configured. Real streaming
    replication requires the ``replication`` compose profile; with a single
    endpoint configured the provider degrades to primary-only and says so.
    """

    @abc.abstractmethod
    async def engine_for(self, shard_id: str, *, read_only: bool) -> Any: ...

    @abc.abstractmethod
    async def status(self) -> dict[str, Any]: ...
