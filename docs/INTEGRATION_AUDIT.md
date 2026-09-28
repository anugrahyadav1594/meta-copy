# Integration Audit — MetaScale final integrated system

**Project:** MetaScale — *Database Normalization Challenges in Facebook and
Instagram* (educational/research).

> **Disclaimer.** This is an independent educational/research project inspired
> by publicly discussed database and infrastructure concepts associated with
> large-scale social platforms. It is not affiliated with, endorsed by, or a
> reproduction of Meta's proprietary systems.

This audit records, per member module: what existed, whether it is really
implemented (versus simulated or demo-only), what integration work was needed
to make it part of **one** system, and the API it ends up behind. It is written
after running the system, not before.

## 1. System shape

One FastAPI application, one `/api/v1` prefix, one canonical source of truth.

```
Client (Architecture Control Center)
  -> FastAPI  (/api/v1)
  -> Service layer (business rules, validation)
  -> Cache adapter            (Member 6, cache-aside, above the repository)
  -> Repository               (canonical | sharded)
  -> Shard Router             (Member 4, knows nothing about the cache)
  -> Replication provider     (Member 5, primary/replica inside a shard)
  -> PostgreSQL               (source of truth: canonical + shards)

Derived (rebuildable) systems hang off the event bus, never off the write path:
  denormalized read model (M3) · feed (M7) · media metadata (M8) · search (M10)
  · observability (M11)
```

PostgreSQL is the only relational engine and the only source of truth. Redis is
a rebuildable cache, the search index is a projection, the denormalized feed
table is a projection — all three can be dropped and rebuilt from PostgreSQL.

## 2. Member-by-member audit

| Member | Module | Existing Implementation | Status | Integration Work | Final API |
| --- | --- | --- | --- | --- | --- |
| M1 | Canonical normalized schema | `packages/models/*`, `migrations/`, `packages/db/repositories/canonical.py` — 3NF tables, FKs, indexes, hybrid 64-bit ids | IMPLEMENTED | Kept as the single source of truth; repositories switched between canonical and sharded behind one interface | `POST/GET/PATCH/DELETE /api/v1/users`, `/api/v1/posts`, `/api/v1/posts/{id}/comments`, `/likes` |
| M2 | Query optimization / indexes | Schema indexes in `packages/models/*` (follower/post/author + composite indexes); no standalone query-optimizer module existed in the repo | PARTIAL | Index set documented in `docs/DATABASE_DESIGN.md`; the *effect* of query shape is measured live instead of asserted (`normalized_read` vs `denormalized_read` vs cached read) | `GET /api/v1/benchmarks/compare`, `GET /api/v1/database/tables` |
| M3 | Denormalization | `services/denormalization/projector.py` — `denormalized_post_feed` fed by domain events | IMPLEMENTED | Wired to the unified event bus; rebuild scatters over the shards and joins in the API process; projector errors counted, never fatal | `GET /api/v1/read-model/posts`, `POST /api/v1/read-model/rebuild`, `GET /api/v1/read-model/stats` |
| M4 | Sharding | `services/shard-router/*` (consistent hash + modulo, virtual nodes, hot-shard detector, rebalance migrator), `packages/db/shard_engine.py` | IMPLEMENTED | Router used by **all** generic endpoints in shard modes (previously only `/sharded/*`); scatter-gather merge fixed; per-shard joins moved into the API process | `GET /api/v1/shards`, `/shards/stats`, `/shards/distribution`, `/shards/route/{key}`, `POST /api/v1/shards/rebalance`, `POST /api/v1/shards/{id}/simulate-down` |
| M5 | Replication | `services/replication/provider.py` — real primary/replica provider with health + lag probes; compose attaches streaming standbys | IMPLEMENTED (provider) / SIMULATION (failover demo) | Provider sits **below** the shard router (router picks the shard, provider picks the node); the failover demo is explicitly labelled SIMULATED and does not claim a real promotion | `GET /api/v1/replication/status`, `/describe`, `POST /api/v1/replication/demo/simulate-failover/{shard}` (labelled), `POST /api/v1/replication/promote/{shard}` |
| M6 | Redis cache | `services/cache/cache.py` — async cache-aside, TTL, invalidation, hit/miss/hot-key metrics, fail-open | IMPLEMENTED | Composed **above** the repository; the router is unaware of it; `X-Cache: HIT/MISS/BYPASS` on the read path; invalidation on write | `GET /api/v1/cache/metrics`, `/health`, `/keys`, `DELETE /api/v1/cache/posts/{post_id}` |
| M7 | Feed | `services/feed/service.py` — pull (follow graph → candidates → rank) and push (fan-out table) | IMPLEMENTED | Both strategies survive in one service; push fan-out subscribes to `POST_CREATED`; strategy reported by the API | `GET /api/v1/users/{id}/feed`, `GET /api/v1/feed/strategies`, `/stats`, `POST /api/v1/feed/rebuild` |
| M8 | Media | `services/media/*` — metadata in PostgreSQL, bytes in a blob store (MinIO adapter, local filesystem fallback) | IMPLEMENTED | MinIO is the real backend in compose; local FS is labelled as the fallback adapter; de-duplication by SHA-256; labelled **HAYSTACK-INSPIRED EDUCATIONAL IMPLEMENTATION** in the UI | `POST /api/v1/media/upload`, `GET /api/v1/media/{id}`, `GET /api/v1/media/_stats/backend`, `DELETE /api/v1/media/{id}` |
| M9 | Social graph (TAO-inspired) | `legacy/member9-graph/` — adjacency projection, ported to Python + PostgreSQL | **NOT INTEGRATED** | Excluded from this iteration by decision. Code preserved verbatim for reference; not imported, not routed, no replacement module written. Relationships are read from the canonical `follows` table instead | none — use `POST /api/v1/users/{id}/follow`, `GET /api/v1/users/{id}/followers`, `GET /api/v1/users/{id}/following` |
| M10 | Search | `services/search/*` — BM25 inverted index (in-memory) + OpenSearch provider behind one interface | IMPLEMENTED | Index built from canonical rows and refreshed by events; async indexing is labelled as asynchronous in the UI; nothing is hard-coded into a corpus | `GET /api/v1/search`, `/autocomplete`, `/stats`, `POST /api/v1/search/reindex` |
| M11 | Observability | `services/observability/metrics.py` — counters, gauges, histograms (P50/P95/P99), Prometheus exposition | IMPLEMENTED | Every metric is recorded in the request path; members 4/6/10/3 push their own real counters in; no decorative series | `GET /metrics`, `GET /api/v1/metrics`, `/summary`, `GET /api/v1/observability/status`, `/metrics`, `/latency` |
| M12 | Benchmarking + system integration | `benchmarks/unified_benchmark.py` (A–F sweep, JSON + CSV), `benchmarks/sharding/*`, `scripts/demo.py` | IMPLEMENTED (integration owner, not a separate subsystem) | Also owns: one app, one compose file, env contract, docs, and the in-process comparison API used by the UI | `GET /api/v1/benchmarks/operations`, `/architectures`, `POST /api/v1/benchmarks/run`, `/compare`; `make benchmark-unified`, `make demo` |

### Status vocabulary

* **IMPLEMENTED** — real code, exercised by tests and by the running system.
* **PARTIAL** — real but narrower than the topic (documented explicitly).
* **SIMULATION** — demonstrates a concept with real routing/provider logic but
  without the external system (never presented as the real thing).
* **ADAPTER REQUIRED** — code exists but needs an external service to be
  meaningful.
* **DEMO ONLY** — educational path, not part of the production read/write path.
* **NOT INTEGRATED** — deliberately excluded this iteration.

## 3. Cross-cutting integration work (M12)

| Area | What was unified |
| --- | --- |
| Application | one FastAPI app (`apps/api/main.py`), one `/api/v1` prefix, request ids, one error envelope, OpenAPI tags per member |
| Configuration | one `Settings` object; `.env.example` lists `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`, `OPENSEARCH_URL`, `MINIO_ENDPOINT`, `SHARD_COUNT`, `SHARDING_ENABLED`, `CACHE_DEFAULT_TTL`, `EVENT_BUS` (alias `EVENT_BUS_BACKEND`), `SEARCH_PROVIDER`, `MEDIA_BACKEND`. No real credentials anywhere |
| Events | one `DomainEvent` (`event_id`, `event_type`, `entity_type`, `entity_id`, `timestamp`, `payload`, `source`) over one `EventBus` with `InMemoryEventBus` + `RabbitMQEventBus`; published **after** the canonical commit |
| Change tracking | every canonical write emits `operation / table / primary key / shard / before / after / timestamp / request id / service / latency` (no secrets) — `GET /api/v1/database/changes` |
| Health | `GET /api/v1/health` probes API, PostgreSQL, every shard, Redis, RabbitMQ, OpenSearch, MinIO and replication; `healthy / degraded / unavailable` |
| Routing | in every shard mode the **generic** endpoints route through the shard router, so `/api/v1/users` and `/api/v1/sharded/users` behave identically |
| Frontend | one Architecture Control Center (`frontend/`): dashboard, observability and benchmarks pages, 7-step request process component, DB explorer, live change diff, shard map, cache panel, denormalization side-by-side, replication panel, feed/search/media demos |
| Demo | `scripts/demo.py` — 20 steps against a live API; `make demo` (against a running API) and `make demo-full` (starts its own API) |
| Docker | one `docker-compose.yml`; profile groups **core** (always on), **distributed**, **observability**, plus fine-grained per-backend profiles |
| Docs | this audit, `FINAL_DEMO.md`, `PRESENTATION_GUIDE.md`, `FRONTEND_DEMO.md`, `EVENTS.md`, `DATABASE_DESIGN.md`, `BENCHMARKING.md`, `CACHING.md`, `REPLICATION.md`, `DENORMALIZATION.md`, `SHARDING.md`, `ARCHITECTURE.md`, `architecture.md` (Mermaid), `API_CONTRACT.md`, `MODULE_OWNERS.md` |

## 4. Bugs found by running the system (not by reading it)

Each of these only appeared once the integrated system was actually started;
all are fixed and covered by a test.

1. `GET /api/v1/metrics` returned **500** (missing registry key).
2. `GET /api/v1/shards` returned **500** — tuple `row_count` compared as int.
3. Generic endpoints were **not** routed through the shard router in
   `SHARDED_CACHED` / `SHARDED_REPLICATED` / `FULL_DISTRIBUTED`, so
   `/api/v1/users` and `/api/v1/sharded/users` silently disagreed.
4. Derived rebuilds (feed / read model / search) joined **inside** each shard,
   so cross-shard data was missing; they now scatter and merge in the API.
5. Media upload crashed on the de-duplication path (`checksum_sha256`).
6. `logging` kwargs were passed to the stdlib logger instead of the structured
   one, dropping fields.
7. psycopg3 rejected `:param::cast` casts in three queries.
8. `visibility` could be written as SQL `NULL`, breaking the enum contract.
9. `#hashtag` search never matched because tags were stored without the `#`.
10. `GET /api/v1/users/{id}/following` had **no `response_model`**, so FastAPI
    tried to serialize ORM `Follow` rows and returned **500** (found while
    removing Member 9's graph router).

## 5. Verification performed

| Check | Result |
| --- | --- |
| `pytest tests services/shard-router/tests services/cache/tests` | **141 passed** (real PostgreSQL 16 clusters, no mocks) |
| `ruff check .` / `black --check .` | clean (135 files; `legacy/` excluded on purpose) |
| `make demo` (20 steps, `FULL_DISTRIBUTED`) | **20/20** |
| `make demo-full` (`SHARDED_CACHED`, self-started API) | **20/20**, exit 0 |
| `GET /api/v1/health` | `healthy`, 4/4 shards probed, optional backends reported `unavailable: not configured` |
| Change stream | INSERT/UPDATE on users/posts/likes/comments/follows recorded with shard, before/after, request id, latency; `password_hash` never recorded |
| Benchmarks (live, 40 iterations) | `read_post` p50 ≈ 1.2 ms · `cached_read` cold ≈ 1.5 ms → warm ≈ 0.12 ms · `scatter_gather` ≈ 4.4 ms · `denormalized_read` ≈ 0.7 ms vs `normalized_read` ≈ 1.4 ms |
| Frontend | `npm run build` clean (TypeScript strict), dev server proxies `/api`, pages `/dashboard`, `/observability`, `/benchmarks` |

Numbers above are from one run on one machine; re-measure before quoting them.

## 6. Known limits (stated, not hidden)

* **Docker Compose is UNVERIFIED in this environment** — no Docker daemon was
  available, so profiles were validated by inspection only.
* **Real streaming replication is UNVERIFIED** for the same reason: the
  provider is real, the standbys were never started here.
* **RabbitMQ / OpenSearch / MinIO paths are exercised by unit tests with fakes
  or by config**, not against live servers here; `/api/v1/health` reports them
  as `unavailable: not configured` in the local dev setup.
* The change stream is an **in-process ring buffer**: restarting the API clears
  it. It is a teaching/diagnostic view, not a durable audit log.
* Member 9 (graph) is excluded from this iteration by decision.
