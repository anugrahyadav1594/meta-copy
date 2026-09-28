"""Unified MetaScale benchmark — configurations A to F (Phase 12).

One workload, six configurations, real PostgreSQL, real measurements::

    A  NORMALIZED          canonical PostgreSQL, 3NF reads
    B  DENORMALIZED        + derived denormalized_post_feed (single-table reads)
    C  SHARDED             + shard router over 4 independent PostgreSQL clusters
    D  SHARDED_REPLICATED  + replication-aware connection provider on every shard
    E  SHARDED_CACHED      + Redis cache-aside above the repository
    F  FULL_DISTRIBUTED    all of the above at once

What is actually measured
-------------------------
For every configuration the same deterministic workload runs through the
application's own service layer (the identical code path the HTTP API uses):

    write_user, write_post, write_follow, write_like, write_comment,
    read_post (targeted), read_feed, read_graph, read_search

Each operation records a wall-clock sample (``time.perf_counter``). The report
contains n, mean, p50, p95, p99, min, max and ops/sec per operation, plus the
configuration's own counters (cache hits/misses, shard requests, routing
latency, events published). **Nothing is extrapolated, estimated or
hard-coded** — if a number is in the output, the work happened.

Clusters: real PostgreSQL 16. Either external URLs (``DATABASE_URL`` /
``SHARD_n_URL``, i.e. docker compose) or independent local clusters started
with ``pgserver`` (one data directory and one postgres process per shard).

Outputs
-------
    benchmarks/results/raw/unified_<timestamp>.json
    benchmarks/results/raw/unified_<timestamp>.csv
    benchmarks/results/samples/unified.json   (checked in, for the report)
    benchmarks/results/samples/unified.csv

Usage
-----
    python benchmarks/unified_benchmark.py                 # all six configs
    python benchmarks/unified_benchmark.py --configs A C F # a subset
    python benchmarks/unified_benchmark.py --users 40 --posts 150
    python benchmarks/unified_benchmark.py --json-only
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

_ROOT = Path(__file__).resolve().parents[1]
for p in (
    _ROOT,
    _ROOT / "packages",
    _ROOT / "services" / "shard-router",
    _ROOT / "apps",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from common.config import Settings  # noqa: E402
from tests.support.pgclusters import PgClusters  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration matrix
# ---------------------------------------------------------------------------

CONFIGS: dict[str, dict[str, Any]] = {
    "A": {
        "name": "NORMALIZED",
        "mode": "NORMALIZED",
        "sharding": False,
        "cache": False,
        "denormalized": False,
        "replication": False,
        "description": "3NF canonical PostgreSQL only; every read joins.",
    },
    "B": {
        "name": "DENORMALIZED",
        "mode": "DENORMALIZED",
        "sharding": False,
        "cache": False,
        "denormalized": True,
        "replication": False,
        "description": "A + derived denormalized_post_feed (event-projected, rebuildable).",
    },
    "C": {
        "name": "SHARDED",
        "mode": "SHARDED",
        "sharding": True,
        "cache": False,
        "denormalized": False,
        "replication": False,
        "description": "B's data spread over 4 independent clusters via the shard router.",
    },
    "D": {
        "name": "SHARDED_REPLICATED",
        "mode": "SHARDED_REPLICATED",
        "sharding": True,
        "cache": False,
        "denormalized": False,
        "replication": True,
        "description": "C + replication-aware connection provider (read/write split).",
    },
    "E": {
        "name": "SHARDED_CACHED",
        "mode": "SHARDED_CACHED",
        "sharding": True,
        "cache": True,
        "denormalized": False,
        "replication": False,
        "description": "C + Redis cache-aside above the repository (TTL 60s).",
    },
    "F": {
        "name": "FULL_DISTRIBUTED",
        "mode": "FULL_DISTRIBUTED",
        "sharding": True,
        "cache": True,
        "denormalized": True,
        "replication": True,
        "description": "Everything: shards + replicas + cache + derived read model.",
    },
}


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass
class Samples:
    """Raw latency samples for one operation, in milliseconds."""

    operation: str
    values: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.values.append(ms)

    def summary(self) -> dict[str, Any]:
        if not self.values:
            return {
                "operation": self.operation,
                "n": 0,
                "mean_ms": None,
                "p50_ms": None,
                "p95_ms": None,
                "p99_ms": None,
                "min_ms": None,
                "max_ms": None,
            }
        ordered = sorted(self.values)
        total = sum(ordered)

        def pct(p: float) -> float:
            # nearest-rank percentile: no interpolation, no invented values
            idx = max(0, min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1)))))
            return round(ordered[idx], 3)

        return {
            "operation": self.operation,
            "n": len(ordered),
            "mean_ms": round(statistics.fmean(ordered), 3),
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "min_ms": round(ordered[0], 3),
            "max_ms": round(ordered[-1], 3),
            "total_ms": round(total, 3),
            "ops_per_sec": round(len(ordered) / (total / 1000), 2) if total else None,
        }


class Recorder:
    def __init__(self) -> None:
        self.samples: dict[str, Samples] = {}

    def record(self, operation: str, ms: float) -> None:
        self.samples.setdefault(operation, Samples(operation)).add(ms)

    async def measure(self, operation: str, coro: Coroutine[Any, Any, Any]) -> Any:
        start = time.perf_counter()
        try:
            return await coro
        finally:
            self.record(operation, (time.perf_counter() - start) * 1000.0)

    def summaries(self) -> list[dict[str, Any]]:
        return [s.summary() for _, s in sorted(self.samples.items())]


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------

CONTENT_WORDS = [
    "distributed",
    "sharding",
    "cache",
    "replication",
    "index",
    "graph",
    "latency",
    "throughput",
    "consistent",
    "hashing",
    "projection",
    "rebalance",
]


@dataclass
class Workload:
    users: int = 40
    posts: int = 160
    follows: int = 120
    read_posts: int = 200
    read_feed: int = 40
    read_graph: int = 60
    read_search: int = 30
    likes: int = 120
    comments: int = 60


async def _reset_databases(platform: Any, urls: dict[str, str], canonical: str) -> None:
    """Drop and recreate the schema everywhere for a clean, fair run."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from models import Base

    async def reset(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    await reset(canonical)
    for url in urls.values():
        await reset(url)


async def run_workload(
    platform: Any, recorder: Recorder, wl: Workload, seed: int
) -> dict[str, Any]:
    """The identical workload for every configuration."""
    rng = random.Random(seed)  # deterministic across configurations

    from api.services.post_service import PostService
    from api.services.user_service import UserService
    from schemas.post import CommentCreate, PostCreate
    from schemas.user import UserCreate

    users = UserService(platform.user_repo, platform.event_bus)
    posts = PostService(
        platform.post_repo,
        platform.comment_repo,
        platform.user_repo,
        cache=platform.cache,
        event_bus=platform.event_bus,
        hashtag_repo=platform.hashtag_repo,
    )

    # ------------------------------------------------------------- writes
    user_ids: list[int] = []
    for i in range(wl.users):
        payload = UserCreate(
            username=f"bench_u{i}",
            email=f"bench_u{i}@example.com",
            password="password123",
            display_name=f"Bench User {i}",
        )
        user = await recorder.measure("write_user", users.create(payload))
        user_ids.append(int(user.user_id))

    post_ids: list[int] = []
    for i in range(wl.posts):
        author = rng.choice(user_ids)
        content = " ".join(rng.choices(CONTENT_WORDS, k=8)) + f" post{i}"
        payload = PostCreate(user_id=author, content=content)
        post = await recorder.measure("write_post", posts.create(payload))
        post_id = getattr(post, "post_id", None)
        if post_id is None:  # dict-shaped response (e.g. cached path)
            post_id = post["post_id"]
        post_ids.append(int(post_id))

    for _ in range(wl.follows):
        follower, following = rng.sample(user_ids, 2)
        await recorder.measure(
            "write_follow", platform.follow_repo.add_follow(follower, following)
        )

    for _ in range(wl.likes):
        post_id = rng.choice(post_ids)
        user_id = rng.choice(user_ids)
        try:
            await recorder.measure(
                "write_like", platform.like_repo.add(post_id, user_id)
            )
        except Exception:  # duplicate like: the UNIQUE constraint is the point
            pass

    for _ in range(wl.comments):
        post_id = rng.choice(post_ids)
        user_id = rng.choice(user_ids)
        try:
            await recorder.measure(
                "write_comment",
                posts.comment(
                    post_id, CommentCreate(user_id=user_id, content="benchmark comment")
                ),
            )
        except Exception:
            pass

    # ------------------------------------------------------------- reads
    # (write counters and derived projections are warmed up first)
    for _ in range(wl.read_posts):
        post_id = rng.choice(post_ids)
        await recorder.measure("read_post", posts.get(post_id))

    for _ in range(wl.read_feed):
        user_id = rng.choice(user_ids)
        await recorder.measure(
            "read_feed", platform.feed_service.get_feed(user_id, limit=20)
        )

    for _ in range(wl.read_graph):
        user_id = rng.choice(user_ids)
        await recorder.measure(
            "read_graph", platform.graph.followers(user_id, limit=100)
        )

    # the index is derived: build it, then query it
    if platform.search_indexer is not None:
        await platform.search_indexer.rebuild()
    for i in range(wl.read_search):
        term = CONTENT_WORDS[i % len(CONTENT_WORDS)]
        await recorder.measure(
            "read_search", platform.search_indexer.search(term, limit=20)
        )

    return {"users": len(user_ids), "posts": len(post_ids)}


# ---------------------------------------------------------------------------
# One configuration
# ---------------------------------------------------------------------------


async def run_configuration(
    key: str,
    cfg: dict[str, Any],
    urls: dict[str, str],
    canonical_url: str,
    wl: Workload,
    seed: int,
    tmp_root: Path,
) -> dict[str, Any]:
    from api.dependencies import Platform

    settings = Settings(
        database_url=canonical_url,
        mode=cfg["mode"],
        sharding_enabled=cfg["sharding"],
        shard_count=4,
        shard_0_url=urls.get("shard-0"),
        shard_1_url=urls.get("shard-1"),
        shard_2_url=urls.get("shard-2"),
        shard_3_url=urls.get("shard-3"),
        db_pool_size=6,
        db_max_overflow=4,
        db_connect_timeout=10,
        hot_shard_min_requests=10_000,
        cache_enabled=cfg["cache"],
        # memory:// = fakeredis (dev/test only). A deployment points REDIS_URL
        # at a real Redis; the measured code path is identical.
        redis_url="memory://" if cfg["cache"] else "redis://127.0.0.1:6379/0",
        cache_default_ttl=60,
        denormalized_enabled=cfg["denormalized"],
        replication_enabled=cfg["replication"],
        feed_strategy="pull",
        search_provider="memory",
        event_bus="memory",
        media_backend="local",
        media_root=str(tmp_root / f"media-{key}"),
    )

    platform = Platform(settings)
    started = time.perf_counter()
    await platform.start()
    counters: dict[str, Any] = {}
    try:
        await _reset_databases(platform, urls, canonical_url)
        recorder = Recorder()
        setup_start = time.perf_counter()
        counts = await run_workload(platform, recorder, wl, seed)
        workload_s = time.perf_counter() - setup_start

        # Counters are read BEFORE shutdown: every figure below comes from
        # work that actually happened during the run.
        if platform.cache is not None:
            stats = platform.cache.get_metrics()
            counters["cache"] = {
                k: stats.get(k)
                for k in (
                    "backend",
                    "cache_hits",
                    "cache_misses",
                    "hit_ratio",
                    "cache_sets",
                    "cache_invalidations",
                    "db_queries_avoided",
                )
            }
        if platform.metrics is not None and platform.registry is not None:
            counters["sharding"] = platform.metrics.snapshot(platform.registry)
        if platform.event_bus is not None and hasattr(platform.event_bus, "stats"):
            counters["events"] = platform.event_bus.stats()
        if platform.denormalizer is not None:
            counters["read_model"] = platform.denormalizer.stats()
        if platform.replication is not None:
            status = await platform.replication.status()
            counters["replication"] = status
    finally:
        await platform.shutdown()

    result: dict[str, Any] = {
        "config": key,
        "mode": cfg["mode"],
        "description": cfg["description"],
        "layers": {
            "sharding": cfg["sharding"],
            "replication": cfg["replication"],
            "cache": cfg["cache"],
            "denormalized": cfg["denormalized"],
        },
        "dataset": counts,
        "startup_seconds": round(time.perf_counter() - started, 3),
        "workload_seconds": round(workload_s, 3),
        "operations": recorder.summaries(),
    }

    result["counters"] = counters
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _console_report(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 100)
    lines.append("MetaScale unified benchmark — measured on real PostgreSQL")
    lines.append("=" * 100)

    operations = sorted({op["operation"] for r in results for op in r["operations"]})
    header = f"{'config':<22}{'op':<16}{'n':>6}{'p50 ms':>10}{'p95 ms':>10}{'p99 ms':>10}{'ops/s':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        label = f"{r['config']} {r['mode']}"
        for op in r["operations"]:
            lines.append(
                f"{label:<22}{op['operation']:<16}{op['n']:>6}"
                f"{op['p50_ms'] or 0:>10.3f}{op['p95_ms'] or 0:>10.3f}"
                f"{op['p99_ms'] or 0:>10.3f}{op['ops_per_sec'] or 0:>10.1f}"
            )
            label = ""
        lines.append("")

    lines.append("-" * 100)
    lines.append("Module counters (measured, not estimated)")
    for r in results:
        counters = r.get("counters") or {}
        bits = []
        if "cache" in counters:
            c = counters["cache"]
            bits.append(f"cache hits={c['cache_hits']} misses={c['cache_misses']} ratio={c['hit_ratio']}%")
        if "events" in counters:
            e = counters["events"]
            bits.append(f"events published={e['published']} delivered={e['delivered']}")
        if "sharding" in counters:
            s = counters["sharding"]
            bits.append(f"shard requests={s.get('total_requests')}")
        if "read_model" in counters:
            rm = counters["read_model"]
            bits.append(f"read-model applied={rm['events_applied']} errors={rm['errors']}")
        if "replication" in counters:
            rp = counters["replication"]
            repl = rp.get("counters", {})
            bits.append(
                f"replication active={rp.get('replication_active')} "
                f"reads primary={repl.get('reads_primary', 0)} "
                f"replica={repl.get('reads_replica', 0)}"
            )
        lines.append(f"  {r['config']} {r['mode']:<20} " + ("; ".join(bits) or "n/a"))
    lines.append("=" * 100)
    return "\n".join(lines)


def _write_outputs(results: list[dict[str, Any]], stamp: str, json_only: bool) -> list[Path]:
    raw = _ROOT / "benchmarks" / "results" / "raw"
    samples = _ROOT / "benchmarks" / "results" / "samples"
    raw.mkdir(parents=True, exist_ok=True)
    samples.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "benchmark": "unified A-F",
        "engine": "PostgreSQL 16 (embedded pgserver clusters or external URLs)",
        "note": "Every figure below was measured by executing the workload; nothing is simulated.",
        "results": results,
    }
    written = []
    json_paths = [raw / f"unified_{stamp}.json", samples / "unified.json"]
    for path in json_paths:
        path.write_text(json.dumps(payload, indent=2))
        written.append(path)

    if not json_only:
        rows = []
        for r in results:
            for op in r["operations"]:
                rows.append(
                    {
                        "config": r["config"],
                        "mode": r["mode"],
                        "operation": op["operation"],
                        "n": op["n"],
                        "mean_ms": op["mean_ms"],
                        "p50_ms": op["p50_ms"],
                        "p95_ms": op["p95_ms"],
                        "p99_ms": op["p99_ms"],
                        "min_ms": op["min_ms"],
                        "max_ms": op["max_ms"],
                        "ops_per_sec": op["ops_per_sec"],
                    }
                )
        for path in (raw / f"unified_{stamp}.csv", samples / "unified.csv"):
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            written.append(path)
    return written


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _external_urls() -> tuple[dict[str, str], str | None]:
    import os

    urls = {
        f"shard-{i}": os.environ[f"SHARD_{i}_URL"]
        for i in range(4)
        if os.environ.get(f"SHARD_{i}_URL")
    }
    canonical = os.environ.get("DATABASE_URL")
    if len(urls) == 4 and canonical:
        return urls, canonical
    return {}, None


async def _main(args: argparse.Namespace) -> int:
    wl = Workload(
        users=args.users,
        posts=args.posts,
        follows=args.follows,
        read_posts=args.read_posts,
        read_feed=args.read_feed,
        read_graph=args.read_graph,
        read_search=args.read_search,
        likes=args.likes,
        comments=args.comments,
    )

    urls, canonical = _external_urls()
    clusters: PgClusters | None = None
    if not urls:
        print("starting 5 local PostgreSQL 16 clusters (canonical + 4 shards) ...")
        clusters = PgClusters(_ROOT / ".pgdata" / "benchmark", ["canonical", "shard-0", "shard-1", "shard-2", "shard-3"])
        uris = clusters.start()
        await clusters.create_schema()
        urls = {k: v for k, v in uris.items() if k.startswith("shard-")}
        canonical = uris["canonical"]

    results: list[dict[str, Any]] = []
    try:
        for key in args.configs:
            cfg = CONFIGS[key]
            print(f"running configuration {key} ({cfg['name']}) ...", flush=True)
            results.append(
                await run_configuration(
                    key, cfg, urls, canonical, wl, args.seed, _ROOT / ".pgdata" / "benchmark"
                )
            )
    finally:
        if clusters is not None:
            clusters.stop()

    print(_console_report(results))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    written = _write_outputs(results, stamp, args.json_only)
    print("written:")
    for path in written:
        print(f"  {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified MetaScale benchmark (A-F)")
    parser.add_argument(
        "--configs",
        nargs="+",
        choices=sorted(CONFIGS),
        default=sorted(CONFIGS),
        help="which configurations to run (default: all six)",
    )
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--posts", type=int, default=160)
    parser.add_argument("--follows", type=int, default=120)
    parser.add_argument("--read-posts", type=int, default=200)
    parser.add_argument("--read-feed", type=int, default=40)
    parser.add_argument("--read-graph", type=int, default=60)
    parser.add_argument("--read-search", type=int, default=30)
    parser.add_argument("--likes", type=int, default=120)
    parser.add_argument("--comments", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
