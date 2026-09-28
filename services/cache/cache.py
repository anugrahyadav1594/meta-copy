"""Redis cache-aside provider (Member 6).

Architectural position (must not be violated)::

    API/Service -> Repository -> Shard Router (Member 4) -> PostgreSQL
                        ▲
                        │ cache MISS only
                     Redis cache (this module)

The cache sits ABOVE the repository/shard router; the shard router never knows
whether a cache exists. PostgreSQL remains the source of truth — Redis only
holds rebuildable, TTL-bound JSON projections.

This implementation is fully **async** (`redis.asyncio`, so it never blocks
the event loop), JSON-serializes values (datetimes as ISO-8601), records
hit/miss/error/hot-key metrics, and **fails open**: if Redis is unreachable
the provider degrades to transparent passthrough instead of breaking the API.

Backends
--------
* a real Redis URL, e.g. ``redis://redis:6379/0``
* ``memory://`` -> an in-process ``fakeredis`` emulator, used for local
  no-Docker development and tests (clearly a dev/test stand-in, never used in
  a real deployment).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date, datetime
from typing import Any

from common.logging import get_logger

logger = get_logger("cache")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, bytes | bytearray):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, separators=(",", ":"), ensure_ascii=False)


def loads(raw: str | bytes | None) -> Any | None:
    if raw is None:
        return None
    return json.loads(raw)


class CacheProvider:
    """Async Redis cache-aside provider with metrics and fail-open behavior."""

    def __init__(
        self,
        client: Any,
        *,
        default_ttl: int = 60,
        hot_key_threshold: int = 5,
        backend: str = "redis",
    ) -> None:
        self.client = client
        self.default_ttl = default_ttl
        self.hot_key_threshold = hot_key_threshold
        self.backend = backend
        self.available = True

        # metrics
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_errors = 0
        self.cache_sets = 0
        self.cache_invalidations = 0
        self.db_queries_avoided = 0
        self._latency_total = 0.0
        self._latency_samples = 0
        self._key_access: Counter[str] = Counter()
        self._hot_keys: Counter[str] = Counter()

    # ------------------------------------------------------------- factory
    @classmethod
    async def create(
        cls,
        redis_url: str,
        *,
        default_ttl: int = 60,
        hot_key_threshold: int = 5,
        connect_timeout: float = 2.0,
        fail_open: bool = True,
    ) -> CacheProvider | None:
        """Connect + ping Redis.

        Returns ``None`` when Redis is unreachable and ``fail_open`` is set,
        so callers transparently fall back to the database. Set
        ``fail_open=False`` to surface the connection error instead.
        """
        try:
            if redis_url.startswith("memory://"):
                try:
                    import fakeredis.aioredis as fake
                except ImportError as exc:  # pragma: no cover - dev extra only
                    raise RuntimeError(
                        "memory:// cache requires the 'fakeredis' dev dependency"
                    ) from exc
                client = fake.FakeRedis(decode_responses=True)
                backend = "memory"
            else:
                import redis.asyncio as aioredis

                client = aioredis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=connect_timeout,
                    socket_timeout=connect_timeout,
                    health_check_interval=15,
                )
                backend = "redis"
            await client.ping()
            provider = cls(
                client,
                default_ttl=default_ttl,
                hot_key_threshold=hot_key_threshold,
                backend=backend,
            )
            logger.info("cache_connected", backend=backend, default_ttl=default_ttl)
            return provider
        except Exception as exc:  # noqa: BLE001 — startup must stay resilient
            if not fail_open:
                raise
            logger.warning(
                "cache_unavailable_fail_open",
                backend=redis_url.split("@")[-1],  # never log credentials
                error=type(exc).__name__,
            )
            return None

    # ------------------------------------------------------------- basic I/O
    async def get(self, key: str) -> Any | None:
        start = time.perf_counter()
        try:
            raw = await self.client.get(key)
            self._record_latency(start)
        except Exception as exc:  # noqa: BLE001 — fail open on every Redis error
            self.cache_errors += 1
            logger.warning("cache_get_error", key=key, error=type(exc).__name__)
            return None

        self._key_access[key] += 1
        if self._key_access[key] == self.hot_key_threshold:
            self._hot_keys[key] += 1
            logger.info("cache_hot_key", key=key, accesses=self._key_access[key])

        if raw is not None:
            self.cache_hits += 1
            self.db_queries_avoided += 1
            logger.debug("cache_hit", key=key)
            return loads(raw)

        self.cache_misses += 1
        logger.debug("cache_miss", key=key)
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        ttl = self.default_ttl if ttl is None else ttl
        try:
            await self.client.set(key, dumps(value), ex=ttl)
            self.cache_sets += 1
            return True
        except Exception as exc:  # noqa: BLE001
            self.cache_errors += 1
            logger.warning("cache_set_error", key=key, error=type(exc).__name__)
            return False

    async def delete(self, key: str) -> None:
        """Invalidate one key (also used for pattern-less single-key evict)."""
        try:
            await self.client.delete(key)
            self.cache_invalidations += 1
            self._key_access.pop(key, None)
            logger.info("cache_invalidated", key=key)
        except Exception as exc:  # noqa: BLE001
            self.cache_errors += 1
            logger.warning("cache_delete_error", key=key, error=type(exc).__name__)

    # alias matching the future CacheProvider seam name
    invalidate = delete

    async def keys(self, pattern: str = "*") -> list[str]:
        try:
            return [str(k) for k in await self.client.keys(pattern)]
        except Exception as exc:  # noqa: BLE001
            self.cache_errors += 1
            logger.warning("cache_keys_error", error=type(exc).__name__)
            return []

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:  # noqa: BLE001
            self.available = False
            return False

    async def aclose(self) -> None:
        close = getattr(self.client, "aclose", None) or getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    # -------------------------------------------------------------- metrics
    def _record_latency(self, start: float) -> None:
        self._latency_total += (time.perf_counter() - start) * 1000.0
        self._latency_samples += 1

    def get_metrics(self) -> dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "backend": self.backend,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_errors": self.cache_errors,
            "cache_sets": self.cache_sets,
            "cache_invalidations": self.cache_invalidations,
            "hit_ratio": round(100.0 * self.cache_hits / total, 2) if total else 0.0,
            "miss_ratio": round(100.0 * self.cache_misses / total, 2) if total else 0.0,
            "db_queries_avoided": self.db_queries_avoided,
            "average_cache_latency_ms": (
                round(self._latency_total / self._latency_samples, 3)
                if self._latency_samples
                else 0.0
            ),
            "hot_keys": [
                {"key": k, "accesses": self._key_access[k]}
                for k, _ in self._hot_keys.most_common(10)
            ],
        }

    def reset_metrics(self) -> None:
        self.cache_hits = self.cache_misses = self.cache_errors = 0
        self.cache_sets = self.cache_invalidations = self.db_queries_avoided = 0
        self._latency_total = self._latency_samples = 0.0
        self._key_access.clear()
        self._hot_keys.clear()
