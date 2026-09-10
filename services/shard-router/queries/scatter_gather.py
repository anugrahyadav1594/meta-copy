"""Scatter-gather queries: fan out to every shard concurrently, then merge.

This is the honest demonstration of the sharding trade-off: a query that has
no routing key (e.g. "latest posts across ALL users") cannot be localized, so
it contacts every healthy shard in parallel and merges/sorts/limits in the
application. Unavailable shards are reported per-shard rather than hidden.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from common.enums import QueryType
from common.logging import get_logger
from db.shard_engine import ShardEngineManager
from metrics.collector import MetricsCollector
from router.shard_router import ShardRouter

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger("shard.query.scatter")

ShardWork = Callable[["AsyncSession"], Awaitable[list[Any]]]


@dataclass
class ShardCallResult:
    shard_id: str
    rows: int
    latency_ms: float
    status: str  # "ok" | "unavailable" | "error"
    error: str | None = None
    values: list[Any] = field(default_factory=list)


@dataclass
class ScatterOutcome:
    rows: list[Any]
    shard_results: list[ShardCallResult]
    shards_contacted: list[str]
    unavailable_shards: list[str]
    total_rows_before_limit: int
    execution_time_ms: float
    merge_time_ms: float
    total_latency_ms: float
    query_type: QueryType = QueryType.SCATTER_GATHER


class ScatterGatherExecutor:
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
        work: ShardWork,
        *,
        shard_ids: list[str] | None = None,
        merge: Callable[[list[Any]], list[Any]] | None = None,
        limit: int | None = None,
    ) -> ScatterOutcome:
        total_start = time.perf_counter()
        targets = shard_ids or list(self.router.shard_ids)

        async def call_one(sid: str) -> ShardCallResult:
            start = time.perf_counter()
            try:
                async with self.manager.session_scope(sid, readonly=True) as session:
                    values = await work(session)
                latency = round((time.perf_counter() - start) * 1000.0, 3)
                if self.metrics:
                    self.metrics.record_query(sid, kind="read", latency_ms=latency)
                return ShardCallResult(sid, len(values), latency, "ok", None, list(values))
            except Exception as exc:  # noqa: BLE001 — per-shard containment
                latency = round((time.perf_counter() - start) * 1000.0, 3)
                status = "unavailable" if "unavailable" in type(exc).__name__.lower() else "error"
                if self.metrics:
                    self.metrics.record_query(sid, kind="read", latency_ms=latency, error=True)
                logger.warning(
                    "scatter_shard_failed",
                    shard_id=sid,
                    latency_ms=latency,
                    status=status,
                    detail=type(exc).__name__,
                )
                return ShardCallResult(sid, 0, latency, status, str(exc)[:200], [])

        exec_start = time.perf_counter()
        results = await asyncio.gather(*[call_one(sid) for sid in targets])
        execution_ms = round((time.perf_counter() - exec_start) * 1000.0, 3)

        merge_start = time.perf_counter()
        gathered: list[Any] = []
        for r in results:
            gathered.extend(r.values)
        merged = merge(gathered) if merge else gathered
        total_before_limit = len(merged)
        if limit is not None:
            merged = merged[:limit]
        merge_ms = round((time.perf_counter() - merge_start) * 1000.0, 3)

        unavailable = [r.shard_id for r in results if r.status == "unavailable"]
        if self.metrics:
            self.metrics.record_scatter_gather()
        total_ms = round((time.perf_counter() - total_start) * 1000.0, 3)
        logger.info(
            "scatter_gather",
            shards=len(targets),
            unavailable=len(unavailable),
            total_rows=total_before_limit,
            execution_ms=execution_ms,
            merge_ms=merge_ms,
            latency_ms=total_ms,
            query_type=QueryType.SCATTER_GATHER.value,
            status="success",
        )
        return ScatterOutcome(
            rows=merged,
            shard_results=list(results),
            shards_contacted=targets,
            unavailable_shards=unavailable,
            total_rows_before_limit=total_before_limit,
            execution_time_ms=execution_ms,
            merge_time_ms=merge_ms,
            total_latency_ms=total_ms,
        )
