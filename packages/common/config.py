"""Central configuration for MetaScale, built on Pydantic Settings.

One configuration system for the whole project. Credentials only ever come
from environment variables (or a local ``.env`` file) — nothing in the
codebase hard-codes passwords; see ``.env.example``.

Deployment modes (``MODE``)::

    NORMALIZED          single PostgreSQL, canonical schema
    DENORMALIZED        canonical + derived ``denormalized_post_feed`` reads
    SHARDED             canonical routed through the ShardRouter (Member 4)
    SHARDED_REPLICATED  + replica reads / primary writes (Member 5)
    SHARDED_CACHED      + Redis cache-aside above the repository (Member 6)
    FULL_DISTRIBUTED    everything on: shards + replicas + cache + derived
                        read model, feed, graph, search, media, events
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.enums import DeploymentMode, RoutingStrategy

SHARDED_MODES = frozenset(
    {
        DeploymentMode.SHARDED,
        DeploymentMode.SHARDED_REPLICATED,
        DeploymentMode.SHARDED_CACHED,
        DeploymentMode.FULL_DISTRIBUTED,
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- app -------------------------------------------------------------
    app_name: str = "MetaScale"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ---- deployment mode -------------------------------------------------
    mode: DeploymentMode = DeploymentMode.NORMALIZED

    # ---- canonical PostgreSQL (source of truth) --------------------------
    database_url: str = "postgresql+psycopg://metascale:metascale@postgres:5432/metascale"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 5
    db_connect_timeout: int = 5
    db_statement_timeout_ms: int = 5000

    # ---- sharding (Member 4) ---------------------------------------------
    sharding_enabled: bool = False
    sharding_strategy: RoutingStrategy = RoutingStrategy.CONSISTENT_HASH
    shard_count: int = Field(default=4, ge=1, le=128)
    virtual_nodes_per_shard: int = Field(default=150, ge=1)

    shard_0_url: str | None = None
    shard_1_url: str | None = None
    shard_2_url: str | None = None
    shard_3_url: str | None = None
    # Further shards (used by the 4 -> 5 rebalancing demo) may be supplied as
    # SHARD_4_URL ... and are picked up via the environment.
    shard_4_url: str | None = None
    shard_5_url: str | None = None
    shard_6_url: str | None = None
    shard_7_url: str | None = None

    # ---- replication (Member 5) ------------------------------------------
    # When a replica URL is configured for a shard, read-only traffic may be
    # served from it. Without one, the provider is primary-only (and says so
    # in /api/v1/replication/status instead of pretending).
    replication_enabled: bool = False
    replica_reads_enabled: bool = True
    replica_max_lag_seconds: float = 5.0
    shard_0_replica_url: str | None = None
    shard_1_replica_url: str | None = None
    shard_2_replica_url: str | None = None
    shard_3_replica_url: str | None = None
    shard_4_replica_url: str | None = None

    # ---- hot-shard detection ---------------------------------------------
    hot_shard_load_ratio_threshold: float = 0.40
    hot_shard_min_requests: int = 100
    hot_shard_relative_ratio: float = 1.8

    # ---- cache (Member 6) ------------------------------------------------
    # When false, no Redis connection is attempted at all. When true the
    # platform connects to redis_url; if Redis cannot be reached it fails
    # open (cache bypassed) rather than breaking the PostgreSQL path.
    cache_enabled: bool = False
    redis_url: str = "redis://redis:6379/0"
    cache_default_ttl: int = 60
    cache_hot_key_threshold: int = 5
    cache_connect_timeout: float = 2.0
    cache_fail_open: bool = True

    # ---- events (shared infrastructure) ----------------------------------
    # "memory" (default) | "rabbitmq" (needs RABBITMQ_URL + aio-pika) | "off"
    event_bus: str = "memory"

    # ---- denormalized read model (Member 3) ------------------------------
    # Derived data only; canonical PostgreSQL remains authoritative and the
    # projection can be rebuilt at any time.
    denormalized_enabled: bool = False
    denormalized_read_table: str = "denormalized_post_feed"

    # ---- feed (Member 7) --------------------------------------------------
    # "pull" = follow graph -> candidate posts -> rank (default, always
    # correct); "push" = fan-out-on-write into the derived user_feed table.
    feed_strategy: str = "pull"
    feed_candidate_limit: int = 500

    # ---- media (Member 8) -------------------------------------------------
    # "local" = LocalBlobStore (filesystem, always available);
    # "minio"  = MinioBlobStore (requires the `media` compose profile).
    media_backend: str = "local"
    media_root: str = "./var/media"
    media_max_upload_mb: int = 25

    # ---- graph (Member 9) -------------------------------------------------
    # TAO-inspired adjacency cache: a DERIVED projection rebuilt from the
    # canonical follows table. Never a source of truth.
    # Database change tracking (feeds GET /api/v1/database/changes).
    changes_enabled: bool = True
    change_log_capacity: int = 1_000

    # Accepted alias of ``event_bus`` (the integration contract names it
    # EVENT_BUS_BACKEND); EVENT_BUS wins when both are set.
    event_bus_backend: str | None = None

    # Member 9 / graph is EXCLUDED from this iteration (legacy/member9-graph/).
    # These knobs are kept so the exclusion is visible in configuration too;
    # nothing in the running system reads them.
    graph_projection_enabled: bool = False
    graph_projection_ttl: int = 30

    # ---- search (Member 10) -----------------------------------------------
    # "memory" = in-memory inverted index (default, dev/demo);
    # "opensearch" = OpenSearchProvider (requires the `search` profile).
    search_provider: str = "memory"

    # ---- observability (Member 11) ----------------------------------------
    metrics_enabled: bool = True

    # ---- future services (URLs only; each is optional) --------------------
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672//"
    opensearch_url: str = "http://opensearch:9200"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_secure: bool = False
    minio_bucket: str = "metascale-media"
    prometheus_url: str = "http://prometheus:9090"

    # ---- seeding ---------------------------------------------------------
    seed_size: str = "small"
    seed_random_seed: int = 20260910

    @field_validator("mode", mode="before")
    @classmethod
    def _upper_case_mode(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    @field_validator("sharding_strategy", mode="before")
    @classmethod
    def _lower_strategy(cls, v: Any) -> Any:
        return v.lower() if isinstance(v, str) else v

    @field_validator(
        "event_bus", "media_backend", "search_provider", "feed_strategy", mode="before"
    )
    @classmethod
    def _lower_choice(cls, v: Any) -> Any:
        return v.lower() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _resolve_event_bus_alias(self) -> Settings:
        """EVENT_BUS_BACKEND is an alias of EVENT_BUS (integration contract)."""
        alias = (self.event_bus_backend or "").strip().lower()
        current = (self.event_bus or "").strip().lower()
        if alias and (not current or current == "memory"):
            object.__setattr__(self, "event_bus", alias)
        return self

    # ------------------------------------------------------------ derived
    @property
    def sharding_active(self) -> bool:
        """Sharding is active when explicitly enabled or mode is a shard mode."""
        return self.sharding_enabled or self.mode in SHARDED_MODES

    @property
    def cache_active(self) -> bool:
        return self.cache_enabled or self.mode in (
            DeploymentMode.SHARDED_CACHED,
            DeploymentMode.FULL_DISTRIBUTED,
        )

    @property
    def replication_active(self) -> bool:
        return (
            self.replication_enabled
            or self.mode == DeploymentMode.SHARDED_REPLICATED
            or self.mode == DeploymentMode.FULL_DISTRIBUTED
        )

    @property
    def denormalized_active(self) -> bool:
        return (
            self.denormalized_enabled
            or self.mode == DeploymentMode.DENORMALIZED
            or self.mode == DeploymentMode.FULL_DISTRIBUTED
        )

    def shard_urls(self) -> dict[str, str]:
        """Return ``{shard_id: sqlalchemy_url}`` for all configured shards.

        URLs come from ``SHARD_<n>_URL``; this is the single place that turns
        environment configuration into physical shard topology.
        """
        explicit = {
            0: self.shard_0_url,
            1: self.shard_1_url,
            2: self.shard_2_url,
            3: self.shard_3_url,
            4: self.shard_4_url,
            5: self.shard_5_url,
            6: self.shard_6_url,
            7: self.shard_7_url,
        }
        urls: dict[str, str] = {
            f"shard-{i}": url for i, url in explicit.items() if url and i < self.shard_count
        }
        # Also discover higher SHARD_n_URL ... but only active ones (< count);
        # spare shards (e.g. shard-4 for the 4 -> 5 rebalance demo) are kept
        # out of routing until they are explicitly added.
        for key, val in os.environ.items():
            if key.startswith("SHARD_") and key.endswith("_URL"):
                middle = key[len("SHARD_") : -len("_URL")]
                if middle.isdigit() and int(middle) < self.shard_count:
                    urls.setdefault(f"shard-{int(middle)}", val)
        return dict(sorted(urls.items(), key=lambda kv: int(kv[0].split("-")[1])))

    def replica_urls(self) -> dict[str, str]:
        """Return ``{shard_id: replica_url}`` for shards that have a replica.

        Replicas are optional: an empty mapping means the replication
        provider runs primary-only (reported honestly at
        ``/api/v1/replication/status`` instead of simulating a replica).
        """
        pairs = {
            "shard-0": self.shard_0_replica_url,
            "shard-1": self.shard_1_replica_url,
            "shard-2": self.shard_2_replica_url,
            "shard-3": self.shard_3_replica_url,
            "shard-4": self.shard_4_replica_url,
        }
        discovered = {k: v for k, v in pairs.items() if v}
        for key, val in os.environ.items():
            if key.startswith("SHARD_") and key.endswith("_REPLICA_URL"):
                middle = key[len("SHARD_") : -len("_REPLICA_URL")]
                if middle.isdigit():
                    discovered.setdefault(f"shard-{int(middle)}", val)
        return dict(sorted(discovered.items(), key=lambda kv: int(kv[0].split("-")[1])))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Tests can ``get_settings.cache_clear()``."""
    return Settings()
