"""Per-shard SQLAlchemy connection management (Member 4).

Every shard gets its **own independent engine and connection pool** — there is
deliberately no shared global connection pretending to be sharded. Engines are
created lazily on first use.

Failure handling
----------------
* connection refused / timeout / invalid shard -> the shard is marked
  UNAVAILABLE and :class:`ShardUnavailableError` is raised with a structured
  body;
* pool exhaustion (queue timeout) -> ShardUnavailableError(reason=
  "connection pool exhausted");
* the router never invents replicas — that is Member 5's job.

Member 5 seam
-------------
:class:`ShardConnectionProvider` chooses *which endpoint* inside a shard to
talk to. The current :class:`PrimaryOnlyProvider` always returns the single
primary URL; Member 5 later replaces it with primary/replica selection without
touching routing logic (Member 4 chooses the shard, Member 5 the replica).
"""

from __future__ import annotations

import abc
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from common.config import Settings
from common.exceptions import ShardNotFoundError, ShardUnavailableError
from metadata.models import ShardMetadata
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from db.engine import build_connect_args


@dataclass(frozen=True)
class ConnectionEndpoint:
    """A concrete physical endpoint inside a shard."""

    shard_id: str
    url: str
    role: str = "primary"  # Member 5 will add "replica"


class ShardConnectionProvider(abc.ABC):
    """SEAM for Member 5 (replication): primary/replica selection."""

    @abc.abstractmethod
    async def resolve(
        self,
        shard_id: str,
        metadata: ShardMetadata | None,
        *,
        for_write: bool = False,
    ) -> ConnectionEndpoint:
        """Return the endpoint to connect to for the given shard."""


class PrimaryOnlyProvider(ShardConnectionProvider):
    """Current behaviour: every shard is a single primary (no replication)."""

    def __init__(self, urls: dict[str, str]) -> None:
        self._urls = dict(urls)

    def update_url(self, shard_id: str, url: str) -> None:
        self._urls[shard_id] = url

    def drop(self, shard_id: str) -> None:
        self._urls.pop(shard_id, None)

    async def resolve(
        self,
        shard_id: str,
        metadata: ShardMetadata | None,
        *,
        for_write: bool = False,
    ) -> ConnectionEndpoint:
        if shard_id not in self._urls:
            raise ShardNotFoundError(shard_id)
        # Writes always target the primary today. When Member 5 adds replicas,
        # reads (for_write=False) may be load-balanced to them here.
        return ConnectionEndpoint(shard_id=shard_id, url=self._urls[shard_id], role="primary")


class ShardEngineManager:
    """Owns one lazily-created, pooled :class:`AsyncEngine` per shard."""

    def __init__(
        self,
        urls: dict[str, str],
        settings: Settings,
        *,
        provider: ShardConnectionProvider | None = None,
        pool_size: int | None = None,
    ) -> None:
        self._urls = dict(urls)
        self._settings = settings
        self._engines: dict[str, AsyncEngine] = {}
        self._down: set[str] = set()
        self.provider: ShardConnectionProvider = provider or PrimaryOnlyProvider(self._urls)
        self._pool_size = pool_size or max(3, settings.db_pool_size // 2)

    # ------------------------------------------------------------- topology
    @property
    def shard_ids(self) -> list[str]:
        return sorted(self._urls, key=lambda s: int(s.split("-")[1]))

    def has(self, shard_id: str) -> bool:
        return shard_id in self._urls

    async def add_shard(self, shard_id: str, url: str) -> None:
        self._urls[shard_id] = url
        if isinstance(self.provider, PrimaryOnlyProvider):
            self.provider.update_url(shard_id, url)
        self._down.discard(shard_id)

    async def remove_shard(self, shard_id: str) -> None:
        engine = self._engines.pop(shard_id, None)
        if engine is not None:
            await engine.dispose()
        self._urls.pop(shard_id, None)
        self._down.discard(shard_id)
        if isinstance(self.provider, PrimaryOnlyProvider):
            self.provider.drop(shard_id)

    # ---------------------------------------------------------------- health
    def mark_down(self, shard_id: str) -> None:
        self._down.add(shard_id)

    def mark_up(self, shard_id: str) -> None:
        self._down.discard(shard_id)

    def is_down(self, shard_id: str) -> bool:
        return shard_id in self._down

    async def ping(self, shard_id: str) -> float:
        """Run ``SELECT 1`` even if the shard was marked down.

        A successful probe revives the shard (``mark_up``); a failed probe
        keeps/records the down state and raises a structured error.
        """
        if shard_id not in self._urls:
            raise ShardNotFoundError(shard_id)
        start = time.perf_counter()
        try:
            endpoint = await self.provider.resolve(shard_id, None, for_write=False)
            engine = self._engines.get(f"{shard_id}:{endpoint.role}")
            if engine is None:
                engine = create_async_engine(
                    endpoint.url,
                    pool_size=self._pool_size,
                    max_overflow=self._settings.db_max_overflow,
                    pool_timeout=self._settings.db_pool_timeout,
                    pool_pre_ping=True,
                    connect_args=build_connect_args(self._settings),
                )
                self._engines[f"{shard_id}:{endpoint.role}"] = engine
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await session.execute(text("SELECT 1"))
        except ShardUnavailableError:
            self.mark_down(shard_id)
            raise
        except Exception as exc:
            self.mark_down(shard_id)
            raise ShardUnavailableError(
                shard_id, f"health probe failed: {type(exc).__name__}"
            ) from exc
        self.mark_up(shard_id)
        return round((time.perf_counter() - start) * 1000.0, 3)

    # -------------------------------------------------------------- engines
    async def _endpoint(self, shard_id: str, *, for_write: bool) -> ConnectionEndpoint:
        if shard_id not in self._urls:
            raise ShardNotFoundError(shard_id)
        if shard_id in self._down:
            raise ShardUnavailableError(shard_id, "marked unavailable by health monitoring")
        return await self.provider.resolve(shard_id, None, for_write=for_write)

    async def engine_for(self, shard_id: str, *, for_write: bool = False) -> AsyncEngine:
        endpoint = await self._endpoint(shard_id, for_write=for_write)
        # Cached per (shard, role) once replicas exist; today role is primary.
        cache_key = f"{shard_id}:{endpoint.role}"
        engine = self._engines.get(cache_key)
        if engine is None:
            engine = create_async_engine(
                endpoint.url,
                pool_size=self._pool_size,
                max_overflow=self._settings.db_max_overflow,
                pool_timeout=self._settings.db_pool_timeout,
                pool_pre_ping=True,
                connect_args=build_connect_args(self._settings),
            )
            self._engines[cache_key] = engine
        return engine

    @asynccontextmanager
    async def session_scope(
        self, shard_id: str, *, readonly: bool = False
    ) -> AsyncIterator[AsyncSession]:
        try:
            engine = await self.engine_for(shard_id, for_write=not readonly)
        except ShardUnavailableError:
            raise
        session = AsyncSession(engine, expire_on_commit=False, autoflush=False)
        try:
            yield session
            if not readonly:
                await session.commit()
        except SATimeoutError as exc:  # connection pool queue exhausted
            self.mark_down(shard_id)
            raise ShardUnavailableError(
                shard_id, "connection pool exhausted or acquisition timed out"
            ) from exc
        except OperationalError as exc:
            # Database unreachable / connection dropped mid-use.
            self.mark_down(shard_id)
            raise ShardUnavailableError(
                shard_id, f"database operational error: {type(exc.orig).__name__}"
            ) from exc
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    def pool_status(self, shard_id: str) -> dict[str, Any]:
        engine = self._engines.get(f"{shard_id}:primary")
        if engine is None:
            return {"created": False}
        pool = engine.pool
        return {
            "created": True,
            "size": (
                getattr(pool, "size", lambda: 0)()
                if callable(getattr(pool, "size", None))
                else pool.size()
            ),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "status": "UNAVAILABLE" if shard_id in self._down else "READY",
        }

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()
