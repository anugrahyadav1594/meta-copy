"""Targeted queries: the router decides the ONE shard that owns the key."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from common.enums import QueryType, RoutingStrategy
from common.exceptions import ShardUnavailableError
from common.logging import get_logger
from db.shard_engine import ShardEngineManager
from metrics.collector import MetricsCollector
from router.shard_router import RouteDecision, ShardRouter

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger("shard.query.targeted")

Work = Callable[["AsyncSession"], Awaitable[Any]]


@dataclass
class TargetedOutcome:
    result: Any
    decision: RouteDecision
    shard_contacted: str
    routing_latency_ms: float
    database_latency_ms: float
    total_latency_ms: float
    kind: str = "read"
    query_type: QueryType = QueryType.TARGETED
    log: list[str] = field(default_factory=list)


class TargetedExecutor:
    def __init__(
        self,
        router: ShardRouter,
        manager: ShardEngineManager,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.router = router
        self.manager = manager
        self.metrics = metrics

    async def execute(
        self,
        entity: str | None,
        key: int | str,
        work: Callable[[AsyncSession], Awaitable[Any]],
        *,
        kind: str = "read",
        strategy: RoutingStrategy | str | None = None,
        shard_id: str | None = None,
    ) -> TargetedOutcome:
        """Route ``key`` and execute ``work(session)`` on exactly one shard."""
        total_start = time.perf_counter()

        rstart = time.perf_counter()
        if shard_id is not None:
            decision = self.router.route(key, strategy=strategy)
            # explicit override (e.g. key-aligned insert already knows shard)
            decision = RouteDecision(
                entity=entity,
                shard_key=None,
                key=str(key),
                shard_id=shard_id,
                strategy=decision.strategy,
                shard_count=decision.shard_count,
                bucket=decision.bucket,
                virtual_node=decision.virtual_node,
            )
        elif entity is not None:
            decision = self.router.route_entity(entity, key, strategy=strategy)
        else:
            decision = self.router.route(key, strategy=strategy)
        routing_ms = round((time.perf_counter() - rstart) * 1000.0, 4)
        if self.metrics:
            self.metrics.record_routing(routing_ms)

        db_start = time.perf_counter()
        result: Any = None
        try:
            async with self.manager.session_scope(
                decision.shard_id, readonly=kind != "write"
            ) as session:
                result = await work(session)
        except Exception as exc:  # noqa: BLE001 — translated + re-raised
            if self.metrics:
                self.metrics.record_query(
                    decision.shard_id, kind=kind, latency_ms=_elapsed(db_start), error=True
                )
                if isinstance(exc, ShardUnavailableError):
                    self.metrics.routing_errors += 1
            logger.error(
                "targeted_query_failed",
                operation=f"targeted_{kind}",
                entity=entity,
                key=str(key),
                shard_id=decision.shard_id,
                strategy=decision.strategy.value,
                latency_ms=_elapsed(db_start),
                status="error",
            )
            raise
        db_ms = round((time.perf_counter() - db_start) * 1000.0, 3)
        if self.metrics:
            self.metrics.record_query(decision.shard_id, kind=kind, latency_ms=db_ms)
            self.metrics.set_active_connections(
                decision.shard_id, _checked_out(self.manager, decision.shard_id)
            )
        total_ms = round((time.perf_counter() - total_start) * 1000.0, 3)
        logger.info(
            "targeted_query",
            operation=f"targeted_{kind}",
            entity=entity,
            key=str(key),
            shard_id=decision.shard_id,
            strategy=decision.strategy.value,
            query_type=QueryType.TARGETED.value,
            routing_latency_ms=routing_ms,
            database_latency_ms=db_ms,
            latency_ms=total_ms,
            status="success",
        )
        return TargetedOutcome(
            result=result,
            decision=decision,
            shard_contacted=decision.shard_id,
            routing_latency_ms=routing_ms,
            database_latency_ms=db_ms,
            total_latency_ms=total_ms,
            kind=kind,
        )


def _elapsed(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def _checked_out(manager: ShardEngineManager, shard_id: str) -> int:
    try:
        return manager.pool_status(shard_id).get("checked_out", 0)
    except Exception:  # pragma: no cover
        return 0
