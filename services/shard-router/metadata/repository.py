"""Durable persistence for shard metadata / migration audit.

The in-memory registry remains the runtime authority, but ownership changes
and migrations are audited in the *canonical* PostgreSQL instance through
these two operational tables::

    shard_registry_state  — latest known state of every shard
    shard_migration_log    — append-only audit of rebalancing phases

The tables live outside the canonical 3NF social schema (they are operational
metadata, not domain data). This class is optional: the system works fully
without it, which keeps unit tests dependency-free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.enums import ShardStatus
from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text

from metadata.models import ShardMetadata

operational_metadata = MetaData()

shard_registry_state = Table(
    "shard_registry_state",
    operational_metadata,
    Column("shard_id", String(64), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("host", String(255)),
    Column("port", Integer),
    Column("database", String(128)),
    Column("row_count", BigInteger, default=0),
    Column("load", String(32)),
    Column("virtual_nodes", Integer, default=0),
    Column("updated_at", DateTime(timezone=True)),
)

shard_migration_log = Table(
    "shard_migration_log",
    operational_metadata,
    Column("migration_id", String(64), primary_key=True),
    Column("source_shard_id", String(64), nullable=False),
    Column("destination_shard_id", String(64), nullable=False),
    Column("table_name", String(64), nullable=False),
    Column("phase", String(32), nullable=False),
    Column("rows_migrated", BigInteger, default=0),
    Column("detail", Text),
    Column("created_at", DateTime(timezone=True)),
)


class ShardRegistryRepository:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def create_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(operational_metadata.create_all)

    async def save_snapshot(self, shards: list[ShardMetadata]) -> None:
        await self.create_tables()
        now = datetime.now(UTC)
        async with self._engine.begin() as conn:
            for s in shards:
                row = {
                    "shard_id": s.id,
                    "status": s.status.value,
                    "host": s.host,
                    "port": s.port,
                    "database": s.database,
                    "row_count": s.row_count,
                    "load": str(s.load),
                    "virtual_nodes": s.virtual_nodes,
                    "updated_at": s.updated_at or now,
                }
                await conn.execute(
                    shard_registry_state.delete().where(shard_registry_state.c.shard_id == s.id)
                )
                await conn.execute(shard_registry_state.insert().values(**row))

    async def load_snapshot(self) -> list[dict[str, Any]]:
        await self.create_tables()
        async with self._engine.connect() as conn:
            result = await conn.execute(shard_registry_state.select())
            return [dict(r._mapping) for r in result]

    async def record_migration(
        self,
        *,
        migration_id: str,
        source_shard_id: str,
        destination_shard_id: str,
        table_name: str,
        phase: str,
        rows_migrated: int = 0,
        detail: str = "",
    ) -> None:
        await self.create_tables()
        async with self._engine.begin() as conn:
            await conn.execute(
                shard_migration_log.delete().where(
                    shard_migration_log.c.migration_id == migration_id
                )
            )
            await conn.execute(
                shard_migration_log.insert().values(
                    migration_id=migration_id,
                    source_shard_id=source_shard_id,
                    destination_shard_id=destination_shard_id,
                    table_name=table_name,
                    phase=phase,
                    rows_migrated=rows_migrated,
                    detail=detail,
                    created_at=datetime.now(UTC),
                )
            )

    @staticmethod
    def status_of(meta: ShardMetadata) -> ShardStatus:
        return meta.status
