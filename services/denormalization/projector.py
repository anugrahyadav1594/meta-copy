"""Member 3 — read-model projector (event -> ``denormalized_post_feed``).

Subscribes to the domain event bus and keeps the derived read model in step
with canonical writes. Because the projection is derived, it can always be
rebuilt: ``rebuild()`` recomputes every row from canonical PostgreSQL.

Status: IMPLEMENTED (the Java/RabbitMQ worker from the original branch is
superseded — the transport became a configuration choice, not a second
runtime).
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from db.session import session_scope
from denormalization import read_model
from events.bus import DomainEvent
from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger("denormalization.projector")


class DenormalizedFeedProjector:
    """Applies canonical events to the denormalized read model."""

    def __init__(
        self,
        engine: AsyncEngine,
        event_bus: Any,
        *,
        auto_schema: bool = True,
        shard_manager: Any = None,
    ) -> None:
        self.engine = engine
        self.event_bus = event_bus
        self.auto_schema = auto_schema
        # In SHARDED mode the canonical database is not where the posts are.
        self.shard_manager = shard_manager
        self.applied = 0
        self.skipped = 0
        self.errors = 0

    async def start(self) -> None:
        if self.auto_schema:
            await read_model.ensure_schema(self.engine)
        for event_type in (
            "POST_CREATED",
            "POST_UPDATED",
            "POST_DELETED",
            "LIKE_CREATED",
            "LIKE_DELETED",
            "COMMENT_CREATED",
        ):
            self.event_bus.subscribe(event_type, self.project)

    async def project(self, event: DomainEvent) -> None:
        try:
            async with session_scope(self.engine) as session:
                handled = await read_model.project_event(session, event)
            if handled:
                self.applied += 1
            else:
                self.skipped += 1
        except Exception as exc:  # projection failure must never break writes
            self.errors += 1
            logger.error(
                "projection_failed",
                event_type=getattr(event, "event_type", "?"),
                aggregate_id=getattr(event, "aggregate_id", None),
                error=type(exc).__name__,
                detail=str(getattr(exc, "orig", exc))[:300],
            )

    async def rebuild(self) -> int:
        """Full rebuild from canonical data (used by /read-model/rebuild)."""
        await read_model.ensure_schema(self.engine)
        return await read_model.rebuild(self.engine, self.shard_manager)

    def stats(self) -> dict[str, Any]:
        return {
            "table": "denormalized_post_feed",
            "derived": True,
            "events_applied": self.applied,
            "events_skipped": self.skipped,
            "errors": self.errors,
        }
