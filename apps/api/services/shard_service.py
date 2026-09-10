"""Sharding orchestration service (Member 4 API surface).

Pure CRUD remains in the sharded repositories; this service handles shard
listing/stats, routing, health, distribution, rebalancing, the hot-user
workload generator, and the cross-shard join demonstration.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from common.enums import RoutingStrategy, ShardStatus
from common.exceptions import NotFoundError, ShardNotFoundError
from models import User
from models.base import Base
from queries.cross_shard import CrossShardExecutor, ShardHop
from schemas.shard import RebalanceRequest
from sqlalchemy import func, select


class ShardService:
    def __init__(self, platform: Any) -> None:
        self.p = platform
        self.cross = CrossShardExecutor(platform.shard_manager, platform.metrics)

    # ------------------------------------------------------------- registry
    async def list_shards(self) -> dict[str, Any]:
        p = self.p
        assert p.registry and p.router and p.metrics
        hot_ids = {h.shard_id for h in p.detector.detect()}
        counts = p.metrics.request_counts()
        p.registry.update_loads(counts, hot_ids=hot_ids)
        shards = await p.registry.list()
        return {
            "strategy": p.router.strategy,
            "shard_count": len(shards),
            "shards": [self._summary(s) for s in shards],
        }

    async def get_shard(self, shard_id: str) -> dict[str, Any]:
        p = self.p
        assert p.registry
        try:
            shard = await p.registry.get(shard_id)
        except ShardNotFoundError:
            raise
        return self._summary(shard)

    def _summary(self, shard: Any) -> dict[str, Any]:
        p = self.p
        assert p.metrics and p.router
        per = p.metrics.snapshot(p.registry)["shards"].get(shard.id, {})
        return {
            "id": shard.id,
            "status": shard.status,
            "host": shard.host,
            "port": shard.port,
            "database": shard.database,
            "load": shard.load if shard.load else per.get("load_fraction", 0.0),
            "row_count": shard.row_count or per.get("rows", 0),
            "capacity": shard.capacity,
            "active_connections": per.get("active_connections", shard.active_connections),
            "virtual_nodes": shard.virtual_nodes or p.router.virtual_nodes_per_shard,
            "requests": per.get("requests", 0),
            "is_hot": shard.is_hot,
            "created_at": shard.created_at,
            "updated_at": shard.updated_at,
        }

    # --------------------------------------------------------------- routing
    async def route_key(
        self, key: str, strategy: RoutingStrategy | str | None = None, entity: str | None = None
    ) -> dict[str, Any]:
        p = self.p
        assert p.router
        start = time.perf_counter()
        if entity:
            decision = p.router.route_entity(entity, key, strategy=strategy)
        else:
            decision = p.router.route(key, strategy=strategy)
        latency = round((time.perf_counter() - start) * 1000.0, 5)
        if p.metrics:
            p.metrics.record_routing(latency)
        return {
            "key": str(key),
            "shard_id": decision.shard_id,
            "strategy": decision.strategy,
            "shard_count": decision.shard_count,
            "entity": decision.entity,
            "virtual_node": decision.virtual_node,
            "routing_latency_ms": latency,
        }

    async def distribution(
        self, sample_size: int = 10_000, strategy: RoutingStrategy | str | None = None
    ) -> dict[str, Any]:
        p = self.p
        assert p.router
        sample_size = max(1, min(sample_size, 1_000_000))
        keys = list(range(1, sample_size + 1))
        percentages = p.router.distribution(keys, strategy=strategy)
        return {
            "strategy": strategy or p.router.strategy,
            "sample_size": sample_size,
            "virtual_nodes_per_shard": p.router.virtual_nodes_per_shard,
            "distribution": [
                {"shard_id": sid, "keys": round(pct * sample_size / 100), "percentage": pct}
                for sid, pct in percentages.items()
            ],
        }

    # ---------------------------------------------------------------- stats
    async def stats(self, *, refresh_rows: bool = True) -> dict[str, Any]:
        p = self.p
        assert p.metrics and p.registry and p.detector
        if refresh_rows:
            try:
                await self.refresh_row_counts()
            except Exception:  # shards may be down; never fail stats on counts
                pass
        snapshot = p.metrics.snapshot(p.registry)
        hot = [h.describe() for h in p.detector.detect()]
        snapshot["hot_shards"] = hot
        snapshot["strategy"] = p.router.strategy.value if p.router else None
        return snapshot

    async def refresh_row_counts(self) -> dict[str, int]:
        p = self.p
        assert p.shard_manager and p.registry and p.router and p.metrics
        tables = list(Base.metadata.tables.values())

        async def count_shard(sid: str) -> tuple[str, int]:
            total = 0
            async with p.shard_manager.session_scope(sid, readonly=True) as session:
                for table in tables:
                    res = await session.execute(select(func.count()).select_from(table))
                    total += int(res.scalar_one())
            return sid, total

        results = await asyncio.gather(
            *[count_shard(sid) for sid in p.router.shard_ids], return_exceptions=True
        )
        counts: dict[str, int] = {}
        for sid, result in zip(p.router.shard_ids, results, strict=True):
            if not isinstance(result, Exception):
                counts[sid] = result
        p.registry.set_row_counts(counts)
        p.metrics.set_row_counts(counts)
        return counts

    # ---------------------------------------------------------------- health
    async def health_check(self, shard_id: str) -> dict[str, Any]:
        p = self.p
        assert p.health_checker and p.registry
        if not p.shard_manager or not p.shard_manager.has(shard_id):
            raise ShardNotFoundError(shard_id)
        probe = await p.health_checker.check(shard_id)
        return {
            "shard_id": probe.shard_id,
            "status": probe.status,
            "reachable": probe.reachable,
            "latency_ms": probe.latency_ms,
            "detail": probe.detail,
            "checked_at": probe.checked_at,
        }

    async def simulate_down(self, shard_id: str) -> dict[str, Any]:
        """DEVELOPER/DEMO ONLY (SIMULATION): mark a shard UNAVAILABLE.

        Does not stop the container; it flips the same health flag a real
        failed ping would. A subsequent health-check brings it back.
        """
        p = self.p
        assert p.registry and p.shard_manager
        if not p.shard_manager.has(shard_id):
            raise ShardNotFoundError(shard_id)
        p.shard_manager.mark_down(shard_id)
        await p.registry.set_status(shard_id, ShardStatus.UNAVAILABLE)
        shard = await p.registry.get(shard_id)
        return {
            "shard_id": shard_id,
            "status": shard.status,
            "reachable": False,
            "note": "SIMULATION: shard marked UNAVAILABLE for failure handling demo",
        }

    # ------------------------------------------------------------ rebalance
    async def rebalance(self, request: RebalanceRequest) -> dict[str, Any]:
        p = self.p
        assert p.router and p.migrator
        new_index = len(p.router.shard_ids)
        new_shard_id = request.new_shard_id or f"shard-{new_index}"
        new_url = request.new_shard_url or self._url_for(new_index)
        source_hint = request.source_shard_id
        if source_hint is None:
            hottest = p.detector.hottest() if p.detector else None
            source_hint = hottest.shard_id if hottest else None

        report = await p.migrator.migrate_add_shard(
            new_shard_id,
            new_url=new_url,
            table=request.table,
            dry_run=request.dry_run,
            source_hint=source_hint,
        )
        return report.as_dict()

    def _url_for(self, index: int) -> str | None:
        import os

        return os.environ.get(f"SHARD_{index}_URL")

    # ------------------------------------------------------- hot-user demo
    async def hot_user_workload(
        self,
        hot_user_id: int,
        *,
        requests: int = 400,
        spread_user_ids: int = 1000,
        skew: float = 0.8,
        concurrency: int = 32,
        seed: int = 4242,
    ) -> dict[str, Any]:
        """DEMO/DEV ONLY: generate skewed read traffic (celebrity problem).

        ``skew`` fraction of requests target ``hot_user_id``; the rest are
        spread uniformly. All requests are REAL targeted router queries.
        """
        p = self.p
        assert p.sharded_user_repo and p.router and p.metrics and p.registry
        rng = random.Random(seed)
        keys = [
            hot_user_id if rng.random() < skew else rng.randint(1, max(2, spread_user_ids))
            for _ in range(requests)
        ]
        sem = asyncio.Semaphore(concurrency)

        async def one(k: int) -> None:
            async with sem:
                try:
                    await p.sharded_user_repo.get_by_id_traced(k)
                except Exception:  # missing rows still record per-shard load
                    pass

        started = time.perf_counter()
        await asyncio.gather(*[one(k) for k in keys])
        elapsed = round((time.perf_counter() - started) * 1000.0, 3)

        hot_ids = {h.shard_id for h in p.detector.detect()}
        p.registry.update_loads(p.metrics.request_counts(), hot_ids=hot_ids)
        snapshot = p.metrics.snapshot(p.registry)
        return {
            "note": "DEMO workload — real targeted queries, skewed access pattern",
            "hot_user_id": hot_user_id,
            "hot_shard": p.router.shard_for(hot_user_id),
            "requests": requests,
            "skew": skew,
            "elapsed_ms": elapsed,
            "per_shard": {
                sid: {
                    "requests": data["requests"],
                    "load_pct": round(data["load_fraction"] * 100, 1),
                    "p95_latency_ms": data["p95_latency_ms"],
                }
                for sid, data in snapshot["shards"].items()
            },
            "hot_shards": [h.describe() for h in p.detector.detect()],
        }

    # ----------------------------------------------- cross-shard join demo
    async def post_cross_shard(self, post_id: int) -> dict[str, Any]:
        """Fetch post + comments + every author, stitching across shards."""
        p = self.p
        assert p.router and p.shard_manager and p.sharded_post_repo
        assert p.sharded_comment_repo and p.sharded_user_repo

        post, post_outcome = await p.sharded_post_repo.get_by_id_traced(post_id)
        if post is None:
            raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})
        comments, comment_outcome = await p.sharded_comment_repo.list_for_post_traced(post_id)

        author_ids = sorted({post.author_id, *(c.user_id for c in comments)})
        groups: dict[str, list[int]] = {}
        for uid in author_ids:
            sid = p.router.shard_for(uid)
            groups.setdefault(sid, []).append(uid)

        # Author rows are fetched with a grouped, concurrent cross-shard fanout.
        hops = [
            ShardHop(
                label=f"users:{sid}",
                shard_id=sid,
                key=",".join(map(str, ids)),
                work=self._users_hop(ids),
            )
            for sid, ids in groups.items()
        ]
        outcome = await self.cross.execute(hops)
        if outcome.errors:
            from common.exceptions import CrossShardQueryError

            raise CrossShardQueryError(
                "cross-shard assembly had failed hops", {"errors": outcome.errors}
            )

        merged_authors: dict[int, dict[str, Any]] = {}
        owner_shards: dict[int, str] = {}
        for sid in groups:
            for uid, row in outcome.results.get(f"users:{sid}", {}).items():
                merged_authors[uid] = row
                owner_shards[uid] = sid

        # The post + comments shard was already contacted (targeted queries);
        # total distinct physical shards touched by this request:
        all_shards = sorted(
            {post_outcome.shard_contacted, comment_outcome.shard_contacted, *owner_shards.values()}
        )
        return {
            "post": self._post_dict(post),
            "post_shard": post_outcome.shard_contacted,
            "comments": [
                {
                    "id": c.id,
                    "user_id": c.user_id,
                    "content": c.content,
                    "author_shard": owner_shards.get(c.user_id),
                    "author": merged_authors.get(c.user_id),
                }
                for c in comments
            ],
            "author_of_post": {
                "user_id": post.author_id,
                "shard_id": p.router.shard_for(post.author_id),
            },
            "network_hops": len(all_shards),
            "shards_contacted": all_shards,
            "cross_shard_fanout_latency_ms": outcome.latency_ms,
            "fanout_shards": outcome.shards_contacted,
            "note": outcome.note,
        }

    def _users_hop(self, ids: list[int]) -> Any:
        async def work(session: Any) -> dict[int, dict[str, Any]]:
            res = await session.execute(select(User).where(User.id.in_(ids)))
            return {
                u.id: {"id": u.id, "username": u.username, "email": u.email}
                for u in res.scalars().all()
            }

        return work

    @staticmethod
    def _post_dict(post: Any) -> dict[str, Any]:
        return {
            "id": post.id,
            "author_id": post.author_id,
            "content": post.content,
            "visibility": post.visibility,
            "created_at": post.created_at.isoformat() if post.created_at else None,
        }
