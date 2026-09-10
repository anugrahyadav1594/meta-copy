# MetaScale sharding benchmarks

Every benchmark performs real work and writes results — numbers are never
hard-coded.

## Benchmarks

| Script | Needs a DB? | What it measures |
|---|---|---|
| `benchmark_hash.py` | no | modulo-router throughput/latency; measured key distribution; **4→5 key movement** (modulo vs consistent hash) |
| `benchmark_consistent_hash.py` | no | virtual-node sweep (1…500) and the resulting distribution spread/stdev; add/remove movement |
| `benchmark_scaling.py` | yes | end-to-end scenarios A–E against real PostgreSQL clusters |

### Scenarios in `benchmark_scaling.py`

- **A** — single PostgreSQL, targeted reads.
- **B** — four independent PostgreSQL shards, targeted reads through the
  ShardRouter (reports routing overhead and per-shard load).
- **C** — four shards, scatter-gather (fan out to all shards + merge/limit).
- **D** — four shards with a hot-user (celebrity) workload; reports the hot
  shard and detector findings.
- **E** — online 4 → 5 rebalancing: rows copied, checksums verified, ownership
  switched, duration recorded.

If `SHARD_0_URL …` are set in the environment the benchmark uses those real
containers; otherwise it starts independent embedded PostgreSQL 16 clusters
via the `pgserver` wheel (labeled in the output) — the same mechanism the test
suite uses, never SQLite.

## Run

```bash
make benchmark                 # all three
python benchmarks/sharding/benchmark_hash.py
BENCH_REQUESTS=3000 BENCH_CONCURRENCY=64 BENCH_SCATTER=80 \
    python benchmarks/sharding/benchmark_scaling.py
```

## Output

- machine-readable: `results/raw/<name>.json` and `.csv`
- human-readable: console tables/banners
- a committed, measured snapshot: `results/samples/` (regenerate rather than
  hand-edit; `results/raw/` is git-ignored)

## Interpreting the numbers

Routing itself is microseconds and dwarfed by database latency. On a
single-host deployment the four shard clusters share CPU, so absolute
throughput of B vs A is not the point — the educational measurements are:

1. the routing/key-movement differences between modulo and consistent hashing;
2. how virtual nodes flatten distribution;
3. targeted (one shard) vs scatter-gather (all shards) latency;
4. how skewed access creates a hot shard despite an even ring;
5. that a capacity expansion migrates only the expected fraction of keys with
   verified checksums.
