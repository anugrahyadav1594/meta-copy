"""Metadata describing a physical shard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

from common.enums import ShardStatus


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ShardMetadata:
    id: str
    url: str
    host: str | None = None
    port: int | None = None
    database: str | None = None
    status: ShardStatus = ShardStatus.BOOTSTRAPPING
    capacity: int = 1_000_000
    row_count: int = 0
    load: float = 0.0
    active_connections: int = 0
    virtual_nodes: int = 0
    is_hot: bool = False
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @classmethod
    def from_url(
        cls,
        shard_id: str,
        url: str,
        *,
        virtual_nodes: int = 0,
        capacity: int = 1_000_000,
    ) -> ShardMetadata:
        """Parse an SQLAlchemy/psycopg URL for host/port/database."""
        # SQLAlchemy driver form: postgresql+psycopg://user:pw@host:port/db
        parsed = urlparse(url.replace("postgresql+psycopg", "postgresql", 1))
        return cls(
            id=shard_id,
            url=url,
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=(parsed.path or "/postgres").lstrip("/") or "postgres",
            virtual_nodes=virtual_nodes,
            capacity=capacity,
        )

    def touch(self) -> None:
        self.updated_at = _now()

    def is_available(self) -> bool:
        return self.status in (ShardStatus.HEALTHY, ShardStatus.DEGRADED, ShardStatus.REBALANCING)
