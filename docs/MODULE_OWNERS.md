# Module owners & integration rules

MetaScale is one integrated system. Members must build against the shared
foundation rather than fork the schema or bypass the repository/router seams.

## Ownership matrix

| Member | Module | Owns | Does NOT own / touch |
|---|---|---|---|
| 1 | Canonical database | `packages/models`, normalization, Alembic migrations, `infrastructure/postgres/init`, seed data | sharding internals, caches |
| 3 | Denormalization | denormalized read-model projections (future `ReadModelProjector`), consumes domain events | canonical writes, shard routing |
| **4 (implemented here)** | **Sharding (ShardRouter)** | shard-key mapping, consistent-hash ring + vnodes, shard registry, per-shard connection pools, targeted/scatter/cross-shard queries, hot-shard detection, rebalancing, sharding benchmarks | replication selection, caching, search/feed/graph/media logic |
| 5 | Replication | primary/replica endpoints, replica lag, failover by replacing `ShardConnectionProvider` | choosing the shard (that is Member 4), ring topology |
| **6 (implemented here)** | **Redis caching** | async cache-aside wrapper above repositories (`services/cache`), TTL, write invalidation, cache metrics, hot-key detection, fail-open, Docker Redis profile | repository internals and the shard router (must stay cache-unaware) |
| 7+ | Feeds / TAO-inspired graph / Haystack-inspired media / search / observability | projections consuming events or repository reads; `MetricsSink` export | canonical schema as source of truth |

The cardinal boundary: **Member 4 chooses the shard; Member 5 chooses the
replica inside that shard; Member 6 sits above both.**

```text
Request → (M6 Cache) → Repository → M4 Shard Router → (M5 Replica provider) → PostgreSQL
```

## Shared, frozen foundation (do not fork)

- ORM models: `packages/models/*` — one canonical schema for the canonical DB
  AND every shard. Additive, reviewed migrations only.
- DB plumbing: `packages/db/engine.py`, `session.py`, `shard_engine.py`,
  `ids.py`, and `packages/db/repositories/base.py`.
- Cross-cutting: `packages/common/{config,enums,exceptions,logging}.py`.
- Contracts/seams: `packages/events/contracts.py`.
- Seed data: `scripts/_dataset.py` (deterministic; shared by all members).

## How each future member plugs in (integration guide)

1. **Do not change canonical tables for derived data.** Build projections in
   your own store keyed by canonical ids; PostgreSQL remains authoritative.
2. **Talk to data through repository interfaces.** Depend on
   `UserRepository`/`PostRepository`/`CommentRepository` (or add new abstract
   repositories here, implemented for both canonical and sharded backends).
3. **Member 3 (denormalization):** after a successful canonical write the
   service publishes a `DomainEvent` (bus currently a no-op
   `NotConfiguredEventBus`); subscribe your projector when messaging exists.
4. **Member 5 (replication):** implement `ShardConnectionProvider` to return a
   `ConnectionEndpoint(role="replica", url=...)` for reads and `role=
   "primary"` for writes; register it via `ShardEngineManager(provider=...)`.
   Routing, checksums, and query executors are unchanged.
5. **Member 6 (cache):** decorate a repository with cache-aside. First target
   `GET /api/v1/posts/{id}` with key `post:{id}`: on cache miss call the
   repository (which itself routes through shards); on write invalidate. The
   router must not receive any cache parameter.
6. **Members 7+ (feed/graph/search/media):** implement the matching abstract
   seam in `events/contracts.py`; read through repositories/events; store
   blobs via the future `MediaBlobStore` (the `media` table holds metadata
   only).
7. **Observability:** consume `MetricsCollector.snapshot()` (or the registry
   `/api/v1/shards/stats`) via a `MetricsSink`; structured logs already carry
   `request_id, operation, shard_id, routing_strategy, key, query_type,
   latency_ms, status`.

## Adding a new shardable entity

- add the model under `packages/models` (canonical DDL only);
- extend `EntityName` and `DEFAULT_ENTITY_SHARD_KEYS` (or pass a custom
  mapping) with the column that best serves its hot access path;
- add a repository on the abstract base and a sharded implementation that
  routes by the mapped column; keep cross-shard references FK-free;
- the shard DDL regenerates via `python scripts/initialize_shards.py --write-sql`.

## Definition of "done" for an integration change

- canonical and sharded modes both work;
- `make test` green (unit + real-PostgreSQL integration/contract tests);
- no new canonical source of truth (caches/indexes are rebuildable);
- no credentials in code; structured errors and logs preserved.
