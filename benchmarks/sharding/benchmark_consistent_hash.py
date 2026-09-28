"""Benchmark: consistent hashing — virtual-node distribution study.

Measures, for increasing virtual-node counts, the REAL distribution spread
(max%-min%) of 100,000 keys across 4 shards, and the fraction of keys moved
on shard add/remove. Nothing here is hard-coded: every number is measured.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "packages", _ROOT / "services" / "shard-router", _ROOT / "apps"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import statistics

from router.consistent_hash import ConsistentHashRing

from benchmarks.sharding._bench_common import banner, write_outputs

KEYS = list(range(1, 100_001))
FOUR = [f"shard-{i}" for i in range(4)]


def spread(vnodes: int) -> dict:
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=vnodes)
    counts = {sid: 0 for sid in FOUR}
    for k in KEYS:
        counts[ring.shard_for(k)] += 1
    pcts = {sid: round(100.0 * counts[sid] / len(KEYS), 3) for sid in FOUR}
    values = list(pcts.values())
    return {
        "virtual_nodes_per_shard": vnodes,
        "total_ring_points": vnodes * len(FOUR),
        "percentages": pcts,
        "spread_pp": round(max(values) - min(values), 3),
        "stdev_pp": round(statistics.pstdev(values), 4),
    }


def add_remove_movement(vnodes: int) -> dict:
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=vnodes)
    before = {k: ring.shard_for(k) for k in KEYS}
    ring.add_shard("shard-4")
    added_moved = sum(1 for k in KEYS if before[k] != ring.shard_for(k))
    ring.remove_shard("shard-4")
    removed_returned = sum(1 for k in KEYS if ring.shard_for(k) == before[k])
    return {
        "virtual_nodes_per_shard": vnodes,
        "add_shard_pct_moved": round(100.0 * added_moved / len(KEYS), 3),
        "remove_shard_pct_restored": round(100.0 * removed_returned / len(KEYS), 3),
    }


def main() -> None:
    banner("METASCALE BENCHMARK — CONSISTENT HASHING / VIRTUAL NODES")
    rows, movements = [], []
    for vnodes in (1, 5, 25, 50, 100, 150, 500):
        r = spread(vnodes)
        rows.append(r)
        print(
            f"vnodes/shard={vnodes:<4} spread={r['spread_pp']:>6.2f} pp  "
            f"stdev={r['stdev_pp']:>5.3f}  per-shard={r['percentages']}"
        )
        m = add_remove_movement(vnodes)
        movements.append(m)

    print("\nShard add/remove movement:")
    for m in movements:
        print(
            f"vnodes/shard={m['virtual_nodes_per_shard']:<4} "
            f"add moves {m['add_shard_pct_moved']}%  "
            f"(theoretical ~{round(100.0 / 5, 1)}%); "
            f"remove restores {m['remove_shard_pct_restored']}%"
        )

    payload = {
        "benchmark": "benchmark_consistent_hash",
        "sample_size": len(KEYS),
        "fair_share_pct": round(100.0 / len(FOUR), 2),
        "distribution_sweep": rows,
        "movement": movements,
    }
    j, c = write_outputs("benchmark_consistent_hash", payload, rows)
    print(f"\nwrote {j}\nwrote {c}")


if __name__ == "__main__":
    main()
