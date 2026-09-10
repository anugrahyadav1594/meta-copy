"""Central configuration for MetaScale, built on Pydantic Settings.

Credentials only ever come from environment variables (or a local ``.env``
file). Nothing in the codebase hard-codes passwords — see ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.enums import DeploymentMode, RoutingStrategy


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

    # ---- sharding --------------------------------------------------------
    sharding_enabled: bool = False
    sharding_strategy: RoutingStrategy = RoutingStrategy.CONSISTENT_HASH
    shard_count: int = Field(default=4, ge=1, le=128)
    virtual_nodes_per_shard: int = Field(default=150, ge=1)

    shard_0_url: str | None = None
    shard_1_url: str | None = None
    shard_2_url: str | None = None
    shard_3_url: str | None = None
    # Further shards (used by the 4 -> 5 rebalancing simulation) may be
    # supplied as SHARD_4_URL ... and are picked up via the environment.
    shard_4_url: str | None = None
    shard_5_url: str | None = None
    shard_6_url: str | None = None
    shard_7_url: str | None = None

    # ---- hot-shard detection --------------------------------------------
    hot_shard_load_ratio_threshold: float = 0.40
    hot_shard_min_requests: int = 100
    hot_shard_relative_ratio: float = 1.8

    # ---- future services (NOT consumed yet — placeholders) --------------
    redis_url: str = "redis://redis:6379/0"
    cache_default_ttl: int = 60
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672//"
    opensearch_url: str = "http://opensearch:9200"
    minio_endpoint: str = "minio:9000"
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

    @property
    def sharding_active(self) -> bool:
        """Sharding is active when explicitly enabled or mode == SHARDED."""
        return self.sharding_enabled or self.mode == DeploymentMode.SHARDED

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
        import os

        for key, val in os.environ.items():
            if key.startswith("SHARD_") and key.endswith("_URL"):
                middle = key[len("SHARD_") : -len("_URL")]
                if middle.isdigit() and int(middle) < self.shard_count:
                    urls.setdefault(f"shard-{int(middle)}", val)
        return dict(sorted(urls.items(), key=lambda kv: int(kv[0].split("-")[1])))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Tests can ``get_settings.cache_clear()``."""
    return Settings()
