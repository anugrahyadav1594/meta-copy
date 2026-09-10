# MetaScale architecture

## 1. Layered design

```text
Client
  → FastAPI routes (apps/api/routes)
  → Service layer (apps/api/services)         validation, business rules
  → Repository interfaces (packages/db/repositories/base.py)
  ├─ NORMALIZED: Canonical*Repository → single PostgreSQL
  └─ SHARDED:    Sharded*Repository  → Targeted/Scatter/Cross-shard
                                   → Shard Router (services/shard-router)
                                   → ShardEngineManager (one pool per shard)
                                   → independent PostgreSQL shards
```

Routes never touch SQLAlchemy directly; services depend only on abstract
repository interfaces (`UserRepository`, `PostRepository`,
`CommentRepository`). The concrete implementation is selected once in the
composition root `apps/api/dependencies.py::Platform` according to `MODE`.
This is what lets Members 3/5/6 swap storage strategies without touching the
API.

Request flow in SHARDED mode (the separation the project treats as
inviolable):

```text
API/Service → (future Redis cache, Member 6) → Repository → ShardRouter (M4)
            → (future replica selection, Member 5) → PostgreSQL shard
```

The shard router never knows whether a cache exists.

## 2. Implemented now

**Foundation**

- FastAPI application with structured JSON logging (request_id), structured
  error envelopes, Pydantic v2 validation, OpenAPI at `/openapi.json`.
- Canonical normalized PostgreSQL schema (8 tables, 3NF) as SQLAlchemy 2
  models — the single shared model for canonical DB and every shard.
- Constraints: primary/unique/not-null/CHECK, foreign keys, cascades,
  composite indexes (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)).
- Alembic migrations (`migrations/`) **and** Docker first-boot SQL
  (`infrastructure/postgres/init`), kept in parity by a test.
- Deterministic mock dataset (small/medium/large, fixed RNG seed).
- Repository abstraction with canonical and sharded implementations.
- Docker Compose with canonical PostgreSQL, 4 active + 1 spare shard
  containers, and profiled placeholders; embedded-PostgreSQL dev server for
  environments without Docker.
- Configuration via Pydantic Settings and `.env`; no secrets in code.

**Sharding (Member 4)**

- Stable MurmurHash3 modulo routing and a consistent-hash ring with virtual
  nodes (configurable count and entity shard-key mapping).
- Shard metadata registry (abstract seam + in-memory implementation;
  optional durable audit tables).
- One independent pooled engine per shard, lazy creation; structured handling
  of connection failure/timeout/pool exhaustion.
- Targeted queries, concurrent scatter-gather with merge/sort/limit,
  application-side cross-shard join with network-hop accounting.
- CRUD through the router for users and posts (caller never picks a shard).
- Hot-shard detector, metrics collector (P50/P95/P99, rates, rows, errors),
  and a real skewed celebrity-workload generator.
- Online shard-addition workflow (plan → copy → checksum-verify → switch →
  cleanup) that refuses to switch ownership on checksum mismatch.
- Shard health checking and a clearly-labeled SIMULATION failure endpoint.
- Shard admin + sharded-data REST APIs; JSON/CSV/console benchmarks A–E.

## 3. Future modules (extension points only — NOT implemented)

These are deliberately represented by interfaces/placeholders; do not mistake
them for working systems:

| Module | Owner | Seam today |
|---|---|---|
| Denormalized read models | Member 3 | `events/contracts.ReadModelProjector`, `DomainEvent`/`EventBus` |
| Replication / primary-replica selection / failover | Member 5 | `db/shard_engine.ShardConnectionProvider` (`PrimaryOnlyProvider` today) |
| Redis cache-aside, invalidation, TTL | Member 6 | `events/contracts.CacheProvider`; first target `GET /posts/{id}` (`post:{id}`) |
| Feed generation | Member 7+ | `FeedService` |
| TAO-inspired graph | Member 7+ | `GraphAccessLayer` |
| Haystack-inspired blob storage | Member 7+ | `MediaBlobStore` (only media *metadata* is in PostgreSQL now) |
| Search | Member 7+ | `SearchIndex`; `opensearch` compose placeholder |
| Observability | Member 7+ | `MetricsSink`; metrics already exported as a plain dict; `prometheus`/`grafana` placeholders |
| Messaging | future | `EventBus`/`NotConfiguredEventBus`; `rabbitmq` placeholder |

Reserved deployment modes `DENORMALIZED`, `SHARDED_REPLICATED`,
`SHARDED_CACHED`, `FULL_DISTRIBUTED` exist as enum values but their behavior is
not built. Calling an unbuilt seam raises `NotConfiguredError` (HTTP 501).

## 4. Canonical-vs-derived rule

PostgreSQL (single instance in `NORMALIZED`, the shard set in `SHARDED`) is
the only source of truth. Caches, search indexes, graph projections, and feeds
consume domain events and are rebuildable from PostgreSQL. They are never
written through as authoritative storage.

## 5. Request/observability plumbing

Every request gets a `request_id` (header `X-Request-ID` or generated). Shard
operations log structured fields: `operation`, `key`, `shard_id`,
`routing_strategy`, `query_type`, `routing_latency_ms`, `database_latency_ms`,
`status`. `MetricsCollector.snapshot()` is the contract the future
observability member will scrape.

## 6. Configuration modes

Set `MODE`/`SHARDING_ENABLED` and `SHARD_n_URL` (see `.env.example`). The
generic `/users`, `/posts` endpoints use sharded repositories automatically in
SHARDED mode; `/sharded/...` always targets the shard path and returns routing
telemetry; `/shards/...` is the shard admin surface.
