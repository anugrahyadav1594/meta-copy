"""Health probe result models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from common.enums import ShardStatus


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class HealthProbe:
    shard_id: str
    reachable: bool
    latency_ms: float | None
    status: ShardStatus
    detail: str | None
    checked_at: datetime

    @classmethod
    def ok(cls, shard_id: str, latency_ms: float) -> HealthProbe:
        return cls(
            shard_id=shard_id,
            reachable=True,
            latency_ms=round(latency_ms, 3),
            status=ShardStatus.HEALTHY,
            detail=None,
            checked_at=_now(),
        )

    @classmethod
    def down(cls, shard_id: str, detail: str) -> HealthProbe:
        return cls(
            shard_id=shard_id,
            reachable=False,
            latency_ms=None,
            status=ShardStatus.UNAVAILABLE,
            detail=detail,
            checked_at=_now(),
        )
