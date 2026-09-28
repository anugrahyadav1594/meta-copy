> **Update — Member 9 is EXCLUDED from the final integrated iteration.**
> The TAO-inspired graph projection is no longer wired into the system: its code
> is preserved verbatim under `legacy/member9-graph/` for reference, it is not
> imported, and `/api/v1/graph/*` no longer exists. Relationships are read from
> the canonical `follows` table (`GET /api/v1/users/{id}/followers`,
> `/following`). The authoritative status table is
> [INTEGRATION_AUDIT.md](INTEGRATION_AUDIT.md); this document is kept for the
> historical record of the integration work.

# FINAL ARCHITECTURE — MetaScale, the integrated system

Status: integrated, running, and verified end to end. This document
describes what the code **does today**, not what a README once promised.
Every table below carries a status label:

| Label | Meaning |
| --- | --- |
| **IMPLEMENTED** | real code, exercised by tests and by the running system |
| **SIMULATED** | explicitly labelled demo behaviour — never presented as real |
| **OPTIONAL** | implemented behind an interface, off by default |
| **UNVERIFIED** | code exists and is wired, but could not be started in the sandbox (no Docker daemon) |
| **FUTURE** | documented design, not built |

Updated: 2026-09-29.

---

## 1. What the system is

**One** FastAPI application, one API prefix (`/api/v1`), one Pydantic
settings object, one canonical schema in **PostgreSQL**, and six deployment
modes selected by a single environment variable.

```bash
MODE=FULL_DISTRIBUTED CACHE_ENABLED=true REDIS_URL=memory:// DENORMALIZED_ENABLED=true \
  .venv/bin/python scripts/dev_server.py --sharded --seed small --sharded-seed --port 8000
```

| Mode | Canonical PG | Derived read model | Shard router | Replicas | Redis cache |
| --- | --- | :-: | :-: | :-: | :-: |
| `NORMALIZED` | ✅ | — | — | — | — |
| `DENORMALIZED` | ✅ | ✅ | — | — | — |
| `SHARDED` | ✅ | — | ✅ | — | — |
| `SHARDED_REPLICATED` | ✅ | — | ✅ | ✅ | — |
| `SHARDED_CACHED` | ✅ | — | ✅ | — | ✅ |
| `FULL_DISTRIBUTED` | ✅ | ✅ | ✅ | ✅ | ✅ |

The mode does not change any code path — it switches which layers are
**active**. `SHARDED_*` and `FULL_DISTRIBUTED` route every generic endpoint
(`/api/v1/users`, `/api/v1/posts`, …) through the shard router, so there is
exactly one write path.

---

## 2. Mandatory layering (enforced, not aspirational)

```text
Client
  → FastAPI routes            apps/api/routes/*
  → Service layer             apps/api/services/*        business rules, events
  → Cache adapter (M6)        services/cache/cache.py    cache-aside, TTL, fail-open
  → Repository interface      packages/db/repositories/base.py
  → Shard Router (M4)         services/shard-router/*    chooses the SHARD
  → Replication provider (M5) services/replication/*     chooses the REPLICA in it
  → PostgreSQL (source of truth)
```

Two invariants the code actually holds:

1. **The shard router knows nothing about caching.** No file under
   `services/shard-router/` imports the cache.
2. **The cache sits above the repository, never inside it.** Repositories
   have no cache parameter; `PostService` holds the cache and calls
   `get` → repository → `set`.

Member 4 picks the shard, Member 5 picks the replica inside it, Member 6 sits
above both.

---

## 3. Data: one canonical schema, five derived projections

**PostgreSQL is the only source of truth.** Everything else is rebuildable.

| Store | Kind | Where | Rebuild |
| --- | --- | --- | --- |
| `users`, `profiles`, `posts`, `comments`, `likes`, `follows`, `media`, `notifications`, `hashtags`, `post_hashtags` | canonical 3NF | PostgreSQL (canonical and/or shards) | n/a |
| `denormalized_post_feed` (M3) | derived read model | canonical PostgreSQL | `POST /api/v1/read-model/rebuild` |
| `user_feed` (M7) | derived fan-out table | canonical PostgreSQL | `POST /api/v1/feed/rebuild` |
| Social graph (M9) | derived adjacency, TTL cache | in-process | `POST /api/v1/graph/rebuild` |
| Search index (M10) | derived inverted index | in-process or OpenSearch | `POST /api/v1/search/reindex` |
| Media blobs (M8) | content-addressed bytes | filesystem or MinIO | re-upload (metadata is canonical) |

Identifiers follow the hybrid decision: `user_id`, `post_id`, `comment_id`,
`media_id`, `notification_id`, `hashtag_id`, with a proper
`hashtags` + `post_hashtags` vocabulary (no comma-separated tag columns) and
the richer foundation columns retained (`posts.visibility`,
`media.mime_type`, checksum, indexes, Alembic migrations).

**Placement is by access pattern, not by table** — this matters, see §7:

| Entity | Shard key | Why |
| --- | --- | --- |
| `users`, `profiles`, `posts`, `media`, `notifications` | `user_id` / `owner_id` | one author's data is co-located |
| `comments`, `likes`, `post_hashtags` | `post_id` | one post's data is co-located |
| `follows` | `follower_id` | a reader's edge list is co-located |
| `hashtags` | `hashtag_id` | vocabulary lookup is point-access |

---

## 4. Request walkthroughs (what the logs actually show)

**Write (create post), sharded mode:**

```
POST /api/v1/posts
  target: users     key=380100900397514752  shard-2  routing 0.02ms  db 17.9ms
  target: posts     key=380100900397514752  shard-2  routing 0.01ms  db  7.2ms
  target: hashtags  key="metascale"         shard-1  routing 0.04ms  db  5.2ms
  target: post_hashtags key=380100902570164227 shard-2
  → event POST_CREATED  → read model, feed fan-out, search index
```

**Read (get post), cache on:**

```
GET /api/v1/posts/{id}   x-cache: MISS  → repository → shard → PostgreSQL → cache set
GET /api/v1/posts/{id}   x-cache: HIT   → cache, no database round trip
PATCH /api/v1/posts/{id} → cache_invalidated key=post:{id}
```

Each shard hop emits a structured log line with routing and database latency
separately (`shard.query.targeted`), and scatter-gather queries emit
`shard.query.scatter` with per-shard row counts and unavailable shards.

---

## 5. Module map

| Member | Module | Status | Where |
| --- | --- | --- | --- |
| 1 | Canonical schema, migrations, deterministic seed | **IMPLEMENTED** | `packages/models`, `migrations/`, `scripts/seed.py` |
| 2 | EXPLAIN / query-plan analysis | **FUTURE** (design credited; the MySQL/Streamlit tool is out of the runtime) | — |
| 3 | Denormalized read model | **IMPLEMENTED** | `services/denormalization/`, `apps/api/routes/read_model.py` |
| 4 | Sharding: ring + vnodes, registry, pooling, targeted/scatter/cross-shard, hot shards, online rebalance, health, metrics, benchmarks | **IMPLEMENTED** | `services/shard-router/` |
| 5 | Replication: connection provider, read/write split, health/lag, promotion | **IMPLEMENTED** (+ compose replicas **UNVERIFIED**) | `services/replication/provider.py` |
| 6 | Cache-aside over `GET /posts/{id}` (`post:{id}`, TTL 60 s), invalidation, hot keys, graceful Redis-down | **IMPLEMENTED** | `services/cache/cache.py`, `apps/api/routes/cache.py` |
| 7 | Feed: pull **and** fan-out-on-write, documented trade-off | **IMPLEMENTED** | `services/feed/service.py` |
| 8 | Media: SHA-256 CAS, `LocalBlobStore` / `MinioBlobStore`, metadata in PG | **IMPLEMENTED** (MinIO **OPTIONAL/UNVERIFIED**) | `services/media/` |
| 9 | Social graph as a rebuildable projection (SQLite removed) | **IMPLEMENTED** | `services/graph/service.py` |
| 10 | Search: `InMemorySearchProvider` (BM25) / `OpenSearchProvider` | **IMPLEMENTED** (OpenSearch **OPTIONAL/UNVERIFIED**) | `services/search/` |
| 11 | Observability: `/metrics` (Prometheus), `/api/v1/metrics` (JSON), P50/P95/P99 | **IMPLEMENTED** | `services/observability/`, `apps/api/routes/metrics.py` |
| 12 | Unified benchmark A–F → JSON + CSV | **IMPLEMENTED** | `benchmarks/unified_benchmark.py` |
| 15 | `DomainEvent` + `EventBus` (`InMemory`, `RabbitMQ`, `Null`) | **IMPLEMENTED** (RabbitMQ **OPTIONAL**) | `packages/events/` |
| 16 | One `docker-compose.yml`, profiles | **IMPLEMENTED / UNVERIFIED** | `docker-compose.yml` |
| 17 | Single API under `/api/v1` (77 endpoints) | **IMPLEMENTED** | `apps/api/routes/` |
| 18 | One settings system, env-driven, no credentials in source | **IMPLEMENTED** | `packages/common/config.py` |
| 19 | `/health` + `/ready` with per-dependency status | **IMPLEMENTED** | `apps/api/routes/health.py` |
| 20 | Unit + integration + contract + E2E suites | **IMPLEMENTED** (139 tests) | `tests/`, `services/*/tests` |
| 21 | Duplicate removal / adaptation | **IMPLEMENTED** | see §10 |
| 22 | This document + `ARCHITECTURE`, `API_CONTRACT`, `DATABASE_SCHEMA`, `MODULE_OWNERS`, `INTEGRATION_AUDIT`, `RUNBOOK`, `DEMO` | **IMPLEMENTED** | `docs/` |
| 23 | 20-step live demo | **IMPLEMENTED** | `scripts/demo.py`, `docs/DEMO.md` |
| 24 | Final verification (tests, lint, format, real run) | **IMPLEMENTED** | §11 |

---

## 6. Event-driven derived systems (Member 15)

`packages/events/` defines one `DomainEvent` and one `EventBus` protocol;
`InMemoryEventBus` (default), `RabbitMQEventBus` (OPTIONAL) and
`NullEventBus` implement it. Services publish **after** a successful commit,
and a failing projection can never fail a request — the bus isolates handler
exceptions and counts them.

| Event | Consumers |
| --- | --- |
| `POST_CREATED` | read model (upsert), feed fan-out, search index |
| `POST_UPDATED` / `POST_DELETED` | read model update / delete |
| `LIKE_CREATED` / `LIKE_DELETED` | read model counters |
| `COMMENT_CREATED` | read model counters |
| `FOLLOW_CREATED` / `FOLLOW_DELETED` | graph invalidation |
| `MEDIA_CREATED`, `USER_CREATED` | available to future consumers |

Measured in a demo run: `{"published": 12, "delivered": 8, "failures": 0}`.

---

## 7. Sharding (Member 4) — preserved and extended

Everything from the member's implementation is intact: consistent hashing
with virtual nodes, hash routing, shard registry, per-shard pools, targeted /
scatter-gather / cross-shard queries, hot-shard and hot-user detection,
checksums, health checks, metrics and benchmarks, and online 4 → 5
rebalancing with row verification. The router depends on **no** external
service — no Redis, no ZooKeeper.

Two integration bugs were found by *running* it, both fixed:

* In `SHARDED_CACHED` / `SHARDED_REPLICATED` / `FULL_DISTRIBUTED` the generic
  endpoints used the canonical repositories, so writes never reached the
  shards. Every shard mode now routes through the router.
* Derived rebuilds were written as a JOIN inside each shard. Because `posts`
  is keyed by `user_id` while `follows` is keyed by `follower_id`, those
  joins silently returned partial data. Rebuilds now scatter the base tables
  and join in the API process (`packages/db/shard_scan.py`). On seeded data
  the feed rebuild went from 20 554 to **38 627** rows.

---

## 8. Replication (Member 5) — real provider + labelled simulation

`ReplicationAwareProvider` resolves a shard to a **primary** (writes) or a
**replica** (reads) endpoint, tracks health and replication lag, skips
replicas that are unhealthy or too far behind, and counts every decision
(`reads_primary`, `reads_replica`, `replica_skipped_unhealthy`,
`replica_skipped_lag`). `promote_replica()` issues real `pg_promote()`.

With no `SHARD_n_REPLICA_URL` configured the provider says so instead of
pretending: `replication_active: false`, every read served by the primary.

The member's demo is preserved but clearly separated:

* `POST /api/v1/replication/demo/simulate-failover/{shard}` →
  `{"simulated": true, ...}` — **SIMULATED**, no PostgreSQL command runs.
* `POST /api/v1/replication/promote/{shard}` → real; 409 when no standby.

The compose `replication` profile ships bitnami standbys that run
`pg_basebackup` on first boot — **UNVERIFIED** (no Docker daemon here).

---

## 9. Cache (Member 6)

`GET /api/v1/posts/{post_id}` is cached under `post:{post_id}` with a 60 s
TTL, above the repository and below the service. It reports `x-cache:
MISS|HIT`, invalidates on update/delete, detects hot keys, counts
`db_queries_avoided`, and **fails open**: a dead Redis turns into misses,
never into 500s. `REDIS_URL=memory://` selects fakeredis — **dev/test only**,
and the API reports `backend: "memory"` so nobody mistakes it for Redis.

---

## 10. Phase 21 — duplicates removed, useful code adapted

| Duplicate found | Resolution |
| --- | --- |
| SQLite graph store (Node.js service) as a source of truth | removed; logic re-implemented as a rebuildable projection over PostgreSQL (M9) |
| Java/Spring RabbitMQ worker | superseded: the transport is now a configuration choice in one Python event bus |
| MySQL media service (Saurabh) | logic ported into `services/media/` over PostgreSQL metadata |
| MySQL/Streamlit EXPLAIN analyzer (M2) | out of the runtime; credited in the audit, **FUTURE** |
| `docker-compose.cache.yml` overlay | merged into the single compose file (profile + env var) |
| Second `database.py` / session module / FastAPI app | single `packages/db/{engine,session}.py`, single `apps/api/main.py` |
| Hard-coded search corpus | deleted; the index is built from PostgreSQL and refreshed by events |
| Field-name mismatches (`caption` vs `content`, `checksum` vs `checksum_sha256`) | unified on the canonical column names |
| Hand-written SQL vs ORM drift | integration test keeps `infrastructure/postgres/init` in parity with the models |

---

## 11. Verification (Phase 24) — measured, not asserted

| Check | Command | Result |
| --- | --- | --- |
| Full test suite | `make test` | **139 passed** (unit, integration, contract, E2E) |
| Lint | `make lint` | `ruff` + `black --check` clean |
| Format | `make format` | applied; 129 files |
| Real app | `scripts/dev_server.py --sharded` | starts 5 PostgreSQL 16 clusters, API ready |
| Live demo | `make demo` | **20/20 steps** |
| End-to-end chain | `tests/integration/test_end_to_end.py` | create → post → route → retrieve (MISS/HIT) → comment → like → feed → search → graph → media dedup → read model → metrics → replication |
| Derived rebuilds | `/feed/rebuild`, `/read-model/rebuild`, `/search/reindex`, `/graph/rebuild` | 38 627 rows / 2 003 rows / 2 103 docs / adjacency |
| Unified benchmark | `make benchmark-unified` | six configurations, JSON + CSV |
| Compose | YAML validated (17 services, 7 profiles) | `docker compose config` **UNVERIFIED** — no Docker daemon in this environment |

### Unified benchmark sample (this machine, embedded PostgreSQL)

| Config | read_post p50 | read_feed p50 | read_graph p50 | write_post p50 | Notes |
| --- | --- | --- | --- | --- | --- |
| A NORMALIZED | 1.21 ms | 5.67 ms | 1.14 ms | 4.22 ms | baseline |
| B DENORMALIZED | 1.44 ms | 5.39 ms | 0.93 ms | 5.87 ms | + read model |
| C SHARDED | 1.27 ms | 6.24 ms | 3.13 ms | 4.41 ms | 1 130 shard requests |
| D SHARDED_REPLICATED | 1.30 ms | 5.79 ms | 3.10 ms | 4.47 ms | replicas not configured → all reads primary |
| E SHARDED_CACHED | **0.14 ms** | 5.94 ms | 3.03 ms | 4.47 ms | 128 hits / 132 misses (49.2 %) |
| F FULL_DISTRIBUTED | **0.10 ms** | 6.08 ms | 2.97 ms | 6.39 ms | cache + shards + read model |

Raw output: `benchmarks/results/samples/unified.{json,csv}` (a timestamped
copy is written to `benchmarks/results/raw/`). These numbers are for one
laptop with five local clusters; run the benchmark on your own hardware
before quoting them.

---

## 12. Known limitations

1. **Streaming replication is unverified.** The provider, health/lag checks
   and promotion are implemented and tested; the compose standbys were never
   started (no Docker daemon in the environment used to build this).
2. **Projection rebuilds join in the API process.** Because rows are placed by
   access pattern, a correct rebuild must gather across shards. Fine for the
   seeded datasets (tens of thousands of rows); a production system would
   stream it through a batch job.
3. **Default backends are in-process**: fakeredis, in-memory BM25 index,
   filesystem blobs. All sit behind the documented interfaces
   (`CacheProvider`, `SearchProvider`, `MediaBlobStore`), but only Redis,
   OpenSearch and MinIO are deployment-grade.
4. **One API process.** The service layer is stateless and the derived
   systems are per-process, so running multiple replicas would give each its
   own graph/search cache; a shared Redis/OpenSearch deployment (supported by
   config) is the multi-instance path.
5. **`make cache`/`messaging`/`search`/`media`/`observability`/`distributed`**
   need Docker; the equivalent no-Docker command is `make dev-full`.
6. The **`small` seed** (100 users / 1 000 posts / 5 000 comments / 10 000
   likes) is what the demo uses; `medium` and `large` are deterministic but
   slower to load.

---

## 13. Where to go next

* [RUNBOOK.md](RUNBOOK.md) — every command: install, run, configure, test,
  benchmark, troubleshoot.
* [DEMO.md](DEMO.md) — the 20-step walkthrough you can perform live.
* [API_CONTRACT.md](API_CONTRACT.md) — all 77 endpoints.
* [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) — canonical schema and shard keys.
* [MODULE_OWNERS.md](MODULE_OWNERS.md) — who owns what, and the integration
  rules.
* [INTEGRATION_AUDIT.md](INTEGRATION_AUDIT.md) — what each member's branch
  actually contained, and what was done with it.
