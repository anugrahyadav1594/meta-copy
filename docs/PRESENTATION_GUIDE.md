# Presentation Guide — one section per member

Use this to present the integrated system. For every member: the topic, the
problem, the concept, what we actually implemented, the diagram, the demo, the
metrics, the trade-offs, what to say and **what not to claim**.

Global disclaimer to state once, up front:

> This is an independent educational/research project inspired by publicly
> discussed database and infrastructure concepts associated with large-scale
> social platforms. It is not affiliated with, endorsed by, or a reproduction
> of Meta's proprietary systems.

---

## Member 1 — Canonical normalized schema

* **Problem.** Social data (users, posts, comments, likes, follows, media) is
  highly relational; duplicating it immediately creates update anomalies.
* **Concept.** Third normal form: one fact in one place, foreign keys, indexes
  on every access path.
* **Implementation.** `packages/models/*` — `users`, `profiles`, `posts`,
  `comments`, `likes`, `follows`, `media`, `notifications`, `hashtags`,
  `post_hashtags`. Hybrid identifiers: natural names (`user_id`, `post_id`, …)
  with 64-bit application-generated values so ids are shardable.
* **Diagram.**

  ```mermaid
  erDiagram
    users ||--o| profiles : has
    users ||--o{ posts : authors
    posts ||--o{ comments : has
    posts ||--o{ likes : receives
    users ||--o{ follows : follower
    users ||--o{ media : owns
  ```

* **Demo.** `GET /api/v1/database/tables` — real row counts and columns.
* **Metrics.** Row counts per table and per shard (`/api/v1/database/overview`).
* **Trade-offs.** No duplication, strongest consistency → join-heavy reads.
* **Say.** "PostgreSQL is the single source of truth; everything else can be
  rebuilt from it."
* **Do not claim.** Any resemblance to Meta's internal schema.

## Member 2 — Query optimization / indexes

* **Problem.** The same question can be answered with a scan, an index lookup,
  a join, or a projection — at very different costs.
* **Concept.** Indexes on access paths; measure, don't assume.
* **Implementation.** Composite and covering indexes in the schema
  (`ix_follows_following`, post/author indexes, `post_hashtags`); the *effect*
  is measured live by the benchmark API instead of asserted.
* **Diagram.** `flowchart LR; Q[query] --> P[planner] --> I[index scan] --> R[rows]`
* **Demo.** `/benchmarks` → run `normalized_read` versus `denormalized_read`;
  `/database/tables` shows the columns the indexes cover.
* **Metrics.** Measured P50/P95 for each query shape.
* **Trade-offs.** Indexes speed reads and slow writes; a projection is faster
  but eventually consistent.
* **Say.** "We measure query shapes instead of quoting plans we never ran."
* **Do not claim.** `EXPLAIN` output we did not capture, or index effects that
  were not measured on this dataset.

## Member 3 — Denormalization

* **Problem.** Feed-shaped reads need author names and counters — three joins
  and two count subqueries per page.
* **Concept.** Store a read-optimised projection, keep it in step with events,
  rebuild it from canonical data whenever you like.
* **Implementation.** `services/denormalization/projector.py`, table
  `denormalized_post_feed`, fed by `POST_*`, `LIKE_*`, `COMMENT_CREATED`.
* **Diagram.**

  ```mermaid
  flowchart LR
    W[canonical write] --> E[DomainEvent] --> P[Projector] --> D[denormalized_post_feed]
    C[canonical tables] -.rebuild.-> D
  ```

* **Demo.** Create a post with likes → the projection row appears; press
  "rebuild projection" and watch it repopulate from PostgreSQL.
* **Metrics.** `events_applied`, `errors`, `rows` in `/api/v1/read-model/stats`.
* **Trade-offs.** Fast reads, write amplification, eventual consistency.
* **Say.** "It is a projection: drop it and rebuild it, nothing is lost."
* **Do not claim.** That the projection is a source of truth.

## Member 4 — Sharding

* **Problem.** One database stops scaling vertically long before a social
  workload stops growing.
* **Concept.** Partition by a shard key with consistent hashing; targeted reads
  are O(1) shard, unkeyed reads scatter-gather.
* **Implementation.** `services/shard-router/*` (consistent hash + modulo,
  virtual nodes, hot-shard detector, rebalance migrator) plus
  `packages/db/shard_engine.py` (per-shard engines, health, timeouts).
* **Diagram.**

  ```mermaid
  flowchart LR
    A[API] --> R[ShardRouter]
    R --> S0[shard-0]
    R --> S1[shard-1]
    R --> S2[shard-2]
    R --> S3[shard-3]
    R -.scatter-gather.-> S0
  ```

* **Demo.** Shard map (real row counts, hot shard highlighted), "simulate
  failure", "run rebalance" (keys move to the spare shard).
* **Metrics.** `/api/v1/shards/stats`, `/shards/distribution`, routing latency
  per query in the structured logs.
* **Trade-offs.** Scale-out versus cross-shard joins, resharding complexity and
  no cross-shard transactions.
* **Say.** "Targeted reads stay on one shard; the expensive path is the one that
  must touch all of them — and the benchmark shows the difference."
* **Do not claim.** Automatic resharding, or cross-shard transactional
  guarantees.

## Member 5 — Replication

* **Problem.** Reads must scale inside a shard, and a primary will eventually
  fail.
* **Concept.** Primary for writes, replicas for reads, lag decides whether a
  replica is safe to use.
* **Implementation.** `services/replication/provider.py` — a real provider that
  probes each endpoint (`reachable`, `in_recovery`, `replication_lag_seconds`)
  and routes reads accordingly; compose attaches real streaming standbys.
* **Diagram.** `flowchart LR; W[write] --> P[primary]; R[read] --> P; R -.if healthy & low lag.-> REP[replica]`
* **Demo.** `/api/v1/replication/status`; the failover button is labelled
  **FAILOVER SIMULATION** whenever no replica URL is configured.
* **Metrics.** Per-shard `replica_configured`, `read_routing`, lag.
* **Trade-offs.** Read scaling and a failover target versus replication lag and
  stale reads.
* **Say.** "With replicas configured this is real routing; without them the demo
  only walks the decision, and the UI says so."
* **Do not claim.** Real failover or real lag numbers when no standby is
  running.

## Member 6 — Caching

* **Problem.** The same hot posts are read thousands of times; every one of them
  should not reach PostgreSQL.
* **Concept.** Cache-aside above the repository, TTL-bounded, invalidated on
  write, fail-open if the cache disappears.
* **Implementation.** `services/cache/cache.py` (Redis by default, in-memory
  backend for local dev); `X-Cache: HIT|MISS|BYPASS` on the read path.
* **Diagram.** `flowchart LR; A[API] --> C{cache?}; C --hit--> X[return]; C --miss--> DB[(PostgreSQL)] --> C`
* **Demo.** Cache panel: read the same post three times → MISS, HIT, HIT; then
  update the post → invalidated → MISS again.
* **Metrics.** hits, misses, hit ratio, `db_queries_avoided`, invalidations.
* **Trade-offs.** Latency versus staleness; invalidation correctness.
* **Say.** "The cache is above the repository and the shard router knows
  nothing about it."
* **Do not claim.** Strong consistency through the cache.

## Member 7 — Feed

* **Problem.** A feed is a per-user merge of somebody else's writes.
* **Concept.** Pull (follow graph → candidates → rank) or push (fan-out on
  write).
* **Implementation.** `services/feed/service.py`, both strategies, switchable
  with `FEED_STRATEGY`.
* **Demo.** "GENERATE FEED" after following somebody with posts.
* **Metrics.** Feed generation latency in the request-latency histogram;
  candidate/ranked counts in the response.
* **Trade-offs.** Pull is cheap to write and expensive to read; push is the
  opposite.
* **Say.** "Both strategies read the same canonical relationships; push simply
  pre-computes the merge."
* **Do not claim.** A ranking algorithm resembling any production feed.

## Member 8 — Media

* **Problem.** Images are large, immutable and read far more often than written.
* **Concept.** Metadata in the database, bytes in a blob store, de-duplication
  by content hash.
* **Implementation.** `services/media/*` — MinIO adapter (real object storage in
  compose), local-filesystem adapter as the documented fallback, SHA-256
  de-duplication.
* **Diagram.** `flowchart LR; U[upload] --> MD[(media row)] --> B[(blob store)]`
* **Demo.** Media card: upload → checksum, size, backend stats.
* **Metrics.** `objects`, `bytes`, backend name from `/api/v1/media/_stats/backend`.
* **Trade-offs.** Cheap storage and CDN-friendly immutability versus an extra
  system to run and de-duplication cost.
* **Say.** "HAYSTACK-INSPIRED EDUCATIONAL IMPLEMENTATION."
* **Do not claim.** That we implement Haystack.

## Member 9 — Social graph (EXCLUDED this iteration)

* **Status.** `NOT INTEGRATED`. The ported adjacency projection is preserved
  under `legacy/member9-graph/` for reference; it is not imported, not routed
  and has no API.
* **Say.** "Relationships are read from the canonical `follows` table. The
  graph projection was deliberately left out of this iteration."
* **Do not claim.** Any graph feature, or that the excluded code is running.

## Member 10 — Search

* **Problem.** Keyword search over content that lives in a relational store.
* **Concept.** A derived inverted index (BM25) rebuilt from canonical rows and
  refreshed by events.
* **Implementation.** `services/search/*` — in-memory BM25 provider and an
  OpenSearch provider behind one interface.
* **Demo.** SEARCH (may be empty until indexed) → "reindex from PostgreSQL".
* **Metrics.** `documents`, `queries`, recall is not claimed.
* **Trade-offs.** Fast text search versus asynchronous indexing lag.
* **Say.** "Indexing is asynchronous: a new post becomes searchable once its
  event is processed."
* **Do not claim.** Relevance quality, or synchronous indexing.

## Member 11 — Observability

* **Problem.** A distributed system you cannot measure is a system you cannot
  explain.
* **Concept.** Counters, gauges and histograms recorded in the request path;
  Prometheus exposition; health probes with an honest vocabulary.
* **Implementation.** `services/observability/metrics.py`; members 4/6/10/3 push
  their own counters in; `/api/v1/health` classifies every dependency.
* **Demo.** `/observability` page; `/metrics` scrape output.
* **Metrics.** Request count/errors, P50/P95/P99 latency, cache hit ratio,
  shard requests, events published/delivered/failed, indexed documents.
* **Trade-offs.** Instrumentation costs a little latency and a lot of care.
* **Say.** "Every series on that page is produced by code that ran; an empty
  histogram means we never exercised that path."
* **Do not claim.** SLOs or dashboards we do not ship.

## Member 12 — Benchmarking and system integration

* **Problem.** Twelve modules only become a system when they share one app, one
  contract, one configuration and one story.
* **Concept.** One FastAPI app, one event contract, one compose file, one
  measurement method; benchmark architecture A→F by *running* each one.
* **Implementation.** `benchmarks/unified_benchmark.py` (A–F sweep, JSON + CSV),
  in-process comparison API, `scripts/demo.py`, docs, frontend.
* **Demo.** `make demo-full`; `/benchmarks`; `make benchmark-unified`.
* **Metrics.** Percentiles per operation, cold vs warm cache, targeted vs
  scatter-gather, normalized vs denormalized.
* **Trade-offs.** Every layer adds complexity; the benchmark shows what each one
  buys.
* **Say.** "There is no scoreboard — each architecture trades consistency,
  storage, latency and complexity differently, and we show `n/a` where we did
  not measure."
* **Do not claim.** That these numbers generalise beyond this machine and
  dataset.

## Closing slide — the answer to the research question

As the workload grows, each architecture moves a different cost:

| Step | Buys | Costs |
| --- | --- | --- |
| Normalized | consistency, no duplication | joins on every read |
| Denormalized | fast feed reads | write amplification, eventual consistency |
| Sharded | horizontal scale | cross-shard queries, resharding, no cross-shard transactions |
| + Replication | read scaling, failover target | lag, stale reads |
| + Cache | hot reads never hit the database | staleness, invalidation |
| Full distributed | all of the above | operational complexity, several consistency regimes at once |

End with the disclaimer and with the limits listed in `INTEGRATION_AUDIT.md`
(Docker and real replication unverified in this environment).
