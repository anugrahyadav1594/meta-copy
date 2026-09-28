"""Benchmark: modulo hash routing — throughput, distribution, 4->5 movement.

No database required: measures the routing layer itself. Demonstrates the
classic modulo problem: growing 4 -> 5 shards relocates ~80% of keys.

Run:  python benchmarks/sharding/benchmark_hash.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "packages", _ROOT / "services" / "shard-router", _ROOT / "apps"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import time

from common.enums import RoutingStrategy
from router.hash_router import HashRouter
from router.shard_router import ShardRouter

from benchmarks.sharding._bench_common import (
    banner,
    distribution,
    latency_summary,
    write_outputs,
)

SAMPLE = 100_000
KEYS = list(range(1, SAMPLE + 1))


def time_routing(shard_ids: list[str], strategy: RoutingStrategy, keys) -> dict:
    from router.consistent_hash import ConsistentHashRing

    start_setup = time.perf_counter()
    if strategy == RoutingStrategy.HASH:
        router = HashRouter(shard_ids)
        route = router.shard_for
    else:
        ring = ConsistentHashRing(shard_ids, virtual_nodes_per_shard=150)
        route = ring.shard_for
    setup_ms = (time.perf_counter() - start_setup) * 1000

    # warm
    for k in keys[:1000]:
        route(k)

    latencies: list[float] = []
    start = time.perf_counter()
    for k in keys:
        t0 = time.perf_counter()
        route(k)
        latencies.append((time.perf_counter() - t0) * 1000)
    elapsed = time.perf_counter() - start
    summary = latency_summary(latencies)
    summary.update(
        {
            "shard_count": len(shard_ids),
            "strategy": strategy.value,
            "setup_ms": round(setup_ms, 3),
            "throughput_rps": round(len(keys) / elapsed, 0),
            "elapsed_ms": round(elapsed * 1000, 2),
        }
    )
    return summary


def movement_fraction(old_ids: list[str], new_ids: list[str], strategy: RoutingStrategy) -> dict:
    sample = KEYS
    before = ShardRouter(old_ids, strategy=strategy, virtual_nodes_per_shard=150)
    after = ShardRouter(new_ids, strategy=strategy, virtual_nodes_per_shard=150)
    moved = sum(
        1
        for k in sample
        if before.shard_for(k, strategy=strategy) != after.shard_for(k, strategy=strategy)
    )
    return {
        "strategy": strategy.value,
        "old_shards": len(old_ids),
        "new_shards": len(new_ids),
        "sample_keys": len(sample),
        "keys_moved": moved,
        "pct_moved": round(100.0 * moved / len(sample), 2),
        "theoretical_min_pct": round(100.0 / len(new_ids), 2),
    }


def main() -> None:
    banner("METASCALE BENCHMARK — STABLE HASH ROUTING")
    four = [f"shard-{i}" for i in range(4)]

    rows = []
    for n in (1, 2, 4, 8):
        ids = [f"shard-{i}" for i in range(n)]
        for strategy in (RoutingStrategy.HASH, RoutingStrategy.CONSISTENT_HASH):
            r = time_routing(ids, strategy, KEYS[:20_000])
            r["virtual_nodes_per_shard"] = 150
            rows.append(r)
            print(
                f"shards={n:<2} strategy={strategy.value:<16} "
                f"rps={r['throughput_rps']:>10,.0f} p95={r['p95_latency_ms']}ms"
            )

    print("\nKey distribution over 100,000 integer keys (4 shards):")
    router = ShardRouter(
        four, strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    dist = distribution(router, KEYS, RoutingStrategy.CONSISTENT_HASH)
    for sid, pct in sorted(dist.items()):
        print(f"  {sid}: {pct:5.2f}%")

    print("\nKey movement when growing 4 -> 5 shards:")
    five = four + ["shard-4"]
    movements = []
    for strategy in (RoutingStrategy.HASH, RoutingStrategy.CONSISTENT_HASH):
        m = movement_fraction(four, five, strategy)
        movements.append(m)
        print(
            f"  {strategy.value:<16} moved {m['keys_moved']:,}/{m['sample_keys']:,} "
            f"= {m['pct_moved']}% (theoretical minimum {m['theoretical_min_pct']}%)"
        )

    payload = {
        "benchmark": "benchmark_hash",
        "sample_size": SAMPLE,
        "routing_rows": rows,
        "distribution_4_shards_consistent_hash": dist,
        "movement_4_to_5": movements,
        "determinism_note": "murmur3 hashing is stable across processes; built-in hash() is never used",
    }
    json_path, csv_path = write_outputs("benchmark_hash", payload, rows)
    print(f"\nwrote {json_path}\nwrote {csv_path}")


if __name__ == "__main__":
    main()
