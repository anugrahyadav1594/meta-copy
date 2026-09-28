# Module owners & integration rules

MetaScale is one integrated system. Members build against the shared
foundation rather than forking the schema or bypassing the repository /
router seams. This file records who owns what **after** the integration, and
what each member's original branch contributed.

## Integration rules (non-negotiable)

1. **PostgreSQL is the only source of truth.** Caches, indexes, projections
   and blob stores are derived and must be rebuildable.
2. **Layering**: route → service → cache → repository → shard router →
   replication provider → PostgreSQL. No layer skips another.
3. **The shard router never learns about caching**, and the cache never
   learns about sharding.
4. **No second relational engine.** SQLite and MySQL are out of the runtime;
   useful logic was ported to Python over PostgreSQL.
5. **Nothing may be called "real replication" unless a PostgreSQL command
   actually runs.** Simulations carry `simulated: true`.
6. **No fabricated numbers.** Benchmarks and metrics come from work that
   happened.
7. **Optional backends are off by default** and must not break startup.

## Ownership matrix

| Member | Module | Owns | Status after integration |
|---|---|---|---|
| 1 | Canonical database | `packages/models`, Alembic migrations, `infrastructure/postgres/init`, deterministic seed | IMPLEMENTED |
| 2 | Query optimisation / EXPLAIN analysis | design credited; the MySQL + Streamlit tool is out of the runtime | FUTURE |
| 3 | Denormalization | `services/denormalization/`, `apps/api/routes/read_model.py` — event-driven `denormalized_post_feed`, rebuildable | IMPLEMENTED (Java/RabbitMQ worker superseded by the Python bus) |
| 4 | Sharding | `services/shard-router/` — ring + vnodes, hash routing, registry, pooling, targeted/scatter/cross-shard, hot shards, online rebalance, checksums, health, metrics, benchmarks | IMPLEMENTED, preserved |
| 5 | Replication | `services/replication/provider.py` — provider, read/write split, health + lag, promotion; compose standbys | IMPLEMENTED (compose replicas UNVERIFIED) |
| 6 | Distributed cache | `services/cache/cache.py`, `apps/api/routes/cache.py` — cache-aside on `GET /posts/{id}`, TTL, invalidation, hot keys, fail-open | IMPLEMENTED |
| 7 | Feed | `services/feed/service.py` — pull and fan-out-on-write, rebuildable push table | IMPLEMENTED |
| 8 | Media | `services/media/` — SHA-256 CAS, `LocalBlobStore` / `MinioBlobStore`, metadata in PostgreSQL | IMPLEMENTED (MinIO OPTIONAL) |
| 9 | Social graph | `services/graph/service.py` — rebuildable projection; SQLite removed | IMPLEMENTED (Node/SQLite service retired) |
| 10 | Search | `services/search/` — `InMemorySearchProvider` (BM25) / `OpenSearchProvider`, autocomplete | IMPLEMENTED (OpenSearch OPTIONAL) |
| 11 | Observability | `services/observability/`, `apps/api/routes/metrics.py` — `/metrics`, `/api/v1/metrics`, P50/P95/P99 | IMPLEMENTED |
| 12 | Benchmarks | `benchmarks/unified_benchmark.py` (A–F) and `benchmarks/sharding/*` | IMPLEMENTED |
| 15 | Events | `packages/events/` — `DomainEvent`, `EventBus`, `InMemory` / `RabbitMQ` / `Null` | IMPLEMENTED (RabbitMQ OPTIONAL) |
| 16 | Deployment | `docker-compose.yml` (one file, seven profiles) | IMPLEMENTED, UNVERIFIED |
| 17 | API surface | `apps/api/routes/` — 77 endpoints under `/api/v1` | IMPLEMENTED |
| 18 | Configuration | `packages/common/config.py` + `.env.example` | IMPLEMENTED |
| 19 | Health | `apps/api/routes/health.py` — `/health`, `/ready` | IMPLEMENTED |
| 20 | Tests | `tests/` (unit, integration, contract, E2E) + per-service suites | IMPLEMENTED, 139 passing |
| 21 | Duplicates | removal/adaptation pass | IMPLEMENTED |
| 22 | Documentation | `docs/` | IMPLEMENTED |
| 23 | Demo | `scripts/demo.py`, `docs/DEMO.md` | IMPLEMENTED |
| 24 | Final verification | tests, lint, format, live run | IMPLEMENTED |

## Cross-cutting contracts (see `packages/events/contracts.py`)

Every cross-module extension point is declared once in
`packages/events/contracts.py` with a pointer to its implementation:

* `EventBus` — `InMemoryEventBus` (default), `RabbitMQEventBus` (optional),
  `NullEventBus`;
* `CacheProvider` — `services.cache.cache.CacheProvider` above the
  repository;
* `SearchProvider` — `InMemorySearchProvider` / `OpenSearchProvider`;
* `MediaBlobStore` — `LocalBlobStore` / `MinioBlobStore`;
* the eight repository interfaces in `packages/db/repositories/base.py`,
  implemented by both `Canonical*Repository` and `Sharded*Repository`;
* `ShardConnectionProvider` — primary/replica resolution per shard.

## What members must not do

* add a second write path that bypasses the repository interfaces;
* read or write another member's derived table directly (subscribe to the
  event, or rebuild);
* introduce a new relational engine, a second settings system, or a second
  FastAPI app;
* call a simulation "real", or report a number that was not measured.
