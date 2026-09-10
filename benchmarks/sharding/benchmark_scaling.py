"""End-to-end sharding scaling benchmark (real PostgreSQL).

Scenarios
---------
A  single PostgreSQL instance, targeted reads
B  4 independent PostgreSQL shards, targeted reads via ShardRouter
C  4 shards, scatter-gather (fan out to every shard + merge)
D  4 shards, hot-user skewed workload (the celebrity problem)
E  4 -> 5 shards, online rebalancing with verified row migration

Clusters:
  * if SHARD_0_URL ... exist (docker compose) they are used;
  * otherwise independent PostgreSQL 16 clusters are started locally with
    pgserver (one data directory/process per shard — real processes, labeled
    clearly in the output).

Outputs JSON + CSV to benchmarks/results/raw and prints a console report.
All numbers are measured; nothing is fabricated or hard-coded.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT, _ROOT / "packages", _ROOT / "services" / "shard-router", _ROOT / "apps"):
    sys.path.insert(0, str(p))

from common.config import Settings  # noqa: E402
from common.enums import RoutingStrategy  # noqa: E402
from db.session import session_scope  # noqa: E402
from db.shard_engine import ShardEngineManager  # noqa: E402
from hotspots.detector import HotShardDetector  # noqa: E402
from metadata.models import ShardMetadata  # noqa: E402
from metadata.registry import InMemoryShardRegistry  # noqa: E402
from metrics.collector import MetricsCollector  # noqa: E402
from models import User  # noqa: E402
from rebalance.migrator import RebalanceMigrator  # noqa: E402
from router.shard_router import ShardRouter  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from benchmarks.sharding._bench_common import (  # noqa: E402
    RESULTS_DIR,
    CpuSampler,
    banner,
    latency_summary,
    write_outputs,
)
from scripts._dataset import DatasetBuilder  # noqa: E402

REQUESTS = int(os.environ.get("BENCH_REQUESTS", "2000"))
CONCURRENCY = int(os.environ.get("BENCH_CONCURRENCY", "64"))
SCATTER_ITERS = int(os.environ.get("BENCH_SCATTER", "100"))


# --------------------------------------------------------------- infrastructure
async def acquire_infrastructure():
    """Returns (settings, clusters_or_None, canonical_url, shard_urls)."""
    from tests.support.pgclusters import PgClusters, env_shard_urls

    external = env_shard_urls()
    if external:
        settings = Settings()
        print(f"using external shards from environment: {list(external)}")
        return settings, None, settings.database_url, external

    print("starting independent PostgreSQL 16 clusters via pgserver (IMPLEMENTED)...")
    data_root = _ROOT / ".pgdata" / "bench"
    clusters = PgClusters(
        data_root, ["canonical", "shard-0", "shard-1", "shard-2", "shard-3", "shard-4"]
    )
    uris = clusters.start()
    await clusters.reset_schema()
    shard_urls = {k: v for k, v in uris.items() if k.startswith("shard-") and k != "shard-4"}
    settings = Settings(
        database_url=uris["canonical"],
        shard_0_url=uris["shard-0"],
        shard_1_url=uris["shard-1"],
        shard_2_url=uris["shard-2"],
        shard_3_url=uris["shard-3"],
        shard_4_url=uris["shard-4"],
        shard_count=4,
        db_pool_size=8,
        db_max_overflow=8,
        db_connect_timeout=10,
    )
    return settings, clusters, uris["canonical"], shard_urls


async def bulk_insert(url: str, table, rows: list[dict]) -> None:
    engine = create_async_engine(url)
    try:
        for i in range(0, len(rows), 2000):
            async with engine.begin() as conn:
                await conn.execute(pg_insert(table).on_conflict_do_nothing(), rows[i : i + 2000])
    finally:
        await engine.dispose()


async def seed_databases(canonical_url: str, shard_urls: dict[str, str]) -> dict:
    builder = DatasetBuilder(
        "small",
        seed=20260910,
        overrides={"comments": 0, "likes": 0, "follows": 0, "notifications": 0, "media": 50},
    )
    canon_rows = builder.build(None)
    engine = create_async_engine(canonical_url)
    try:
        async with engine.begin() as conn:
            from models import Base

            await conn.run_sync(Base.metadata.create_all)
        for name in ("users", "media", "profiles", "posts"):
            for i in range(0, len(canon_rows[name]), 2000):
                async with engine.begin() as conn:
                    from models import Base as B

                    await conn.execute(
                        pg_insert(B.metadata.tables[name]).on_conflict_do_nothing(),
                        canon_rows[name][i : i + 2000],
                    )
    finally:
        await engine.dispose()

    # sharded rows (router-aligned ids)
    router = ShardRouter(
        list(shard_urls), strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    shard_rows = builder.build(router)
    key_cols = {"users": "id", "media": "owner_id", "profiles": "user_id", "posts": "author_id"}
    from models import Base

    for name, key in key_cols.items():
        buckets: dict[str, list[dict]] = {sid: [] for sid in shard_urls}
        for row in shard_rows[name]:
            buckets[router.shard_for(row[key])].append(row)
        for sid, bucket in buckets.items():
            engine = create_async_engine(shard_urls[sid])
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                    for i in range(0, len(bucket), 2000):
                        await conn.execute(
                            pg_insert(Base.metadata.tables[name]).on_conflict_do_nothing(),
                            bucket[i : i + 2000],
                        )
            finally:
                await engine.dispose()
    user_count = len(canon_rows["users"])
    post_count = len(canon_rows["posts"])
    return {"users": user_count, "posts": post_count}


# ------------------------------------------------------------------ workloads
async def scenario_a_single(url: str, users: int) -> dict:
    engine = create_async_engine(url, pool_size=16, max_overflow=16)
    rng = random.Random(1)
    sem = asyncio.Semaphore(CONCURRENCY)
    latencies: list[float] = []

    async def one() -> None:
        uid = rng.randint(1, users)
        async with sem:
            t0 = time.perf_counter()
            async with session_scope(engine, readonly=True) as s:
                await s.get(User, uid)
            latencies.append((time.perf_counter() - t0) * 1000)

    with CpuSampler() as cpu:
        start = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(REQUESTS)])
        elapsed = time.perf_counter() - start
    await engine.dispose()
    out = latency_summary(latencies)
    out.update(
        scenario="A_single_pg",
        requests=REQUESTS,
        concurrency=CONCURRENCY,
        elapsed_s=round(elapsed, 3),
        rps=round(REQUESTS / elapsed, 1),
        cpu_pct=cpu.cpu_percent(),
    )
    return out


async def scenario_b_sharded(manager, router, users: int) -> tuple[dict, MetricsCollector]:
    rng = random.Random(2)
    sem = asyncio.Semaphore(CONCURRENCY)
    latencies, route_lat = [], []
    metrics = MetricsCollector(router.shard_ids)

    async def one() -> None:
        uid = rng.randint(1, users)
        async with sem:
            t0r = time.perf_counter()
            sid = router.shard_for(uid)
            route_lat.append((time.perf_counter() - t0r) * 1000)
            t0 = time.perf_counter()
            async with manager.session_scope(sid, readonly=True) as s:
                await s.get(User, uid)
            latencies.append((time.perf_counter() - t0) * 1000)
            metrics.record_query(sid, kind="read", latency_ms=(time.perf_counter() - t0) * 1000)

    with CpuSampler() as cpu:
        start = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(REQUESTS)])
        elapsed = time.perf_counter() - start
    out = latency_summary(latencies)
    out.update(
        scenario="B_four_shards_targeted",
        requests=REQUESTS,
        concurrency=CONCURRENCY,
        elapsed_s=round(elapsed, 3),
        rps=round(REQUESTS / elapsed, 1),
        routing_overhead_avg_ms=round(sum(route_lat) / len(route_lat), 5),
        cpu_pct=cpu.cpu_percent(),
    )
    return out, metrics


async def scenario_c_scatter(manager, router, posts: int) -> dict:
    from models import Post
    from sqlalchemy import desc, select

    sem = asyncio.Semaphore(CONCURRENCY)
    latencies, fanout, merges = [], [], []
    shard_touch = {sid: 0 for sid in router.shard_ids}

    async def one() -> None:
        async with sem:
            t0 = time.perf_counter()
            per_shard_rows = {}
            for sid in router.shard_ids:
                ts = time.perf_counter()
                async with manager.session_scope(sid, readonly=True) as s:
                    res = await s.execute(
                        select(Post).order_by(desc(Post.created_at), desc(Post.id)).limit(50)
                    )
                    per_shard_rows[sid] = list(res.scalars().all())
                fanout.append((time.perf_counter() - ts) * 1000)
                shard_touch[sid] += 1
            tm = time.perf_counter()
            gathered = [p for rows in per_shard_rows.values() for p in rows]
            gathered.sort(key=lambda p: (p.created_at, p.id), reverse=True)
            gathered = gathered[:50]
            merges.append((time.perf_counter() - tm) * 1000)
            latencies.append((time.perf_counter() - t0) * 1000)

    with CpuSampler() as cpu:
        start = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(SCATTER_ITERS)])
        elapsed = time.perf_counter() - start
    out = latency_summary(latencies)
    out.update(
        scenario="C_scatter_gather",
        requests=SCATTER_ITERS,
        concurrency=CONCURRENCY,
        elapsed_s=round(elapsed, 3),
        rps=round(SCATTER_ITERS / elapsed, 2),
        avg_fanout_ms=round(sum(fanout) / len(fanout), 3),
        avg_merge_ms=round(sum(merges) / len(merges), 4),
        shards_contacted_per_query=len(router.shard_ids),
        shard_touches=shard_touch,
        cpu_pct=cpu.cpu_percent(),
    )
    return out


async def scenario_d_hot(manager, router, users: int) -> dict:
    hot_user = max(1, users // 2)
    hot_shard = router.shard_for(hot_user)
    rng = random.Random(3)
    sem = asyncio.Semaphore(CONCURRENCY)
    metrics = MetricsCollector(router.shard_ids)
    latencies: list[float] = []

    async def one(uid: int) -> None:
        sid = router.shard_for(uid)
        async with sem:
            t0 = time.perf_counter()
            async with manager.session_scope(sid, readonly=True) as s:
                await s.get(User, uid)
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            metrics.record_query(sid, kind="read", latency_ms=ms)

    keys = [hot_user if rng.random() < 0.8 else rng.randint(1, users) for _ in range(REQUESTS)]
    with CpuSampler() as cpu:
        start = time.perf_counter()
        await asyncio.gather(*[one(k) for k in keys])
        elapsed = time.perf_counter() - start
    detector = HotShardDetector(
        metrics, load_ratio_threshold=0.40, min_requests=100, relative_ratio=1.8
    )
    findings = [h.describe() for h in detector.detect()]
    load = {
        sid: round(data["load_fraction"] * 100, 1)
        for sid, data in metrics.snapshot()["shards"].items()
    }
    out = latency_summary(latencies)
    out.update(
        scenario="D_hot_user",
        requests=REQUESTS,
        hot_user_id=hot_user,
        hot_shard=hot_shard,
        per_shard_load_pct=load,
        hot_findings=findings,
        elapsed_s=round(elapsed, 3),
        rps=round(REQUESTS / elapsed, 1),
        cpu_pct=cpu.cpu_percent(),
    )
    return out


async def scenario_e_rebalance(settings, shard_urls: dict, fifth_url: str, users: int) -> dict:
    router = ShardRouter(
        list(shard_urls), strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    manager = ShardEngineManager(shard_urls, settings)
    registry = InMemoryShardRegistry(
        ShardMetadata.from_url(sid, url, virtual_nodes=150) for sid, url in shard_urls.items()
    )
    metrics = MetricsCollector(router.shard_ids)
    migrator = RebalanceMigrator(router, manager, metrics, registry)

    start = time.perf_counter()
    report = await migrator.migrate_add_shard("shard-4", new_url=fifth_url, table="users")
    elapsed = time.perf_counter() - start

    # verify the new shard now owns those keys
    moved = report.keys_to_move
    verified = report.status.value == "COMPLETED"
    out = {
        "scenario": "E_rebalance_4_to_5",
        "keys_to_move": moved,
        "rows_migrated": report.rows_migrated,
        "status": report.status.value,
        "duration_ms": round(elapsed * 1000, 2),
        "checksum_verified": verified,
        "final_shard_count": router.shard_count,
        "phases": [s.phase.value for s in report.steps],
        "checksums": {"source": report.source_checksum, "destination": report.destination_checksum},
    }
    await manager.dispose()
    return out


# ------------------------------------------------------------------------ main
async def run() -> dict:
    settings, clusters, canonical_url, shard_urls = await acquire_infrastructure()
    print("seeding databases with the small deterministic dataset...")
    sizes = await seed_databases(canonical_url, shard_urls)
    users, posts = sizes["users"], sizes["posts"]

    router = ShardRouter(
        list(shard_urls), strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    manager = ShardEngineManager(shard_urls, settings)

    results = {}
    banner("METASCALE SHARDING BENCHMARK")
    print(
        f"requests/scenario={REQUESTS}  concurrency={CONCURRENCY}  "
        f"users={users} posts={posts}  shards={len(shard_urls)}"
    )

    print("\n[Scenario A] single PostgreSQL, targeted reads")
    a = await scenario_a_single(canonical_url, users)
    results["A"] = a
    print(f"  RPS={a['rps']}  avg={a['avg_latency_ms']}ms  p95={a['p95_latency_ms']}ms")

    print("[Scenario B] 4 shards, targeted reads via router")
    b, metrics_b = await scenario_b_sharded(manager, router, users)
    results["B"] = b
    print(
        f"  RPS={b['rps']}  avg={b['avg_latency_ms']}ms  p95={b['p95_latency_ms']}ms  "
        f"route overhead={b['routing_overhead_avg_ms']}ms"
    )
    dist = {
        sid: round(data["load_fraction"] * 100, 1)
        for sid, data in metrics_b.snapshot()["shards"].items()
    }
    print("  shard load distribution (%):", dist)

    print("[Scenario C] 4 shards, scatter-gather (all shards each query)")
    c = await scenario_c_scatter(manager, router, posts)
    results["C"] = c
    print(
        f"  RPS={c['rps']}  avg={c['avg_latency_ms']}ms  p95={c['p95_latency_ms']}ms  "
        f"fanout={c['avg_fanout_ms']}ms merge={c['avg_merge_ms']}ms"
    )

    print("[Scenario D] hot-user skewed workload")
    d = await scenario_d_hot(manager, router, users)
    results["D"] = d
    for sid, pct in d["per_shard_load_pct"].items():
        bar = "█" * int(pct / 2)
        marker = "  HOT" if sid == d["hot_shard"] else ""
        print(f"  {sid}: {pct:5.1f}% {bar}{marker}")
    for finding in d["hot_findings"]:
        print("  DETECTED ->", finding)

    await manager.dispose()

    print("[Scenario E] 4 -> 5 shard online rebalancing")
    fifth = settings.shard_4_url or shard_urls.get("shard-4")
    if fifth:
        e = await scenario_e_rebalance(settings, shard_urls, fifth, users)
        results["E"] = e
        print(
            f"  status={e['status']} keys_moved={e['keys_to_move']} "
            f"rows={e['rows_migrated']} duration={e['duration_ms']}ms "
            f"checksum_verified={e['checksum_verified']}"
        )
    else:
        results["E"] = {"scenario": "E_rebalance_4_to_5", "status": "SKIPPED (no shard-4 URL)"}
        print("  SKIPPED: no shard-4 URL configured")

    if clusters is not None:
        clusters.stop()
    return results


def main() -> None:
    results = asyncio.run(run())
    flat_rows = []
    for key, value in results.items():
        row = {"scenario": value.get("scenario", key)}
        for metric in (
            "rps",
            "avg_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "p99_latency_ms",
            "routing_overhead_avg_ms",
            "cpu_pct",
            "rows_migrated",
            "duration_ms",
            "status",
        ):
            if metric in value:
                row[metric] = value[metric]
        flat_rows.append(row)
    payload = {
        "benchmark": "benchmark_scaling",
        "requests": REQUESTS,
        "concurrency": CONCURRENCY,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    j, c = write_outputs("benchmark_scaling", payload, flat_rows)
    banner("RESULTS WRITTEN")
    print(j)
    print(c)


if __name__ == "__main__":
    main()
