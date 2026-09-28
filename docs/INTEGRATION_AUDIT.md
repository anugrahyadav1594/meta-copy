# Integration audit — MetaScale

Audit of **all** branches and merged PRs, performed by reading the code (not
the READMEs). Last updated: 2026-09-28.

Branches found on the remote: `main`, `arena/01a08be4-meta-copy` (this work),
`dhiraj`, `ritika-cache`, `nikhil-denormalization`, `member-7-feed`,
`Satyam`, `shivam_member_8`, `haystack-by-Saurabh`, `abhishek-member-9`,
`member10-search-indexing`, `member-2-query-optimization`.

PRs: #1 foundation + M4 + M6 (open), #2 ritika-cache (open), #3 media by
Shivam (merged), #4 Dhiraj (merged — **replaced `main` with a SQL-only tree**).

---

## 1. Member matrix

| Member | Module | Existing implementation | Branch | Completeness | Integration problems |
|---|---|---|---|---|---|
| 1 | Canonical database | `001_schema.sql`, `002_indexes.sql`, `003_seed.sql`, `normalization.md`, `schema.md`, `er-diagram.md` — 10 tables (users, profiles, posts, comments, likes, follows, media, notifications, hashtags, post_hashtags) with FKs, CHECKs, indexes | `dhiraj` (merged → `main`, PR #4) | **Schema: complete. Code: none.** No ORM models, no Alembic revision, no repository, no deterministic seed generator, no sharding-aware FK policy | Identifiers `user_id/post_id/caption` clash with the runnable foundation's `id/user_id/content`; `posts.caption` drops `visibility`; `media` lacks `mime_type`/`checksum`; seed is literal SQL, not deterministic generator; no shard DDL generation |
| 2 | Query optimization | Streamlit EXPLAIN analyzer (`app.py`, `database.py`, `seed.py`, `schema.sql`) | `member-2-query-optimization` | Standalone dashboard; never run against MetaScale | Targets **MySQL**, not PostgreSQL; own `schema.sql`; no interface into the canonical repositories; separate server |
| 3 | Denormalization | `denormalization/004_denormalized_read_model.sql` (backfill into `denormalized_post_feed`) + Java/Spring AMQP worker (`like_events_queue` → `UPDATE ... like_count + 1`) | `nikhil-denormalization` | SQL backfill: good. Worker: one event type, external runtime | SQL uses `p.post_id`/`p.user_id` (foundation naming — must become canonical `post_id`/`user_id`); worker needs RabbitMQ + JVM and Maven; compiled `.class` files committed; no Python projector; only `LIKE_CREATED` handled; no rebuild/consistency check |
| 4 | Sharding | `services/shard-router/*`: MurmurHash3 modulo + consistent-hash ring w/ vnodes, shard registry, per-shard pooled engines, targeted/scatter-gather/cross-shard join, hot-shard detector, celebrity workload, 4→5 online rebalance w/ checksum verification, health checks, metrics, benchmarks | `arena/01a08be4-meta-copy` (PR #1) | **Complete and tested (79 tests, real PostgreSQL)** | Uses `id`/`user_id`; must be renamed to canonical identifiers; benchmark suite covers sharding only |
| 5 | Replication | `failover_engine.py` — `docker inspect` health + `print()` + `time.sleep(1)` + `os.environ["SHARD_0_URL"] = SHARD_0_REPLICA_URL` | `Satyam` | **Pure SIMULATION** (no PostgreSQL replication, no provider, no read/write split) | No `ShardConnectionProvider`; no replica containers in compose; no lag/health; failover does not touch a running system; duplicates compose/Dockerfile/Makefile |
| 6 | Redis cache | `services/cache/` — `CacheProvider`, metrics, hot keys, TTL | `ritika-cache` (PR #2) → reworked here | Reworked version: **complete + integrated + tested (23 tests)** | Original: `from cache import get_cache...` (functions never existed → ImportError), sync `redis.Redis` in async handlers, no ping/fail-open, cached only 4 fields (422 on every HIT), faked-DB standalone `app.py` returning 200 for missing posts, no `services/__init__.py`, Dockerfile omitted the package, compose Redis had no config/healthcheck/depends_on |
| 7 | Feed | `packages/db/repositories/feed.py` (`PullFeedRepository`: pull via follow graph + `user_feed` fan-out table), `FeedService`, `routes/feed.py` (`/feed/pull/{user_id}`, `/feed/push/{user_id}`) | `member-7-feed` | Pull model works on canonical data | `ensure_push_table()` executes DDL on every GET request; bound to a **single canonical engine** (not the repository abstraction → breaks in SHARDED mode); route shape differs from the required `/api/v1/users/{user_id}/feed`; no ranking; push table is not marked derived |
| 8 | Media | `media-service/main.py` — SHA-256 content-addressable local storage, dedup message | `shivam_member_8` (merged → `main`, PR #3) | Algorithm idea: good. Integration: none | Standalone FastAPI server; no PostgreSQL metadata row; no blob-store interface; no MinIO; no size/type limits |
| 8′ | Media (competing) | `app/{main,storage,database,config}.py` — streaming upload, size cap, SHA-256, `schema.sql` | `haystack-by-Saurabh` | Duplicate of Member 8 with stricter upload handling | **MySQL** (`mysql-connector` `cursor(dictionary=True)`); own `schema.sql`; separate server; competes with Shivam's implementation |
| 9 | Graph (TAO-inspired) | Node.js + Express (`src/server.js`, `src/graph.js` adjacency Maps, `src/routes/social.js`) + `public/` frontend + `src/seed.js` | `abhishek-member-9` | Working standalone demo | **SQLite (`better-sqlite3`) as an independent source of truth** — explicitly forbidden by the target architecture; Node runtime; own `users/posts/follows` schema; no rebuild-from-PostgreSQL path |
| 10 | Search | `engine.py` — in-memory inverted index (`defaultdict(set)`), token regex incl. `#`/`@`, score = number of matching tokens; `main.py` standalone FastAPI | `member10-search-indexing` | Algorithm: usable | Hard-coded sample documents at import time; no canonical indexing; no autocomplete; separate server; no `SearchProvider` abstraction |
| 11 | Observability | — none — | — | **Missing** | No `/metrics`, no histograms/percentiles, no structured dependency metrics |
| 12 | Benchmarking | `benchmarks/sharding/{benchmark_hash,benchmark_consistent_hash,benchmark_scaling}.py` + committed sample JSON/CSV | `arena/01a08be4-meta-copy` | Partial — sharding only | No unified runner; no A–F architecture comparison; no cache/feed/search/media latency; nothing measures denormalized vs normalized |
| — | Event system | `packages/events/contracts.py` — `DomainEvent`, `EventBus` Protocol, `NotConfiguredEventBus` no-op | `arena/01a08be4-meta-copy` | Interface only | No `InMemoryEventBus`, no RabbitMQ bus, no publisher in the service layer, no subscribers |
| — | Infra | `docker-compose.yml` (postgres, 5 shard containers, api + profiled placeholders), `Makefile`, `Dockerfile`, `scripts/dev_server.py` (embedded PostgreSQL) | `arena/01a08be4-meta-copy` | Mostly complete | No replica containers, no unified `distributed` profile, cache overlay separate, no OpenSearch/MinIO wiring to code |

---

## 2. Duplicate / conflicting systems (Phase 21 target list)

| Conflict | Resolution taken |
|---|---|
| Two canonical schemas (dhiraj `user_id/post_id` vs foundation `id/user_id`) | **Hybrid canonical**: adopt dhiraj's identifiers (`user_id`, `post_id`, `comment_id`, `media_id`, `notification_id`, `hashtag_id`) + add `hashtags`/`post_hashtags`; keep the foundation's richer columns (`visibility`, `mime_type`, `checksum_sha256`), shard-aware FK policy, indexes, Alembic, deterministic seeds, shard-DDL generation. Keeps `posts.content` (feed/denorm/cache/search all use text content; `caption` is a rename with no semantic gain) |
| Two media services (Shivam local-CAS vs Saurabh MySQL) | One `MediaBlobStore` interface with `LocalBlobStore` + `MinioBlobStore`; SHA-256 CAS and dedup preserved from Shivam, streaming + size/type limits from Saurabh; metadata into canonical `media`; MySQL service retired |
| SQLite graph as source of truth | TAO-inspired graph re-implemented in Python over the canonical repositories; adjacency is a **derived, rebuildable** projection, never authoritative |
| Java RabbitMQ denorm worker | Ported to a Python `ReadModelProjector` subscribed to the event bus; RabbitMQ transport becomes optional |
| Three standalone FastAPI servers (media ×2, search) | Folded into the single API under `/api/v1/media`, `/api/v1/search` |
| Duplicate compose/Dockerfile/Makefile in `Satyam` and member branches | Not adopted; single compose extended with profiles |
| Hard-coded sample search corpus | Index built from canonical PostgreSQL (posts/users/hashtags) via events + backfill |

---

## 3. Verified facts used for the audit

* `git log origin/main` → single grafted commit; `main` tree = 11 files, all
  from PR #4 (dhiraj). The foundation code is **not** on `main` — PR #1 is the
  branch that carries it.
* Satyam's `failover_engine.py` contains no SQL, no `psycopg`, no promote
  command — only `docker inspect`, `print`, `time.sleep(1)` and an env-var
  assignment. Classified SIMULATION.
* Member 9's `src/database.js` opens `data/social.db` with `better-sqlite3`
  and creates its own `users/posts/follows` tables.
* Member 10's `main.py` calls `search_engine.add_document("1", ...)` with four
  literal documents at import time.
* Member 3's `004_denormalized_read_model.sql` joins `posts p ON
  p.user_id = u.user_id` — i.e. it matches the foundation naming and must be
  rewritten for canonical identifiers.
* Member 7's `routes/feed.py` calls `ensure_push_table()` inside the GET
  handler (DDL per request).

---

## 4. Post-integration status (what was actually done)

Audit table §1 describes what each branch contained when it was read. This
section records what the integrated system does with it now, verified by
running the code.

| Member | Original state | Now | How it was integrated |
|---|---|---|---|
| 1 | SQL-only schema, no code | `packages/models/` ORM + Alembic + deterministic seed | hybrid schema adopted (Member-1 identifiers + foundation columns); shard DDL generated from the ORM; parity test against `infrastructure/postgres/init` |
| 2 | MySQL/Streamlit EXPLAIN tool | **FUTURE** | design credited; MySQL runtime excluded; nothing deleted — simply not wired |
| 3 | SQL backfill + Java AMQP worker | Python `DenormalizedFeedProjector` | ported; handles POST_CREATED/UPDATED/DELETED, LIKE_*, COMMENT_*; `POST /api/v1/read-model/rebuild` recomputes from canonical **and shards** |
| 4 | Complete sharding implementation | preserved, extended | ring/vnodes/registry/pooling/targeted/scatter/cross-shard/hot-shard/rebalance/checksums/health/metrics intact; fixed two integration bugs (generic endpoints used canonical repos in non-`SHARDED` shard modes; per-shard joins in derived rebuilds) |
| 5 | `failover_engine.py` simulation | real provider + labelled demo | `ReplicationAwareProvider` (read/write split, health, lag, `pg_promote()`); compose standbys added (UNVERIFIED); the original demo is preserved at `/api/v1/replication/demo/simulate-failover/{shard}` returning `simulated: true` |
| 6 | Broken cache branch | reworked and integrated | cache-aside on `GET /posts/{id}` (`post:{id}`, TTL 60 s), invalidation on write, hot-key detection, fail-open; 23 cache tests + unit/integration coverage |
| 7 | Pull feed with DDL-per-request | pull **and** push | DDL moved out of the GET (schema ensured once); works in NORMALIZED and SHARDED; `user_feed` marked derived; `/api/v1/users/{id}/feed` is the contract shape |
| 8 | Standalone local-CAS service (+ duplicate MySQL one) | `services/media/` inside the API | `MediaBlobStore` → `LocalBlobStore` / `MinioBlobStore`; SHA-256 CAS, dedup returns the existing row; metadata in PostgreSQL; response names the backend actually used |
| 9 | Node + SQLite graph | Python projection | no SQLite anywhere; adjacency rebuilt from `follows`; `/api/v1/graph/users/{id}/{followers,following}`, `mutuals/{other_id}`; `derived_projection: true` in its stats |
| 10 | Hard-coded corpus | derived index | hard-coded documents deleted; index built from PostgreSQL (scatter over shards) + events; `SearchProvider` with in-memory BM25 and OpenSearch backends |
| 11 | — none — | `services/observability/` | `/metrics` (Prometheus) + `/api/v1/metrics` (JSON) with request count, latency P50/P95/P99, db/cache/shard/replication/search/feed/media latency, hot shards, errors |
| 12 | Sharding benchmarks only | + unified A–F | `benchmarks/unified_benchmark.py` runs one workload across six configurations and writes JSON + CSV |
| 15 | Interface only | three transports | `InMemoryEventBus` (default), `RabbitMQEventBus` (optional), `NullEventBus`; services publish after commit; handler failures isolated and counted |
| 16 | Partial compose | one file, seven profiles | `docker-compose.cache.yml` merged in; `media`, `observability`, `replication`, `distributed` profiles; Prometheus config added (it previously had none and would not have started) |
| 17 | Several servers | one API | 77 endpoints under `/api/v1` plus `/health`, `/ready`, `/metrics` |
| 18 | Partial env handling | one settings system | all six modes implemented; full `.env.example`; no credentials in source |
| 19 | — | `/health` + `/ready` | readiness probes canonical PG, each shard, Redis, RabbitMQ, OpenSearch, MinIO and the replication topology; optional/disabled deps never fail the app |
| 20 | Partial tests | 139 passing | unit (routing, hashing, cache, events, projections), integration (canonical, sharded, derived systems, media, search), contract, and the full E2E chain |
| 21 | Duplicate list | resolved | see §2 and `FINAL_ARCHITECTURE.md` §10 |
| 22 | README claims | seven docs | `ARCHITECTURE`, `API_CONTRACT`, `DATABASE_SCHEMA`, `MODULE_OWNERS`, `INTEGRATION_AUDIT`, `RUNBOOK`, `DEMO`, `FINAL_ARCHITECTURE` — each labelled IMPLEMENTED / SIMULATED / OPTIONAL / FUTURE |
| 23 | — | `scripts/demo.py` | 20 steps executed against the live API; non-zero exit if any step fails |
| 24 | — | verification table | see `FINAL_ARCHITECTURE.md` §11 |

### Bugs found by running the system (not by reading it)

1. `GET /api/v1/metrics` → 500 (`CacheProvider.metrics()` does not exist;
   the method is `get_metrics()`), and the router was mounted on an empty
   sub-path.
2. `GET /api/v1/shards` → 500 (`row_count` was a `(shard_id, total)` tuple,
   failing response validation).
3. In `SHARDED_CACHED` / `SHARDED_REPLICATED` / `FULL_DISTRIBUTED` the
   generic endpoints wrote to the canonical database, never to the shards.
4. Derived rebuilds joined inside each shard, silently producing partial
   projections (feed rebuild: 20 554 → 38 627 rows after the fix).
5. Media de-duplication crashed on the second upload (UNIQUE `storage_key`)
   instead of returning the existing row.
6. Every new module used stdlib logging with structlog-style kwargs — the
   first log line raised `TypeError`, which broke the replication demo
   endpoint.
7. `psycopg3` rejects `:param::type`; the feed fan-out used
   `COALESCE(:created_at::timestamptz, now())`.
8. A partial post update wrote `visibility = NULL` into the read model
   (NOT NULL), so `POST_UPDATED` projection failed.
9. Hashtags were indexed as `#metascale`, so a `metascale` query missed them.

Each fix is committed with the reasoning in its message.
