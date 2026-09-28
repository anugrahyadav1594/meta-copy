"""Spin up real, independent PostgreSQL 16 clusters WITHOUT Docker.

Uses the pip-installable ``pgserver`` wheel (bundles official postgres
binaries). Each named cluster is its own data directory + postgres process
listening on its own unix socket — functionally equivalent to independent
containers for sharding tests. When real SHARD URLs (docker compose) are
provided via environment, those are used instead.

This helper lives under tests/support and is also used by the benchmarks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from models import Base
from sqlalchemy.ext.asyncio import create_async_engine


def _to_sqlalchemy_uri(pg_uri: str) -> str:
    """postgresql://postgres:@/postgres?host=/tmp/x -> +psycopg driver form."""
    parsed = urlparse(pg_uri)
    host = parse_qs(parsed.query).get("host", [""])[0]
    db = (parsed.path or "/postgres").lstrip("/") or "postgres"
    query = urlencode({"host": host})
    return f"postgresql+psycopg://postgres@/{db}?{query}"


@dataclass
class Cluster:
    name: str
    uri: str
    server: object


class PgClusters:
    """Manages N independent postgres clusters."""

    def __init__(self, root: Path, names: list[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.names = names
        self.clusters: dict[str, Cluster] = {}

    def start(self) -> dict[str, str]:
        import pgserver

        uris: dict[str, str] = {}
        for name in self.names:
            data_dir = self.root / name
            server = pgserver.get_server(str(data_dir), cleanup_mode="stop")
            pg_uri = server.get_uri()
            uri = _to_sqlalchemy_uri(pg_uri)
            self.clusters[name] = Cluster(name, uri, server)
            uris[name] = uri
        return uris

    async def create_schema(self, names: list[str] | None = None) -> None:
        for name in names or list(self.clusters):
            engine = create_async_engine(self.clusters[name].uri)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
            finally:
                await engine.dispose()

    async def reset_schema(self, names: list[str] | None = None) -> None:
        for name in names or list(self.clusters):
            engine = create_async_engine(self.clusters[name].uri)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.drop_all)
                    await conn.run_sync(Base.metadata.create_all)
            finally:
                await engine.dispose()

    def uris(self) -> dict[str, str]:
        return {name: c.uri for name, c in self.clusters.items()}

    def stop(self) -> None:
        for cluster in self.clusters.values():
            cleanup = getattr(cluster.server, "cleanup", None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:  # pragma: no cover
                    pass


def env_shard_urls() -> dict[str, str]:
    """Use externally provided shard URLs (e.g. docker compose) if present."""
    urls: dict[str, str] = {}
    for key, val in os.environ.items():
        if key.startswith("SHARD_") and key.endswith("_URL"):
            idx = key[len("SHARD_") : -len("_URL")]
            if idx.isdigit():
                urls[f"shard-{int(idx)}"] = val
    return dict(sorted(urls.items(), key=lambda kv: int(kv[0].split("-")[1])))


def env_canonical_url() -> str | None:
    return os.environ.get("DATABASE_URL")
