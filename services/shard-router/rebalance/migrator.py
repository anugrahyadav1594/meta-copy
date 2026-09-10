"""Functional online data migration for adding a shard.

Explicit, audited phases (no silent deletion)::

    PLANNED -> COPYING -> VERIFYING -> SWITCHING -> (cleanup) -> COMPLETED

* Rows are copied parent-table-first into the new shard.
* Checksums are compared on BOTH sides before the live ring changes.
* Routing ownership switches only after every table verifies.
* Source rows are deleted (child-first) only after the switch completes.
* Any verification failure -> FAILED: the live ring is not touched and no
  source data is deleted.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from common.enums import RebalancePhase, ShardStatus
from common.logging import get_logger
from db.shard_engine import ShardEngineManager
from metrics.collector import MetricsCollector
from models import Base
from router.shard_router import ShardRouter
from sqlalchemy.dialects.postgresql import insert as pg_insert

from rebalance.planner import RebalancePlanner
from rebalance.verifier import ChecksumVerifier

logger = get_logger("shard.rebalance")
BATCH = 1000


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RebalanceStep:
    phase: RebalancePhase
    detail: str
    at: datetime = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {"phase": self.phase.value, "detail": self.detail, "at": self.at.isoformat()}


@dataclass
class RebalanceReport:
    migration_id: str
    source_shard_id: str
    destination_shard_id: str
    table: str
    status: RebalancePhase
    keys_to_move: int
    rows_migrated: int
    source_checksum: dict[str, Any]
    destination_checksum: dict[str, Any]
    duration_ms: float
    steps: list[RebalanceStep]
    simulation: bool
    plan: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "source_shard_id": self.source_shard_id,
            "destination_shard_id": self.destination_shard_id,
            "table": self.table,
            "status": self.status.value,
            "keys_to_move": self.keys_to_move,
            "rows_migrated": self.rows_migrated,
            "source_checksum": self.source_checksum,
            "destination_checksum": self.destination_checksum,
            "duration_ms": round(self.duration_ms, 3),
            "steps": [s.as_dict() for s in self.steps],
            "simulation": self.simulation,
            "plan": self.plan,
        }


class RebalanceMigrator:
    def __init__(
        self,
        router: ShardRouter,
        manager: ShardEngineManager,
        metrics: MetricsCollector | None = None,
        registry: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self.router = router
        self.manager = manager
        self.metrics = metrics
        self.registry = registry
        self.audit = audit
        self.planner = RebalancePlanner(router)
        self.verifier = ChecksumVerifier()

    async def migrate_add_shard(
        self,
        new_shard_id: str,
        *,
        new_url: str | None = None,
        table: str = "users",
        dry_run: bool = False,
        source_hint: str | None = None,
    ) -> RebalanceReport:
        started = time.perf_counter()
        migration_id = f"mig-{uuid.uuid4().hex[:12]}"
        steps: list[RebalanceStep] = []
        rows_migrated = 0
        checksums: dict[str, dict[str, Any]] = {"source": {}, "destination": {}}

        def step(phase: RebalancePhase, detail: str) -> None:
            steps.append(RebalanceStep(phase, detail))
            logger.info(
                "rebalance_phase",
                migration_id=migration_id,
                phase=phase.value,
                detail=detail,
            )

        sources = source_hint or "(multiple)"
        report_kwargs: dict[str, Any] = {}
        try:
            # ------------------------------------------------ PLANNED
            plan = await self.planner.plan_for_add(
                new_shard_id, table=table, manager=self.manager, dry_run=dry_run
            )
            source_ids = sorted({src for tm in plan.tables for src, _ in tm.routes})
            sources = source_hint or (",".join(source_ids) if source_ids else "(none)")
            step(
                RebalancePhase.PLANNED,
                f"{plan.keys_to_move} anchor keys for {table} will move to "
                f"{new_shard_id} across {len(source_ids)} source shard(s)",
            )
            if self.audit:
                await self.audit.record_migration(
                    migration_id=migration_id,
                    source_shard_id=sources,
                    destination_shard_id=new_shard_id,
                    table_name=table,
                    phase=RebalancePhase.PLANNED.value,
                )

            if dry_run or new_url is None:
                # No destination engine registered -> planning SIMULATION.
                # SIMULATION: plan only, no physical movement performed.
                step(
                    RebalancePhase.PLANNED,
                    "SIMULATION: destination shard has no connection URL; "
                    "reporting movement plan without copying rows",
                )
                return RebalanceReport(
                    migration_id=migration_id,
                    source_shard_id=sources,
                    destination_shard_id=new_shard_id,
                    table=table,
                    status=RebalancePhase.PLANNED,
                    keys_to_move=plan.keys_to_move,
                    rows_migrated=0,
                    source_checksum={},
                    destination_checksum={},
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                    steps=steps,
                    simulation=True,
                    plan=plan.describe(),
                    **report_kwargs,
                )

            # register engine + empty schema on the new physical shard
            await self.manager.add_shard(new_shard_id, new_url)
            engine = await self.manager.engine_for(new_shard_id)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            if self.registry is not None:
                from metadata.models import ShardMetadata

                new_meta = ShardMetadata.from_url(
                    new_shard_id,
                    new_url,
                    virtual_nodes=self.router.virtual_nodes_per_shard,
                )
                new_meta.status = ShardStatus.REBALANCING
                await self.registry.add(new_meta)

            for tm in plan.tables:
                for (src, dst), keys in tm.routes.items():
                    if dst != new_shard_id:
                        continue
                    for i in range(0, len(keys), BATCH):
                        batch_keys = keys[i : i + BATCH]
                        # ----------------------------------------- COPYING
                        step(
                            RebalancePhase.COPYING,
                            f"{tm.table}: {len(batch_keys)} keys {src} -> {dst} "
                            f"(batch {i // BATCH + 1})",
                        )
                        rows = await self._fetch_rows(src, tm.table, tm.key_column, batch_keys)
                        await self._insert_rows(dst, tm.table, rows)
                        rows_migrated += len(rows)

                    # --------------------------------------- VERIFYING
                    if tm.routes:
                        step(RebalancePhase.VERIFYING, f"{tm.table}: checksum comparison")
                        async with (
                            self.manager.session_scope(src, readonly=True) as ssrc,
                            self.manager.session_scope(dst, readonly=True) as sdst,
                        ):
                            src_cs, dst_cs = await self.verifier.assert_match(
                                ssrc, sdst, tm.table, tm.key_column, keys
                            )
                        checksums["source"][tm.table] = src_cs.as_dict()
                        checksums["destination"][tm.table] = dst_cs.as_dict()

            # ------------------------------------------------ SWITCHING
            # Only reached if every table verified.
            step(RebalancePhase.SWITCHING, f"adding {new_shard_id} to the live routing ring")
            self.router.add_shard(new_shard_id)
            if self.metrics:
                self.metrics.register_shard(new_shard_id)
            if self.registry is not None:
                await self.registry.set_status(new_shard_id, ShardStatus.HEALTHY)

            # Cleanup: remove migrated rows from sources, CHILD tables first
            # (plan.tables is parent-first; reversed() deletes children first).
            for tm in reversed(plan.tables):
                for (src, dst), keys in tm.routes.items():
                    if dst != new_shard_id:
                        continue
                    for i in range(0, len(keys), BATCH):
                        await self._delete_rows(src, tm.table, tm.key_column, keys[i : i + BATCH])
                    step(
                        RebalancePhase.SWITCHING,
                        f"cleaned {tm.keys_to_move} migrated rows from {src}",
                    )

            step(
                RebalancePhase.COMPLETED,
                f"{rows_migrated} rows migrated; ring now routes to {new_shard_id}",
            )
            if self.metrics:
                self.metrics.record_migration(
                    rows_migrated, (time.perf_counter() - started) * 1000.0
                )
            if self.audit:
                await self.audit.record_migration(
                    migration_id=migration_id,
                    source_shard_id=sources,
                    destination_shard_id=new_shard_id,
                    table_name=table,
                    phase=RebalancePhase.COMPLETED.value,
                    rows_migrated=rows_migrated,
                )

            return RebalanceReport(
                migration_id=migration_id,
                source_shard_id=sources,
                destination_shard_id=new_shard_id,
                table=table,
                status=RebalancePhase.COMPLETED,
                keys_to_move=plan.keys_to_move,
                rows_migrated=rows_migrated,
                source_checksum=checksums["source"],
                destination_checksum=checksums["destination"],
                duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                steps=steps,
                simulation=False,
                plan=plan.describe(),
            )
        except Exception as exc:
            step(RebalancePhase.FAILED, f"{type(exc).__name__}: {str(exc)[:200]}")
            if self.metrics:
                self.metrics.record_migration(
                    rows_migrated, (time.perf_counter() - started) * 1000.0, failed=True
                )
            if self.audit:
                await self.audit.record_migration(
                    migration_id=migration_id,
                    source_shard_id=sources,
                    destination_shard_id=new_shard_id,
                    table_name=table,
                    phase=RebalancePhase.FAILED.value,
                    rows_migrated=rows_migrated,
                    detail=str(exc)[:500],
                )
            raise

    # ------------------------------------------------------------- IO helpers
    async def _fetch_rows(
        self, shard_id: str, table: str, key_column: str, keys: list[int]
    ) -> list[dict[str, Any]]:
        table_obj = Base.metadata.tables[table]
        async with self.manager.session_scope(shard_id, readonly=True) as session:
            res = await session.execute(table_obj.select().where(table_obj.c[key_column].in_(keys)))
            return [dict(row._mapping) for row in res]

    async def _insert_rows(self, shard_id: str, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table_obj = Base.metadata.tables[table]
        async with self.manager.session_scope(shard_id) as session:
            # ON CONFLICT DO NOTHING makes COPYING safely re-runnable (idempotent).
            await session.execute(pg_insert(table_obj).on_conflict_do_nothing(), rows)

    async def _delete_rows(
        self, shard_id: str, table: str, key_column: str, keys: list[int]
    ) -> None:
        table_obj = Base.metadata.tables[table]
        async with self.manager.session_scope(shard_id) as session:
            await session.execute(table_obj.delete().where(table_obj.c[key_column].in_(keys)))
