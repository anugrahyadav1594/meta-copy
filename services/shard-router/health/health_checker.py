"""Active shard health checking.

IMPORTANT BOUNDARY: the checker only decides *shard liveness* and updates the
registry. It does NOT fail over to a replica — primary/replica selection is
Member 5's responsibility. The seam it will use is
:class:`db.shard_engine.ShardConnectionProvider`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from common.enums import ShardStatus
from common.logging import get_logger
from metadata.registry import InMemoryShardRegistry

from health.models import HealthProbe

if TYPE_CHECKING:
    from db.shard_engine import ShardEngineManager

logger = get_logger("shard.health")


class ShardHealthChecker:
    def __init__(self, manager: ShardEngineManager, registry: InMemoryShardRegistry) -> None:
        self._manager = manager
        self._registry = registry

    async def check(self, shard_id: str) -> HealthProbe:
        """Probe one shard with ``SELECT 1`` and update its registry state."""
        try:
            latency_ms = await self._manager.ping(shard_id)
        except Exception as exc:  # network/timeout/auth/pool exhaustion
            await self._registry.set_status(shard_id, ShardStatus.UNAVAILABLE)
            self._manager.mark_down(shard_id)
            detail = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("shard_unreachable", shard_id=shard_id, detail=detail)
            return HealthProbe.down(shard_id, detail)
        else:
            # A previously degraded shard that now answers is healthy again.
            current = await self._registry.get(shard_id)
            if current.status in (
                ShardStatus.UNAVAILABLE,
                ShardStatus.DEGRADED,
                ShardStatus.BOOTSTRAPPING,
            ):
                await self._registry.set_status(shard_id, ShardStatus.HEALTHY)
            self._manager.mark_up(shard_id)
            return HealthProbe.ok(shard_id, latency_ms)

    async def check_all(self) -> list[HealthProbe]:
        shards = await self._registry.list()
        results = await asyncio.gather(*[self.check(s.id) for s in shards], return_exceptions=False)
        return list(results)
