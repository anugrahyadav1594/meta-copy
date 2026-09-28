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
