"""Event bus implementations (Phase 15).

The canonical write path publishes a :class:`DomainEvent` *after* its
PostgreSQL transaction commits. Derived systems (denormalized read model,
feed fan-out, search index, graph projection, notifications) subscribe to
those events. PostgreSQL stays the source of truth: every subscriber builds
or refreshes data that can be rebuilt from canonical tables.

Two transports ship with the project:

* :class:`InMemoryEventBus` — IMPLEMENTED, always available. Handlers run in
  the same process; used by default and in tests. Fan-out is sequential and
  asynchronous, and a failing handler is isolated (logged + counted) so one
  broken projection can never fail a canonical write.
* :class:`RabbitMQEventBus` — IMPLEMENTED but OPTIONAL. Enabled with
  ``EVENT_BUS=rabbitmq``; requires ``RABBITMQ_URL`` and the ``aio-pika``
  package (an extra dependency, therefore imported lazily). Business logic
  never changes: only the bus implementation behind ``EventBus``.

Nothing here simulates messaging: the in-memory bus really dispatches to
subscribers, and the RabbitMQ bus really publishes to an exchange when it is
configured and reachable (it degrades to in-memory otherwise, and logs it).
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from common.logging import get_logger

logger = get_logger("events")


class EventType(str, enum.Enum):
    """Canonical domain events (past tense — they describe committed facts)."""

    POST_CREATED = "POST_CREATED"
    POST_UPDATED = "POST_UPDATED"
    POST_DELETED = "POST_DELETED"
    COMMENT_CREATED = "COMMENT_CREATED"
    LIKE_CREATED = "LIKE_CREATED"
    LIKE_DELETED = "LIKE_DELETED"
    FOLLOW_CREATED = "FOLLOW_CREATED"
    FOLLOW_DELETED = "FOLLOW_DELETED"
    MEDIA_CREATED = "MEDIA_CREATED"
    USER_CREATED = "USER_CREATED"


@dataclass(frozen=True)
class DomainEvent:
    """Immutable fact about a committed canonical state change."""

    event_type: str
    aggregate: str
    aggregate_id: int
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Populated in SHARDED mode so consumers know the physical origin.
    shard_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        data["event_type"] = self.event_type
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainEvent:
        occurred = data.get("occurred_at")
        parsed = (
            datetime.fromisoformat(occurred) if isinstance(occurred, str) else datetime.now(UTC)
        )
        return cls(
            event_type=data["event_type"],
            aggregate=data["aggregate"],
            aggregate_id=int(data["aggregate_id"]),
            payload=dict(data.get("payload") or {}),
            event_id=data.get("event_id") or uuid.uuid4().hex,
            occurred_at=parsed,
            shard_id=data.get("shard_id"),
        )


EventHandler = Any  # async callable(DomainEvent) -> None


class InMemoryEventBus:
    """In-process pub/sub. IMPLEMENTED (default transport)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._wildcard: list[EventHandler] = []
        self.published: list[DomainEvent] = []
        self.delivered = 0
        self.failures: list[tuple[str, str]] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if event_type == "*":
            self._wildcard.append(handler)
        else:
            self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        self.published.append(event)
        handlers = [*self._handlers.get(event.event_type, []), *self._wildcard]
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
                self.delivered += 1
            except Exception as exc:  # isolate failing projections
                self.failures.append((event.event_type, type(exc).__name__))
                logger.error(
                    "event_handler_failed",
                    event_type=event.event_type,
                    handler=getattr(handler, "__name__", repr(handler)),
                    error=type(exc).__name__,
                )

    def stats(self) -> dict[str, Any]:
        return {
            "transport": "in_memory",
            "published": len(self.published),
            "delivered": self.delivered,
            "subscribers": sum(len(v) for v in self._handlers.values()) + len(self._wildcard),
            "failures": len(self.failures),
        }


class NullEventBus:
    """No-op bus used when the event system is disabled entirely."""

    async def publish(self, event: DomainEvent) -> None:
        return None

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        logger.warning("event_subscribe_ignored", event_type=event_type)

    def stats(self) -> dict[str, Any]:
        return {"transport": "disabled", "published": 0, "delivered": 0, "failures": 0}


class RabbitMQEventBus:
    """OPTIONAL AMQP transport (RabbitMQ profile).

    Publishes JSON-encoded events to a topic exchange and mirrors them into
    an in-process bus so local subscribers still run. If ``aio-pika`` is not
    installed or the broker is unreachable, publication falls back to the
    in-memory bus and says so in the log — the write path is never blocked by
    messaging.
    """

    def __init__(self, url: str, exchange: str = "metascale.events") -> None:
        self.url = url
        self.exchange_name = exchange
        self._fallback = InMemoryEventBus()
        self._connection: Any = None
        self._channel: Any = None
        self._exchange: Any = None
        self.connected = False
        self.last_error: str | None = None

    async def connect(self) -> bool:
        try:
            import aio_pika  # optional dependency, imported lazily
        except ImportError:
            self.last_error = "aio-pika not installed"
            logger.warning("rabbitmq_unavailable", reason=self.last_error)
            return False
        try:
            self._connection = await aio_pika.connect_robust(self.url)
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                self.exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
            )
            self.connected = True
            logger.info("rabbitmq_connected", url=_redact(self.url))
            return True
        except Exception as exc:
            self.last_error = type(exc).__name__
            logger.warning("rabbitmq_connect_failed", error=self.last_error)
            return False

    async def publish(self, event: DomainEvent) -> None:
        if self.connected and self._exchange is not None:
            try:
                import aio_pika

                await self._exchange.publish(
                    aio_pika.Message(
                        body=_json_dumps(event.to_dict()).encode(),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=event.event_type.lower().replace("_", "."),
                )
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.error("rabbitmq_publish_failed", error=self.last_error)
        # Always keep the local subscribers fed (single-node correctness).
        await self._fallback.publish(event)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._fallback.subscribe(event_type, handler)

    async def aclose(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:  # pragma: no cover - shutdown best effort
                pass
            self._connection = None
            self.connected = False

    def stats(self) -> dict[str, Any]:
        stats = self._fallback.stats()
        stats.update(
            {
                "transport": "rabbitmq" if self.connected else "rabbitmq_unavailable_in_memory",
                "connected": self.connected,
                "last_error": self.last_error,
            }
        )
        return stats


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str)


def _redact(url: str) -> str:
    """Never log broker credentials."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host = rest.split("@", 1)
    return f"{scheme}://***@{host}"


def build_event_bus(transport: str, rabbitmq_url: str | None = None) -> Any:
    """Factory used by the composition root.

    ``transport``: ``"memory"`` (default) | ``"rabbitmq"`` | ``"off"``.
    """
    transport = (transport or "memory").lower()
    if transport == "off":
        return NullEventBus()
    if transport == "rabbitmq":
        return RabbitMQEventBus(rabbitmq_url or "amqp://guest:guest@rabbitmq:5672//")
    return InMemoryEventBus()
