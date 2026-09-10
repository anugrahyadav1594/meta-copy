"""Shared pytest configuration / fixtures.

Every integration/contract test runs against REAL independent PostgreSQL 16
clusters (one canonical + five shard candidates) started via pgserver, or
against external SHARD_n_URL / DATABASE_URL when those are set in the
environment (docker compose CI path).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[1]
for p in (_ROOT, _ROOT / "packages", _ROOT / "services" / "shard-router", _ROOT / "apps"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from common.config import Settings  # noqa: E402

from tests.support.pgclusters import PgClusters, env_canonical_url, env_shard_urls  # noqa: E402


def _external_or_embedded() -> tuple[dict[str, str], str | None, PgClusters | None]:
    shard_urls = env_shard_urls()
    canonical = env_canonical_url()
    if shard_urls and canonical:
        return shard_urls, canonical, None
    return {}, None, None


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def infra():
    """Start clusters (unless external URLs exist). Returns (settings, clusters)."""
    external_shards = env_shard_urls()
    external_canonical = env_canonical_url()
    clusters: PgClusters | None = None

    if external_shards and external_canonical:
        urls = external_shards
        canonical_url = external_canonical
    else:
        data_root = _ROOT / ".pgdata" / "test"
        names = ["canonical", "shard-0", "shard-1", "shard-2", "shard-3", "shard-4"]
        clusters = PgClusters(data_root, names)
        uris = clusters.start()
        await clusters.create_schema()
        urls = {k: v for k, v in uris.items() if k.startswith("shard-")}
        canonical_url = uris["canonical"]

    settings = Settings(
        database_url=canonical_url,
        mode="SHARDED",
        sharding_enabled=True,
        shard_count=4,
        shard_0_url=urls.get("shard-0"),
        shard_1_url=urls.get("shard-1"),
        shard_2_url=urls.get("shard-2"),
        shard_3_url=urls.get("shard-3"),
        shard_4_url=urls.get("shard-4"),
        db_pool_size=5,
        db_max_overflow=5,
        db_connect_timeout=10,
        hot_shard_min_requests=50,
    )
    yield settings, clusters, urls

    if clusters is not None:
        clusters.stop()


@pytest_asyncio.fixture
async def clean(infra):
    """Reset schema on canonical + four shards before each test."""
    settings, clusters, urls = infra
    from models import Base
    from sqlalchemy.ext.asyncio import create_async_engine

    async def reset(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    await reset(settings.database_url)
    for sid in ("shard-0", "shard-1", "shard-2", "shard-3"):
        await reset(urls[sid])
    if "shard-4" in urls:
        await reset(urls["shard-4"])
    yield
