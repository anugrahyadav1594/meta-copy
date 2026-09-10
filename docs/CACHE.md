# Distributed cache (Member 6)

Redis **cache-aside** in front of the post read path. The cache sits **above**
the repository and shard router; neither knows it exists. PostgreSQL remains
the single source of truth — every cached value is a JSON projection with a
TTL and can be rebuilt on demand.

```text
GET /posts/{id}
      │
      ▼
  CacheProvider.get("post:{id}")
      │ HIT (X-Cache: HIT)                 │ MISS (X-Cache: MISS)
      ▼                                    ▼
  return cached JSON            Repository → Shard Router (M4) → PostgreSQL
                                          │
                                          └── CacheProvider.set(..., TTL)
```

## What is implemented

| Capability | Status | Where |
|---|---|---|
| Async Redis client (`redis.asyncio`, never blocks the event loop) | IMPLEMENTED | `services/cache/cache.py` |
| TTL-based cache-aside on `GET /posts/{id}` | IMPLEMENTED | `apps/api/services/post_service.py` |
| Write invalidation on update/delete | IMPLEMENTED | `PostService.update/delete` |
| Cache metrics (hits/misses, ratio, DB queries avoided, latency, hot keys) | IMPLEMENTED | `GET /api/v1/cache/metrics` |
| Hot-key detection (accesses ≥ `CACHE_HOT_KEY_THRESHOLD`) | IMPLEMENTED | `CacheProvider.get` |
| Graceful degradation (Redis down → fail-open, PostgreSQL keeps serving) | IMPLEMENTED | `CacheProvider.create(fail_open=True)` |
| Redis container with config, healthcheck, ordered startup | IMPLEMENTED | `docker-compose.yml` + `docker-compose.cache.yml` |
| Pub/sub cross-node invalidation | NOT IMPLEMENTED (single API node; invalidation is per-node) | future |

## Running it

### Docker (real Redis 7)

```bash
make cache
# equivalent to:
# docker compose --profile cache \
#   -f docker-compose.yml -f docker-compose.cache.yml up --build
```

This starts PostgreSQL, Redis (`redis:7-alpine`, healthchecked, configured from
`infrastructure/redis/redis.conf`), and the API with `CACHE_ENABLED=true`,
waiting for the Redis healthcheck before the API starts.

Without the overlay, `docker compose up` runs the exact Member 1–4 stack with
**no** Redis dependency; caching is off (`CACHE_ENABLED=false`).

### No Docker (dev/test only)

```bash
make dev-cache   # embedded PostgreSQL + memory:// fakeredis
```

`REDIS_URL=memory://` selects an in-process `fakeredis` emulator. It exists for
local development and the test-suite only — it is **never** a deployment
backend.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/posts/{id}` | Cached read; response header `X-Cache: HIT|MISS|BYPASS` |
| `GET /api/v1/cache/metrics` | Hit/miss counters, ratios, avg latency, hot keys |
| `GET /api/v1/cache/health` | Cache backend reachability (`healthy`/`unavailable`/`disabled`) |
| `GET /api/v1/cache/keys` | List cached `post:*` keys (debugging) |
| `DELETE /api/v1/cache/posts/{id}` | Manual invalidation (developer/demo) |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CACHE_ENABLED` | `false` | Master switch; no Redis connection attempted when false |
| `REDIS_URL` | `redis://redis:6379/0` | Redis DSN; `memory://` = fakeredis (dev/test only) |
| `CACHE_DEFAULT_TTL` | `60` | Seconds a cached post is served before re-reading PostgreSQL |
| `CACHE_HOT_KEY_THRESHOLD` | `5` | Accesses within a process that flag a key as hot |
| `CACHE_CONNECT_TIMEOUT` | `2.0` | Socket connect/IO timeout at startup |
| `CACHE_FAIL_OPEN` | `true` | If false, an unreachable Redis at startup raises instead |

## Correctness rules enforced

1. **Full payloads only.** A cached post contains every `PostRead` field
   (`id, author_id, content, visibility, created_at, updated_at`); timestamps
   are ISO-8601 strings. The original Member 6 draft cached four fields and
   produced a 422 on every HIT — covered now by
   `services/cache/tests/test_cache_api.py`.
2. **Invalidate on every write** to a post (`PATCH`, `DELETE`), so stale data
   is never served after a mutation.
3. **Negative results are not cached** (a missing post always re-checks the
   database).
4. **Redis errors never fail a request**; they increment `cache_errors` and
   fall through to PostgreSQL.
5. The shard router and repositories contain **zero** cache references.

## Tests

```bash
python -m pytest services/cache/tests          # 23 tests, fakeredis only
python -m pytest                                # full suite incl. sharding
```

`services/cache/tests/` covers the provider (TTL, JSON dates, invalidation,
hot keys, fail-open, mid-life Redis failure), the service cache-aside
behaviour with repository call counting, and the HTTP routes including the
`X-Cache` header and observability endpoints.
