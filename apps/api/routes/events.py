"""Event-system introspection (shared infrastructure, not a member module).

The domain-event bus is what keeps derived systems (denormalized read model,
feed fan-out, search index, media metadata, observability) in step with
canonical PostgreSQL. These endpoints expose the *real* bus state: how many
events were published, how many handlers ran, which transport is in use, and
the most recent events themselves.

Nothing here replays or fabricates events: if the transport is RabbitMQ the
recent-event list is empty because this process does not retain them, and the
response says so.
"""

from __future__ import annotations

from typing import Any

from events.bus import EventType
from fastapi import APIRouter, Depends, Query

from api.dependencies import Platform, get_platform

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def events_overview(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Contract + transport + counters of the running event bus."""
    bus = platform.event_bus
    stats = bus.stats() if hasattr(bus, "stats") else {}
    return {
        "contract": {
            "event_id": "uuid4 hex, unique per event",
            "event_type": "one of /api/v1/events/types",
            "entity_type": "aggregate the event is about (post, user, media, follow)",
            "entity_id": "primary key of that aggregate",
            "timestamp": "UTC ISO-8601, set when the event is created",
            "payload": "event-specific fields (never secrets)",
            "source": "service that published it",
        },
        "transport": getattr(bus, "transport", stats.get("transport", "unknown")),
        "publish_after_commit": True,
        "stats": stats,
        "subscribers": _subscriber_names(bus),
    }


def _subscriber_names(bus: Any) -> list[str]:
    """Handler names registered on the bus (real introspection)."""
    names: list[str] = []
    handlers = getattr(bus, "_handlers", None)
    if isinstance(handlers, dict):
        for event_type, fns in handlers.items():
            for fn in fns:
                label = getattr(fn, "__qualname__", getattr(fn, "__name__", repr(fn)))
                names.append(f"{event_type} -> {label}")
    for fn in getattr(bus, "_wildcard", []) or []:
        names.append(f"* -> {getattr(fn, '__qualname__', repr(fn))}")
    return names


@router.get("/types")
async def event_types() -> dict[str, Any]:
    """Canonical domain events and what each one feeds."""
    consumers = {
        "POST_CREATED": ["denormalized read model", "feed fan-out", "search index"],
        "POST_UPDATED": ["denormalized read model", "search index"],
        "POST_DELETED": ["denormalized read model", "feed fan-out", "search index"],
        "COMMENT_CREATED": ["denormalized read model", "notifications"],
        "LIKE_CREATED": ["denormalized read model"],
        "LIKE_DELETED": ["denormalized read model"],
        "FOLLOW_CREATED": ["feed fan-out"],
        "FOLLOW_DELETED": ["feed fan-out"],
        "MEDIA_CREATED": ["media metadata projection"],
        "USER_CREATED": ["search index"],
    }
    return {
        "types": [
            {
                "event_type": member.value,
                "consumers": consumers.get(member.value, []),
            }
            for member in EventType
        ]
    }


@router.get("/recent")
async def recent_events(
    limit: int = Query(default=25, ge=1, le=200),
    event_type: str | None = Query(default=None),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """The most recent domain events published in this process.

    Only the in-memory transport retains a history; with RabbitMQ the events
    really left the process and are no longer listed here (``retained`` is
    false in that case).
    """
    bus = platform.event_bus
    published = getattr(bus, "published", None)
    if published is None:
        return {
            "events": [],
            "retained": False,
            "transport": getattr(bus, "transport", "unknown"),
            "note": (
                "this transport does not retain a local history; use RabbitMQ "
                "management or /api/v1/events counters to observe traffic"
            ),
        }
    rows = list(published)[::-1]
    if event_type:
        wanted = event_type.upper()
        rows = [e for e in rows if e.event_type == wanted]
    rows = rows[:limit]
    return {
        "events": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "entity_type": e.aggregate,
                "entity_id": e.aggregate_id,
                "timestamp": e.occurred_at.isoformat(),
                "payload": e.payload,
                "source": getattr(e, "source", None) or "api",
                "shard": e.shard_id,
            }
            for e in rows
        ],
        "retained": True,
        "transport": getattr(bus, "transport", "in_memory"),
        "count": len(rows),
    }
