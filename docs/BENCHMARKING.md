# Benchmarking — how numbers are produced

**Rule: every number in this project is measured at run time.** There are no
hard-coded results, no "expected" latency tables, and no invented cache hit
rates. Where nothing was measured the output says `n/a` (or
`available: false` with a reason).

## Two measurement paths

| Path | What it does | Command |
| --- | --- | --- |
| In-process comparison | Measures the paths the **running** deployment can execute: cached vs uncached read, targeted vs scatter-gather, normalized joins vs denormalized projection, feed, search | `POST /api/v1/benchmarks/compare` or the `/benchmarks` page |
| Full A–F sweep | Starts one process per architecture (A `NORMALIZED` → F `FULL_DISTRIBUTED`) on real PostgreSQL, runs the same workload against each, writes JSON + CSV | `make benchmark-unified` |

They answer different questions: the first compares **techniques inside one
deployment**, the second compares **architectures**.

## What is measured

* Wall-clock latency per operation with `time.perf_counter()`, collected as raw
  samples; P50/P95/P99 are nearest-rank percentiles of those samples (no
  interpolation, no reservoir guessing).
* Cache counters come from the real provider (`cache_hits`, `cache_misses`,
  `db_queries_avoided`), read as a delta around the measurement.
* `cached_read` is reported in two phases:
  * **cold** — the keys are invalidated first, so every iteration really misses
    (cache check + database read + write-back), and
  * **warm** — the same keys are read again inside their TTL.
* Every run reports its mode, iteration count, timestamp and the dataset it ran
  against, because those are what make a number meaningful.

## Operations

| Operation | Path measured |
| --- | --- |
| `read_post` | API → service → repository → shard router → PostgreSQL |
| `cached_read` | API → service → cache → (miss) repository → PostgreSQL, cold and warm |
| `scatter_gather` | sharded list query fanned out to every shard and merged |
| `feed` | feed generation for a random real user |
| `search` | query the derived search index (documents reported alongside latency) |
| `denormalized_read` | one projection table, no joins |
| `normalized_read` | the same data with joins and count subqueries |

Operations that cannot run in the current deployment return
`{"available": false, "reason": "..."}` and are shown as `n/a` in the UI.

## Reading the results (there is no scoreboard)

The API also returns plain-language interpretation notes, computed from the
measurements (for example the cold→warm ratio and what it costs in staleness).
The A–F catalogue (`GET /api/v1/benchmarks/architectures`) deliberately
contains **no numbers** — only what each architecture adds and what it costs —
because results belong to a measurement, not to a description.

| Architecture | Adds | Costs |
| --- | --- | --- |
| A `NORMALIZED` | single source of truth, no duplication, strong consistency | join-heavy reads, one vertical ceiling |
| B `DENORMALIZED` | feed reads without joins, event-driven projections | write amplification, eventual consistency |
| C `SHARDED` | horizontal scale-out, targeted reads on the shard key | cross-shard scatter-gather, resharding, no cross-shard transactions |
| D `SHARDED_REPLICATED` | read scaling per shard, failover target | replication lag, stale reads |
| E `SHARDED_CACHED` | hot reads never reach PostgreSQL | invalidation correctness, TTL-bounded staleness |
| F `FULL_DISTRIBUTED` | all of the above | operational complexity, several consistency regimes at once |

## Reproducing

```bash
make test                 # makes sure the code paths are healthy first
make dev-full             # or: make demo-full
curl -X POST 'localhost:8000/api/v1/benchmarks/compare?iterations=50' | jq
make benchmark-unified    # full A-F sweep -> benchmarks/results/*.json|csv
```

Published samples live in `benchmarks/results/samples/`; raw runs are
git-ignored. When quoting any number, quote the machine, the dataset size, the
mode and the iteration count with it — a latency without that context is not a
result.
