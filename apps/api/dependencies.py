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

from common.config import SHARDED_MODES, Settings, get_settings
from db.engine import make_engine
from db.repositories.canonical import (
    CanonicalCommentRepository,
    CanonicalFollowRepository,
    CanonicalHashtagRepository,
    CanonicalLikeRepository,
    CanonicalMediaRepository,
    CanonicalNotificationRepository,
    CanonicalPostRepository,
    CanonicalUserRepository,
)
from db.repositories.sharded import (
    ShardedCommentRepository,
    ShardedFollowRepository,
    ShardedHashtagRepository,
    ShardedLikeRepository,
    ShardedMediaRepository,
    ShardedNotificationRepository,
    ShardedPostRepository,
    ShardedUserRepository,
)
from db.shard_engine import ShardEngineManager
from events.bus import build_event_bus
from health.health_checker import ShardHealthChecker
from hotspots.detector import HotShardDetector
from metadata.models import ShardMetadata
from metadata.registry import InMemoryShardRegistry
from metrics.collector import MetricsCollector
from rebalance.migrator import RebalanceMigrator
from router.shard_router import ShardRouter

from services.cache.cache import CacheProvider
from services.denormalization.projector import DenormalizedFeedProjector
from services.feed.service import FeedProjector, FeedService
from services.graph.service import SocialGraph
from services.media.blobstore import build_blob_store
from services.media.service import MediaService
from services.replication.provider import ReplicationAwareProvider
from services.search.indexer import SearchIndexer
from services.search.provider import build_search_provider


@dataclass
class Platform:
    settings: Settings
    sharding_available: bool = False
    # canonical
    canonical_engine: object = None
    user_repo: object = None
    post_repo: object = None
    comment_repo: object = None
    like_repo: object = None
    follow_repo: object = None
    media_repo: object = None
    notification_repo: object = None
    hashtag_repo: object = None
    # domain events (canonical -> derived systems)
    event_bus: object = None
    # derived systems (all rebuildable from canonical PostgreSQL)
    denormalizer: object = None
    feed_service: object = None
    feed_projector: object = None
    media_service: object = None
    graph: object = None
    search_indexer: object = None
    replication: object = None
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
    sharded_like_repo: ShardedLikeRepository | None = None
    sharded_follow_repo: ShardedFollowRepository | None = None
    sharded_media_repo: ShardedMediaRepository | None = None
    sharded_notification_repo: ShardedNotificationRepository | None = None
    sharded_hashtag_repo: ShardedHashtagRepository | None = None
    audit: object = None
    started: bool = False

    # ------------------------------------------------------------------ build
    async def start(self) -> None:
        if self.started:
            return

        # Redis cache-aside (Member 6). Only attempted when CACHE_ENABLED;
        # fails open to None if Redis cannot be reached, so PostgreSQL always
        # keeps serving.
        # Domain events (shared infrastructure). In-memory by default;
        # RabbitMQ when EVENT_BUS=rabbitmq; disabled with EVENT_BUS=off.
        if self.event_bus is None:
            self.event_bus = build_event_bus(self.settings.event_bus, self.settings.rabbitmq_url)
            if self.settings.event_bus == "rabbitmq" and hasattr(self.event_bus, "connect"):
                await self.event_bus.connect()

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
            # Default traffic path follows the deployment mode: in every
            # shard mode (SHARDED, SHARDED_REPLICATED, SHARDED_CACHED,
            # FULL_DISTRIBUTED) the generic endpoints route through the shard
            # router, so /api/v1/users and /api/v1/posts behave identically
            # to the /sharded/* endpoints. Only NORMALIZED / DENORMALIZED
            # talk to the canonical database directly.
            if self.settings.mode in SHARDED_MODES:
                self._use_sharded_repos()
            else:
                self._build_canonical_repos(engine)
        else:
            self._build_canonical_repos(engine)

        await self._build_derived_systems()
        self.started = True

    def _build_canonical_repos(self, engine: object) -> None:
        self.user_repo = CanonicalUserRepository(engine)  # type: ignore[arg-type]
        self.post_repo = CanonicalPostRepository(engine)  # type: ignore[arg-type]
        self.comment_repo = CanonicalCommentRepository(engine)  # type: ignore[arg-type]
        self.like_repo = CanonicalLikeRepository(engine)  # type: ignore[arg-type]
        self.follow_repo = CanonicalFollowRepository(engine)  # type: ignore[arg-type]
        self.media_repo = CanonicalMediaRepository(engine)  # type: ignore[arg-type]
        self.notification_repo = CanonicalNotificationRepository(engine)  # type: ignore[arg-type]
        self.hashtag_repo = CanonicalHashtagRepository(engine)  # type: ignore[arg-type]

    def _use_sharded_repos(self) -> None:
        self.user_repo = self.sharded_user_repo
        self.post_repo = self.sharded_post_repo
        self.comment_repo = self.sharded_comment_repo
        self.like_repo = self.sharded_like_repo
        self.follow_repo = self.sharded_follow_repo
        self.media_repo = self.sharded_media_repo
        self.notification_repo = self.sharded_notification_repo
        self.hashtag_repo = self.sharded_hashtag_repo

    async def _build_derived_systems(self) -> None:
        """Wire the derived modules on top of the canonical repositories.

        Every one of these is a PROJECTION: it can be dropped and rebuilt
        from canonical PostgreSQL without losing data. None of them may be
        written to directly by the API as if it were authoritative.
        """
        s = self.settings

        # --- feed (Member 7)
        self.feed_service = FeedService(
            self.follow_repo,
            self.post_repo,
            strategy=s.feed_strategy,
            candidate_limit=s.feed_candidate_limit,
            engine=self.canonical_engine,
        )
        if s.feed_strategy == "push":
            await self.feed_service.ensure_push_table()
            self.feed_projector = FeedProjector(
                self.canonical_engine,
                self.follow_repo,
                self.event_bus,
                shard_manager=self.shard_manager,
            )
            await self.feed_projector.start()

        # --- denormalized read model (Member 3)
        if s.denormalized_active:
            self.denormalizer = DenormalizedFeedProjector(
                self.canonical_engine,
                self.event_bus,
                shard_manager=self.shard_manager,
            )
            await self.denormalizer.start()

        # --- media (Member 8): blob store + metadata in PostgreSQL
        self.media_service = MediaService(
            self.media_repo,
            build_blob_store(s),
            self.event_bus,
            max_upload_mb=s.media_max_upload_mb,
        )

        # --- TAO-inspired graph (Member 9): derived adjacency over follows
        self.graph = SocialGraph(
            self.follow_repo,
            cache_ttl_seconds=s.graph_projection_ttl if s.graph_projection_enabled else 0,
        )

        # --- search (Member 10): inverted index built from canonical data
        self.search_indexer = SearchIndexer(
            build_search_provider(s),
            self.canonical_engine,
            self.event_bus,
            shard_manager=self.shard_manager,
        )
        await self.search_indexer.start()

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
        # Member 5: replica selection happens BELOW the shard router. With no
        # SHARD_n_REPLICA_URL configured the provider is primary-only.
        replicas = self.settings.replica_urls()
        self.replication = ReplicationAwareProvider(
            urls,
            replicas,
            settings=self.settings,
            read_from_replicas=self.settings.replica_reads_enabled,
            max_lag_seconds=self.settings.replica_max_lag_seconds,
        )
        manager = ShardEngineManager(urls, self.settings, provider=self.replication)
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
        sharded_likes = ShardedLikeRepository(router, manager, metrics)
        sharded_follows = ShardedFollowRepository(router, manager, metrics)
        sharded_media = ShardedMediaRepository(router, manager, metrics)
        sharded_notifications = ShardedNotificationRepository(router, manager, metrics)
        sharded_hashtags = ShardedHashtagRepository(router, manager, metrics)

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
        self.sharded_like_repo = sharded_likes
        self.sharded_follow_repo = sharded_follows
        self.sharded_media_repo = sharded_media
        self.sharded_notification_repo = sharded_notifications
        self.sharded_hashtag_repo = sharded_hashtags

        if self.settings.mode.value != "SHARDED":
            # generic endpoints still use canonical, but /sharded works too
            pass

    async def shutdown(self) -> None:
        if self.replication is not None:
            await self.replication.aclose()
            self.replication = None
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
