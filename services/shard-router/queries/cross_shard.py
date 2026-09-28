"""Cross-shard "join" — performed honestly in the application.

A local SQL JOIN cannot span independent PostgreSQL instances. When a query
needs rows that live on different shards (e.g. a post on shard-A and its
comment author on shard-B), we issue targeted requests to each owning shard
and stitch the records together in the application.

The executor groups requests by owning shard, fans them out concurrently, and
reports the number of *network hops* (distinct shards contacted) so the cost
is visible in both metrics and the API response.
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

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger("shard.query.cross")

HopWork = Callable[["AsyncSession"], Awaitable[Any]]


@dataclass
class ShardHop:
    label: str  # e.g. "post:42", "user:7"
    shard_id: str
    key: int | str
    work: HopWork


@dataclass
class CrossShardOutcome:
    results: dict[str, Any] = field(default_factory=dict)
    shards_contacted: list[str] = field(default_factory=list)
    network_hops: int = 0
    latency_ms: float = 0.0
    errors: dict[str, str] = field(default_factory=dict)
    query_type: QueryType = QueryType.CROSS_SHARD
    note: str = (
        "Rows fetched from independent shards and joined in the application; "
        "a local SQL JOIN across PostgreSQL instances is impossible."
    )


class CrossShardExecutor:
    def __init__(
        self, manager: ShardEngineManager, metrics: MetricsCollector | None = None
    ) -> None:
        self.manager = manager
        self.metrics = metrics

    async def execute(self, hops: list[ShardHop]) -> CrossShardOutcome:
        start = time.perf_counter()
        shards = sorted({h.shard_id for h in hops})
        outcome = CrossShardOutcome(shards_contacted=shards, network_hops=len(shards))

        # Group work per physical shard so one connection can serve all hops
        # that happen to be co-located.
        grouped: dict[str, list[ShardHop]] = {sid: [] for sid in shards}
        for hop in hops:
            grouped[hop.shard_id].append(hop)

        async def run_group(sid: str, group: list[ShardHop]) -> dict[str, Any]:
            local: dict[str, Any] = {}
            gstart = time.perf_counter()
            async with self.manager.session_scope(sid, readonly=True) as session:
                for hop in group:
                    local[hop.label] = await hop.work(session)
            if self.metrics:
                self.metrics.record_query(
                    sid,
                    kind="read",
                    latency_ms=round((time.perf_counter() - gstart) * 1000.0, 3),
                )
            return local

        gathered = await asyncio.gather(
            *[run_group(sid, group) for sid, group in grouped.items()],
            return_exceptions=True,
        )
        for sid, result in zip(grouped.keys(), gathered, strict=True):
            if isinstance(result, Exception):
                outcome.errors[sid] = f"{type(result).__name__}: {str(result)[:160]}"
                logger.warning("cross_shard_hop_failed", shard_id=sid, detail=type(result).__name__)
            else:
                outcome.results.update(result)

        outcome.latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
        if self.metrics:
            self.metrics.record_cross_shard()
        logger.info(
            "cross_shard_join",
            hops=len(hops),
            shards_contacted=len(shards),
            network_hops=len(shards),
            latency_ms=outcome.latency_ms,
            query_type=QueryType.CROSS_SHARD.value,
        )
        return outcome
