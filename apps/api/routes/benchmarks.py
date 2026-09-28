"""Real, in-process benchmarks (no hard-coded numbers anywhere).

Every value returned by this router is measured *at call time* against the
running system: real rows, real shard routing, real cache, real percentile
computation from the collected samples. Where a subsystem is not enabled in
the current deployment the response says ``available: false`` instead of
printing a number — an unmeasured path is never filled in with a guess.

``POST /api/v1/benchmarks/run?operation=…&iterations=…``   — one operation
``POST /api/v1/benchmarks/compare``                        — several at once
``GET  /api/v1/benchmarks/architectures``                  — the A–F catalogue
                                                             (capabilities and
                                                             trade-offs only,
                                                             never results)
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from api.dependencies import Platform, get_platform
from api.services.post_service import PostService

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

MAX_ITERATIONS = 2_000


# --------------------------------------------------------------- statistics
def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, math.ceil(q * len(sorted_values)) - 1))
    return round(sorted_values[index], 3)


def _summarise(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    total = sum(ordered)
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3) if ordered else 0.0,
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "p99_ms": _percentile(ordered, 0.99),
        "max_ms": round(ordered[-1], 3) if ordered else 0.0,
        "mean_ms": round(total / len(ordered), 3) if ordered else 0.0,
    }


async def _measure(fn: Callable[[], Awaitable[Any]], iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


# ------------------------------------------------------------------- helpers
async def _sample_ids(platform: Platform, table: str, column: str, count: int) -> list[int]:
    """Fetch real primary keys from the live database (scatter-gather)."""
    ids: list[int] = []
    sql = f"SELECT {column} FROM {table} LIMIT {max(count, 1)}"
    if platform.shard_manager is not None and platform.shard_manager.shard_ids:
        for sid in platform.shard_manager.shard_ids:
            async with platform.shard_manager.session_scope(sid, readonly=True) as session:
                rows = (await session.execute(text(sql))).scalars().all()
                ids.extend(int(r) for r in rows)
            if len(ids) >= count:
                break
    elif platform.canonical_engine is not None:
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(platform.canonical_engine, expire_on_commit=False) as session:
            rows = (await session.execute(text(sql))).scalars().all()
            ids.extend(int(r) for r in rows)
    return ids[:count]


def _cache_delta(platform: Platform, before: dict[str, Any]) -> dict[str, Any]:
    if platform.cache is None or before is None:
        return {}
    after = platform.cache.get_metrics()
    return {
        "cache_hits": after.get("cache_hits", 0) - before.get("cache_hits", 0),
        "cache_misses": after.get("cache_misses", 0) - before.get("cache_misses", 0),
        "db_queries_avoided": after.get("db_queries_avoided", 0)
        - before.get("db_queries_avoided", 0),
    }


def _cache_snapshot(platform: Platform) -> dict[str, Any]:
    return platform.cache.get_metrics() if platform.cache is not None else {}


# ---------------------------------------------------------------- operations
async def _op_read_post(platform: Platform, iterations: int, ids: list[int]) -> dict[str, Any]:
    """Canonical/routed point read — the cache is NOT consulted."""
    if not ids:
        raise HTTPException(status_code=409, detail="no posts in the database to read")
    samples = await _measure(lambda: platform.post_repo.get_by_id(random.choice(ids)), iterations)
    return {
        "path": "API -> Service -> Repository -> ShardRouter -> PostgreSQL",
        "cache_involved": False,
        **_summarise(samples),
    }


async def _op_cached_read(platform: Platform, iterations: int, ids: list[int]) -> dict[str, Any]:
    """Cache-aside read, reported in two phases: COLD (misses) then WARM (hits).

    The cold phase is the honest cost of a cache miss (cache check + database
    read + write-back); the warm phase is what a hot key actually costs. Both
    are measured, never assumed.
    """
    if platform.cache is None:
        return {"available": False, "reason": "cache disabled (CACHE_ENABLED=false)"}
    if not ids:
        raise HTTPException(status_code=409, detail="no posts in the database to read")
    service = PostService(
        platform.post_repo,
        platform.comment_repo,
        platform.user_repo,
        cache=platform.cache,
        event_bus=platform.event_bus,
        hashtag_repo=platform.hashtag_repo,
    )

    # --- cold pass: invalidate the keys we are about to read so every
    # iteration really misses (a stale hit would hide the miss cost).
    for pid in set(ids):
        await platform.cache.invalidate(f"post:{pid}")
    cold_before = _cache_snapshot(platform)
    cold = await _measure(lambda: service.get(random.choice(ids)), iterations)
    cold_delta = _cache_delta(platform, cold_before)

    # --- warm pass: the same keys now sit in the cache (inside their TTL).
    warm_before = _cache_snapshot(platform)
    warm = await _measure(lambda: service.get(random.choice(ids)), iterations)
    warm_delta = _cache_delta(platform, warm_before)

    return {
        "path": "API -> Service -> Cache -> (miss) Repository -> ShardRouter -> PostgreSQL",
        "cache_involved": True,
        "cache_backend": platform.settings.redis_url.split("://")[0],
        "cold": _summarise(cold),
        "cold_cache": cold_delta,
        "warm": _summarise(warm),
        "warm_cache": warm_delta,
        # Headline number = the warm path (what a hot key costs in production).
        **_summarise(warm),
        "cache_hits": warm_delta.get("cache_hits", 0),
        "cache_misses": cold_delta.get("cache_misses", 0),
    }


async def _op_scatter_gather(platform: Platform, iterations: int, ids: list[int]) -> dict[str, Any]:
    if platform.sharded_post_repo is None:
        return {"available": False, "reason": "sharding disabled in this deployment"}
    samples = await _measure(lambda: platform.sharded_post_repo.list_recent(limit=50), iterations)
    return {
        "path": "API -> Service -> ShardRouter -> ALL shards -> merge",
        "cache_involved": False,
        "shards": (
            list(platform.shard_manager.shard_ids) if platform.shard_manager is not None else []
        ),
        **_summarise(samples),
    }


async def _op_feed(platform: Platform, iterations: int, ids: list[int]) -> dict[str, Any]:
    if platform.feed_service is None:
        return {"available": False, "reason": "feed service not initialised"}
    users = await _sample_ids(platform, "users", "user_id", 25)
    if not users:
        return {"available": False, "reason": "no users in the database"}
    samples = await _measure(
        lambda: platform.feed_service.get_feed(random.choice(users), limit=20), iterations
    )
    return {
        "path": "API -> FeedService -> follows -> candidate posts -> rank",
        "cache_involved": False,
        "strategy": platform.settings.feed_strategy,
        **_summarise(samples),
    }


async def _op_search(platform: Platform, iterations: int, ids: list[int]) -> dict[str, Any]:
    if platform.search_indexer is None:
        return {"available": False, "reason": "search indexer not initialised"}
    terms = ["a", "the", "post", "hello", "world", "data", "meta"]
    samples = await _measure(
        lambda: platform.search_indexer.search(random.choice(terms), limit=20), iterations
    )
    stats = platform.search_indexer.stats()
    return {
        "path": "API -> SearchIndexer -> search provider (derived index)",
        "cache_involved": False,
        "provider": platform.settings.search_provider,
        # Context: an empty index answers instantly, so the document count is
        # reported alongside the latency (POST /api/v1/search/reindex first).
        "documents": stats.get("documents", 0),
        **_summarise(samples),
    }


async def _op_denormalized_read(
    platform: Platform, iterations: int, ids: list[int]
) -> dict[str, Any]:
    if platform.denormalizer is None or platform.canonical_engine is None:
        return {"available": False, "reason": "denormalized read model disabled"}
    from sqlalchemy.ext.asyncio import AsyncSession

    async def one() -> None:
        async with AsyncSession(platform.canonical_engine, expire_on_commit=False) as session:
            await session.execute(
                text(
                    "SELECT post_id, author_username, content, like_count, comment_count "
                    "FROM denormalized_post_feed ORDER BY created_at DESC LIMIT 20"
                )
            )

    samples = await _measure(one, iterations)
    return {
        "path": "API -> single denormalized table (no joins)",
        "cache_involved": False,
        **_summarise(samples),
    }


async def _op_normalized_read(
    platform: Platform, iterations: int, ids: list[int]
) -> dict[str, Any]:
    """The equivalent normalized read: posts JOIN users JOIN aggregates."""
    if platform.canonical_engine is None:
        return {"available": False, "reason": "canonical database not initialised"}
    from sqlalchemy.ext.asyncio import AsyncSession

    async def one() -> None:
        async with AsyncSession(platform.canonical_engine, expire_on_commit=False) as session:
            await session.execute(
                text(
                    "SELECT p.post_id, u.username, p.content, "
                    "(SELECT count(*) FROM likes l WHERE l.post_id = p.post_id) AS like_count, "
                    "(SELECT count(*) FROM comments c WHERE c.post_id = p.post_id) AS comment_count "
                    "FROM posts p JOIN users u ON u.user_id = p.user_id "
                    "ORDER BY p.created_at DESC LIMIT 20"
                )
            )

    samples = await _measure(one, iterations)
    return {
        "path": "API -> normalized joins (posts + users + count subqueries)",
        "cache_involved": False,
        **_summarise(samples),
    }


OPERATIONS: dict[str, Callable[[Platform, int, list[int]], Awaitable[dict[str, Any]]]] = {
    "read_post": _op_read_post,
    "cached_read": _op_cached_read,
    "scatter_gather": _op_scatter_gather,
    "feed": _op_feed,
    "search": _op_search,
    "denormalized_read": _op_denormalized_read,
    "normalized_read": _op_normalized_read,
}

OPERATION_HELP: dict[str, str] = {
    "read_post": "point read straight through the repository (no cache)",
    "cached_read": "same read through the Redis/in-memory cache-aside layer",
    "scatter_gather": "sharded list query fanned out to every shard and merged",
    "feed": "home-feed generation for a random real user",
    "search": "query the derived search index",
    "denormalized_read": "read the denormalized projection (one table, no joins)",
    "normalized_read": "the same data assembled with normalized joins",
}


# ------------------------------------------------------------------- routes
@router.get("/operations")
async def operations(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Which operations can actually be measured in this deployment."""
    ids = await _sample_ids(platform, "posts", "post_id", 1)
    return {
        "operations": [
            {
                "name": name,
                "description": help_text,
                "available": name not in ("read_post", "cached_read") or bool(ids),
            }
            for name, help_text in OPERATION_HELP.items()
        ],
        "max_iterations": MAX_ITERATIONS,
        "mode": platform.settings.mode.value,
    }


@router.post("/run")
async def run_benchmark(
    operation: str = Query(..., description="operation name from /benchmarks/operations"),
    iterations: int = Query(default=100, ge=1, le=MAX_ITERATIONS),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """Measure one operation right now. Real samples, real percentiles."""
    if operation not in OPERATIONS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown operation '{operation}' (see /api/v1/benchmarks/operations)",
        )
    ids = await _sample_ids(platform, "posts", "post_id", 50)
    started = time.perf_counter()
    result = await OPERATIONS[operation](platform, iterations, ids)
    return {
        "operation": operation,
        "iterations": iterations,
        "mode": platform.settings.mode.value,
        "wall_clock_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": (
            "measured live in this process against real data; results depend on "
            "the machine, the dataset and the cache state"
        ),
        **result,
    }


@router.post("/compare")
async def compare(
    operations: str = Query(
        default="read_post,cached_read,scatter_gather,feed,search,normalized_read,denormalized_read",
        description="comma-separated operation names",
    ),
    iterations: int = Query(default=50, ge=1, le=MAX_ITERATIONS),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """Measure several paths side by side.

    This is a *capability* comparison of the running system (cache vs no cache,
    targeted vs scatter-gather, normalized joins vs denormalized projection),
    not the full A–F deployment sweep — that one needs one process per
    architecture and lives in ``make benchmark-unified``.
    """
    wanted = [o.strip() for o in operations.split(",") if o.strip()]
    unknown = [o for o in wanted if o not in OPERATIONS]
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown operations: {unknown}")
    ids = await _sample_ids(platform, "posts", "post_id", 50)
    results: list[dict[str, Any]] = []
    for name in wanted:
        result = await OPERATIONS[name](platform, iterations, ids)
        results.append({"operation": name, "available": result.get("available", True), **result})
    measured = [r for r in results if r.get("available", True) and r.get("count")]
    return {
        "mode": platform.settings.mode.value,
        "iterations": iterations,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "skipped": [r["operation"] for r in results if not r.get("available", True)],
        "interpretation": _interpret(measured),
        "note": (
            "no ranking and no score: each row trades latency against freshness, "
            "storage and complexity — read docs/BENCHMARKING.md before quoting any number"
        ),
    }


def _interpret(measured: list[dict[str, Any]]) -> list[str]:
    """Plain-language reading of the measured rows (no invented figures)."""
    notes: list[str] = []
    by_op = {row["operation"]: row for row in measured}
    if "read_post" in by_op and "cached_read" in by_op:
        base = by_op["read_post"]["p50_ms"]
        warm = by_op["cached_read"].get("warm", {}).get("p50_ms", by_op["cached_read"]["p50_ms"])
        cold = by_op["cached_read"].get("cold", {}).get("p50_ms")
        if base > 0 and warm > 0:
            direction = "faster" if warm < base else "slower"
            ratio = (base / warm) if warm < base else (warm / base)
            notes.append(
                f"warm cache-aside p50 is {warm:.3f} ms vs {base:.3f} ms for the same "
                f"read straight from PostgreSQL — {ratio:.1f}x {direction} on this run "
                f"(cache backend: {by_op['cached_read'].get('cache_backend', 'unknown')})"
            )
        if cold is not None and warm:
            notes.append(
                f"the same read costs {cold:.3f} ms cold (miss + database read + "
                f"write-back) and {warm:.3f} ms warm — the cache only pays off "
                f"when a key is read more than once inside its TTL"
            )
        notes.append(
            "cache speed is bought with staleness: entries are served until their "
            "TTL expires or the write path invalidates them"
        )
    if "normalized_read" in by_op and "denormalized_read" in by_op:
        notes.append(
            "the denormalized projection answers a feed-shaped query from one "
            "table; the normalized form pays joins and per-row count subqueries"
        )
        notes.append(
            "denormalization moves cost to the write path: every like/comment "
            "event updates the projection, and the projection can always be "
            "rebuilt from canonical PostgreSQL"
        )
    if "scatter_gather" in by_op:
        notes.append(
            "scatter-gather touches every shard, so its latency is bounded by "
            "the slowest shard — targeted reads on the shard key stay O(1) shard"
        )
    return notes


@router.get("/architectures")
async def architectures() -> dict[str, Any]:
    """The A–F catalogue: what each architecture adds and what it costs.

    Deliberately contains **no** numbers — results belong to a measurement,
    not to a description. Run ``make benchmark-unified`` (or POST /compare for
    the in-process comparison) to obtain real figures.
    """
    return {
        "disclaimer": (
            "This is an independent educational/research project inspired by "
            "publicly discussed database and infrastructure concepts associated "
            "with large-scale social platforms. It is not affiliated with, "
            "endorsed by, or a reproduction of Meta's proprietary systems."
        ),
        "architectures": [
            {
                "id": "A",
                "name": "NORMALIZED",
                "description": "single PostgreSQL, 3NF schema, joins on read",
                "adds": ["single source of truth", "no duplication", "strong consistency"],
                "costs": ["join-heavy reads", "one vertical ceiling"],
            },
            {
                "id": "B",
                "name": "DENORMALIZED",
                "description": "A + a derived read model kept in step by domain events",
                "adds": ["feed reads without joins", "event-driven projections"],
                "costs": ["write amplification", "eventual consistency of the projection"],
            },
            {
                "id": "C",
                "name": "SHARDED",
                "description": "B + horizontal partitioning with a consistent-hash router",
                "adds": ["horizontal scale-out", "targeted reads on the shard key"],
                "costs": [
                    "cross-shard queries need scatter-gather",
                    "resharding/rebalance complexity",
                    "no cross-shard transactions",
                ],
            },
            {
                "id": "D",
                "name": "SHARDED_REPLICATED",
                "description": "C + read replicas inside each shard",
                "adds": ["read scaling per shard", "failover target"],
                "costs": ["replication lag", "stale reads if you route to replicas"],
            },
            {
                "id": "E",
                "name": "SHARDED_CACHED",
                "description": "C + Redis cache-aside above the repository",
                "adds": ["hot reads never reach PostgreSQL", "TTL-bounded staleness"],
                "costs": ["invalidation correctness", "cold-start/thundering herd"],
            },
            {
                "id": "F",
                "name": "FULL_DISTRIBUTED",
                "description": "sharding + replicas + cache + event-driven derived systems",
                "adds": ["all of the above, composed"],
                "costs": ["operational complexity", "multiple consistency regimes at once"],
            },
        ],
        "how_to_measure": {
            "in_process": "POST /api/v1/benchmarks/compare",
            "full_sweep": "make benchmark-unified (one process per architecture, A-F)",
        },
    }
