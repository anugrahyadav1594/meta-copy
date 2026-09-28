# MetaScale architecture (integrated system)

Companion to [FINAL_ARCHITECTURE.md](FINAL_ARCHITECTURE.md), which carries
the full status tables, verification results and limitations. This file
describes the shape of the code.

## 1. Layered design

```text
Client
  → FastAPI routes (apps/api/routes)          validation, HTTP contract
  → Service layer (apps/api/services)         business rules, domain events
  → Cache adapter (Member 6)                  cache-aside, TTL, fail-open
  → Repository interface (packages/db/repositories/base.py)
      ├─ NORMALIZED   Canonical*Repository  → PostgreSQL
      └─ SHARDED      Sharded*Repository    → Targeted / Scatter / Cross-shard
                                            → Shard Router (Member 4)
                                            → Replication provider (Member 5)
                                            → shard PostgreSQL
```

Invariants the code holds (not just intentions):

* the shard router has **no** knowledge of the cache — nothing under
  `services/shard-router/` imports `services/cache`;
* the cache sits **above** the repositories — no repository takes a cache;
* callers cannot tell which topology is active: `CanonicalRepository` and
  `ShardedRepository` implement the same eight interfaces.

## 2. One process, six modes

`MODE` selects the layers; no code path is compiled out.

| Mode | Canonical | Read model | Shards | Replicas | Cache |
| --- | :-: | :-: | :-: | :-: | :-: |
| `NORMALIZED` | ✅ | — | — | — | — |
| `DENORMALIZED` | ✅ | ✅ | — | — | — |
| `SHARDED` | ✅ | — | ✅ | — | — |
| `SHARDED_REPLICATED` | ✅ | — | ✅ | ✅ | — |
| `SHARDED_CACHED` | ✅ | — | ✅ | — | ✅ |
| `FULL_DISTRIBUTED` | ✅ | ✅ | ✅ | ✅ | ✅ |

In every shard mode the *generic* endpoints (`/api/v1/users`,
`/api/v1/posts`, …) go through the shard router, so `/api/v1/posts/{id}` and
`/api/v1/sharded/posts/{id}` take the same path — the `/sharded/*` variants
merely expose routing metadata.

## 3. Canonical data vs derived data

PostgreSQL is the only source of truth. Five projections hang off it, all
rebuildable:

| Projection | Module | Written by | Rebuilt by |
| --- | --- | --- | --- |
| `denormalized_post_feed` | Member 3 | event projector | `POST /api/v1/read-model/rebuild` |
| `user_feed` (fan-out) | Member 7 | event projector | `POST /api/v1/feed/rebuild` |
| Social graph adjacency | Member 9 | lazy read + TTL cache | `POST /api/v1/graph/rebuild` |
| Search index | Member 10 | events + full reindex | `POST /api/v1/search/reindex` |
| Media blobs | Member 8 | upload (CAS by SHA-256) | re-upload; metadata stays canonical |

Because rows are placed by **access pattern** (`posts` by `user_id`,
`follows` by `follower_id`, `likes`/`comments` by `post_id`), a rebuild must
scatter the base tables over every shard and join in the API process. Doing
the join inside a shard silently misses rows — see
`packages/db/shard_scan.py`.

## 4. Write path

```text
POST /api/v1/posts
  → PostService.create
      → UserRepository.get_by_id(author)           (targeted on shard(author))
      → PostRepository.create                      (targeted on shard(author))
      → HashtagRepository.tag_post                 (hashtags + post_hashtags)
      → publish POST_CREATED
          → DenormalizedFeedProjector  (upsert into denormalized_post_feed)
          → FeedProjector              (fan-out to every follower, push mode)
          → SearchIndexer              (index the document)
```

A failing projection is counted, never propagated: the write already
committed.

## 5. Read paths

```text
GET /api/v1/posts/{id}
  → cache.get("post:{id}")
      HIT  → return  (x-cache: HIT, no database round trip)
      MISS → repository → shard router → replica or primary → PostgreSQL
           → cache.set(..., ttl=60)  (x-cache: MISS)

GET /api/v1/users/{id}/feed
  pull: follows → candidate posts (scatter-gather) → recency rank
  push: read the derived user_feed table (empty for followers added
        after the write; /api/v1/feed/rebuild fixes it)

GET /api/v1/graph/users/{id}/followers
  → rebuildable adjacency cache over the follows table (TTL 30s)
```

## 6. Failure behaviour

| Dependency down | Effect |
| --- | --- |
| Redis | cache reads become misses; requests still served; `cache_errors` counted (fail-open) |
| A shard | targeted queries to it fail with `SHARD_UNAVAILABLE` (503); scatter-gather reports `unavailable: n` and returns partial results |
| A replica | provider routes reads to the primary and counts `replica_skipped_unhealthy` / `replica_skipped_lag` |
| RabbitMQ | publication falls back to the in-memory bus; writes unaffected |
| OpenSearch | provider reports unreachable; search degrades to the in-memory index if configured |
| MinIO | upload fails loudly — the local filesystem backend is the fallback, and the response always names the backend actually used |

## 7. Observability

`GET /metrics` is the Prometheus scrape target; `GET /api/v1/metrics` is the
same data as JSON. Only measured values appear: request count and latency
histograms (P50/P95/P99), per-layer latency (db / shard routing / cache /
search / feed / media), cache hits and misses, hot keys, events
published/delivered/failed, shard requests and health. `/api/v1/metrics`
mirrors each module's counters into the registry, so the two views agree.
