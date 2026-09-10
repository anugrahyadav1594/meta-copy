"""Composition root / dependencies.

Wires the single :class:`Platform` singleton according to ``MODE``:

* NORMALIZED -> canonical engine + canonical repositories
* SHARDED    -> shard registry, ShardRouter, per-shard engines, metrics,
                health checker, detector, migrator, sharded repositories

FastAPI dependencies return the same singleton; tests construct a Platform
with explicit settings/engines.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.config import Settings, get_settings
from db.engine import make_engine
from db.repositories.canonical import (
    CanonicalCommentRepository,
    CanonicalPostRepository,
    CanonicalUserRepository,
)
from db.repositories.sharded import (
    ShardedCommentRepository,
    ShardedPostRepository,
    ShardedUserRepository,
)
from db.shard_engine import ShardEngineManager
from health.health_checker import ShardHealthChecker
from hotspots.detector import HotShardDetector
from metadata.models import ShardMetadata
from metadata.registry import InMemoryShardRegistry
from metrics.collector import MetricsCollector
from rebalance.migrator import RebalanceMigrator
from router.shard_router import ShardRouter

from services.cache.cache import CacheProvider


@dataclass
class Platform:
    settings: Settings
    sharding_available: bool = False
    # canonical
    canonical_engine: object = None
    user_repo: object = None
    post_repo: object = None
    comment_repo: object = None
    # Redis cache-aside (Member 6); None when caching is disabled or Redis
    # failed startup and the system failed open.
    cache: CacheProvider | None = None
    # sharding components
    registry: InMemoryShardRegistry | None = None
    router: ShardRouter | None = None
    shard_manager: ShardEngineManager | None = None
    metrics: MetricsCollector | None = None
    health_checker: ShardHealthChecker | None = None
    detector: HotShardDetector | None = None
    migrator: RebalanceMigrator | None = None
    sharded_user_repo: ShardedUserRepository | None = None
    sharded_post_repo: ShardedPostRepository | None = None
    sharded_comment_repo: ShardedCommentRepository | None = None
    audit: object = None
    started: bool = False

    # ------------------------------------------------------------------ build
    async def start(self) -> None:
        if self.started:
            return

        # Redis cache-aside (Member 6). Only attempted when CACHE_ENABLED;
        # fails open to None if Redis cannot be reached, so PostgreSQL always
        # keeps serving.
        if self.settings.cache_enabled:
            self.cache = await CacheProvider.create(
                self.settings.redis_url,
                default_ttl=self.settings.cache_default_ttl,
                hot_key_threshold=self.settings.cache_hot_key_threshold,
                connect_timeout=self.settings.cache_connect_timeout,
                fail_open=self.settings.cache_fail_open,
            )

        engine = make_engine(self.settings.database_url, self.settings)
        self.canonical_engine = engine

        if self.settings.sharding_active and self.settings.shard_urls():
            await self._build_sharding()
            # Default traffic path follows the deployment mode: in SHARDED
            # mode even the generic endpoints route through shards.
            if self.settings.mode.value == "SHARDED":
                self.user_repo = self.sharded_user_repo
                self.post_repo = self.sharded_post_repo
                self.comment_repo = self.sharded_comment_repo
            else:
                self._build_canonical_repos(engine)
        else:
            self._build_canonical_repos(engine)
        self.started = True

    def _build_canonical_repos(self, engine: object) -> None:
        self.user_repo = CanonicalUserRepository(engine)  # type: ignore[arg-type]
        self.post_repo = CanonicalPostRepository(engine)  # type: ignore[arg-type]
        self.comment_repo = CanonicalCommentRepository(engine)  # type: ignore[arg-type]

    async def _build_sharding(self) -> None:
        urls = self.settings.shard_urls()
        shard_ids = list(urls)
        strategy = self.settings.sharding_strategy
        registry = InMemoryShardRegistry(
            ShardMetadata.from_url(sid, url, virtual_nodes=self.settings.virtual_nodes_per_shard)
            for sid, url in urls.items()
        )
        metrics = MetricsCollector(shard_ids)
        router = ShardRouter(
            shard_ids,
            strategy=strategy,
            virtual_nodes_per_shard=self.settings.virtual_nodes_per_shard,
        )
        manager = ShardEngineManager(urls, self.settings)
        checker = ShardHealthChecker(manager, registry)
        detector = HotShardDetector(
            metrics,
            load_ratio_threshold=self.settings.hot_shard_load_ratio_threshold,
            min_requests=self.settings.hot_shard_min_requests,
            relative_ratio=self.settings.hot_shard_relative_ratio,
        )
        migrator = RebalanceMigrator(router, manager, metrics, registry, audit=None)
        sharded_users = ShardedUserRepository(router, manager, metrics)
        sharded_posts = ShardedPostRepository(router, manager, metrics)
        sharded_comments = ShardedCommentRepository(router, manager, metrics)

        self.sharding_available = True
        self.registry = registry
        self.router = router
        self.shard_manager = manager
        self.metrics = metrics
        self.health_checker = checker
        self.detector = detector
        self.migrator = migrator
        self.sharded_user_repo = sharded_users
        self.sharded_post_repo = sharded_posts
        self.sharded_comment_repo = sharded_comments

        if self.settings.mode.value != "SHARDED":
            # generic endpoints still use canonical, but /sharded works too
            pass

    async def shutdown(self) -> None:
        if self.cache is not None:
            await self.cache.aclose()
            self.cache = None
        if self.shard_manager is not None:
            await self.shard_manager.dispose()
        if self.canonical_engine is not None:
            await self.canonical_engine.dispose()
        self.canonical_engine = None
        self.started = False

    def require_sharding(self) -> None:
        if not self.sharding_available:
            from common.exceptions import NotConfiguredError

            raise NotConfiguredError(
                "Sharding is not enabled. Start with shard URLs configured "
                "(SHARDING_ENABLED=true) or `make sharding`."
            )


_platform: Platform | None = None


def get_platform() -> Platform:
    """FastAPI dependency: process-wide singleton (sync; start() in lifespan)."""
    global _platform
    if _platform is None:
        _platform = Platform(settings=get_settings())
    return _platform


async def platform_lifespan(app: object) -> object:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: object) -> AsyncIterator[None]:
        platform = get_platform()
        await platform.start()
        if platform.sharding_available and platform.health_checker is not None:
            await platform.health_checker.check_all()
        try:
            yield
        finally:
            await platform.shutdown()

    return lifespan
