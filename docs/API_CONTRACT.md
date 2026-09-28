# API contract

Base prefix `/api/v1` (health/ready are unversioned). The machine-readable
contract is the FastAPI/OpenAPI document served at `/openapi.json` and
browsable at `/docs` (Swagger) and `/redoc`. The table below is the
human-readable companion.

Error envelope (never contains SQL, stack traces, or credentials):

```json
{ "error": "SHARD_UNAVAILABLE", "message": "Target shard 'shard-2' is currently unavailable",
  "shard_id": "shard-2" }
```

Common codes: `NOT_FOUND` 404, `VALIDATION_ERROR` 422, `CONFLICT` 409,
`SHARD_NOT_FOUND` 404, `SHARD_UNAVAILABLE` 503, `ROUTING_ERROR` 500,
`CROSS_SHARD_QUERY_ERROR` 500, `CHECKSUM_MISMATCH` 409,
`NOT_CONFIGURED` 501 (sharding off / future module).

## System

| Method | Path | Request | Response | DB / sharding behavior |
|---|---|---|---|---|
| GET | `/health` | — | `{status, service, mode, uptime_seconds}` | liveness only: touches **no** dependency |
| GET | `/ready` | — | `{status, degraded_optional_dependencies[], mode, derived_systems, checks:{canonical_postgres, shards?, redis, rabbitmq, opensearch, minio, replication}}` | `SELECT 1` on canonical and (when enabled) every shard; optional backends probed with a 2s timeout and reported `disabled` when off |
| GET | `/metrics` | — | Prometheus text | scrape target, unversioned |

## Canonical, mode-aware endpoints

These use the canonical repository in NORMALIZED mode and the sharded
repository in SHARDED mode.

### Users

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| POST | `/api/v1/users` | `{username,email,password,display_name?}` | 201 `UserRead` | hashes password; 409 on duplicate username/email |
| GET | `/api/v1/users` | query `limit,offset` | `UserRead[]` | scatter-gather in SHARDED |
| GET | `/api/v1/users/{id}` | — | `UserRead` or 404 | targeted in SHARDED |
| PATCH | `/api/v1/users/{id}` | `{username?,email?}` | `UserRead` | targeted write |
| DELETE | `/api/v1/users/{id}` | — | 204 | cascades on canonical; targeted in SHARDED |
| GET | `/api/v1/users/{id}/followers` | `limit` | `FollowerRead[]` | single join (canonical) / scatter on `following_id` (sharded) |

### Posts / comments

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| POST | `/api/v1/posts` | `{user_id,content,visibility?}` | 201 `PostRead` | validates author exists; routed by author in SHARDED |
| GET | `/api/v1/posts` | `limit` | `PostRead[]` | recency; scatter-gather + merge in SHARDED |
| GET | `/api/v1/posts/{id}` | — | `PostRead` or 404 | targeted (key-aligned id) |
| PATCH | `/api/v1/posts/{id}` | `{content?,visibility?}` | `PostRead` | targeted |
| DELETE | `/api/v1/posts/{id}` | — | 204 | cascades comments/likes |
| GET | `/api/v1/posts/{id}/comments` | `limit` | `CommentRead[]` | co-located with post (targeted in SHARDED) |
| POST | `/api/v1/posts/{id}/comments` | `{user_id,content}` | 201 `CommentRead` | routed by post id |

`UserRead`: `{id,username,email,created_at,updated_at}`.
`PostRead`: `{id,user_id,content,visibility,created_at,updated_at}`.
`CommentRead`: `{id,post_id,user_id,content,created_at}`.

## Shard admin (SHARDED only; 501 NOT_CONFIGURED otherwise)

| Method | Path | Description / response |
|---|---|---|
| GET | `/api/v1/shards` | registry: strategy, shard_count, per-shard status/host/load/rows/connections/vnodes/is_hot |
| GET | `/api/v1/shards/{id}` | one shard's metadata |
| GET | `/api/v1/shards/stats` | metrics snapshot: per-shard requests/rps/reads/writes/errors/avg/P50/P95/P99/rows/load, routing errors, scatter/cross-shard counters, migration counters, hot shards |
| GET | `/api/v1/shards/distribution?sample_size=&strategy=` | **measured** key distribution across the ring |
| GET | `/api/v1/shards/route/user/{user_id}?strategy=` | route the `users` entity by id; returns shard, vnode, latency |
| GET | `/api/v1/shards/route/{key}?strategy=` | route an arbitrary key (`hash` or `consistent_hash`) |
| POST | `/api/v1/shards/rebalance` | **dev/demo**; body `{new_shard_id?, new_shard_url?, source_shard_id?, table, dry_run}`; runs plan→copy→verify→switch→cleanup |
| POST | `/api/v1/shards/{id}/health-check` | active `SELECT 1` probe; flips HEALTHY/UNAVAILABLE |
| POST | `/api/v1/shards/{id}/simulate-down` | **SIMULATION / dev-demo**; force UNAVAILABLE for failure demos |
| POST | `/api/v1/shards/demo/hot-user/{user_id}?requests=&skew=&concurrency=` | **dev/demo**; generates real skewed targeted traffic and reports per-shard load + detections |

## Sharded data endpoints (caller never chooses a shard)

Responses wrap the row as `{ "data": ..., "routing": {...} }` where routing
contains `shard_contacted`, `strategy`, `entity`, `shard_key`, `key`,
`virtual_node`, `routing_latency_ms`, `database_latency_ms`,
`total_latency_ms`, `query_type`.

| Method | Path | Behavior |
|---|---|---|
| POST | `/api/v1/sharded/users` | writes user + profile to the routed shard; id assigned |
| GET | `/api/v1/sharded/users/{id}` | **targeted**: one shard; 404 if absent |
| PATCH/DELETE | `/api/v1/sharded/users/{id}` | targeted update/delete |
| POST | `/api/v1/sharded/posts` | routed by `user_id`; post id key-aligned to author shard |
| GET | `/api/v1/sharded/posts?limit=` | **scatter-gather**: `{data, timing:{shards_contacted, shard_results[], total_rows, execution_time_ms, merge_time_ms, total_latency_ms, unavailable_shards, query_type}}` |
| GET | `/api/v1/sharded/posts/{id}` | targeted read by key-aligned id |
| PATCH/DELETE | `/api/v1/sharded/posts/{id}` | targeted |
| GET | `/api/v1/sharded/posts/{id}/comments` | targeted (comments co-located with post) |
| GET | `/api/v1/sharded/posts/{id}/cross-shard` | **application-side join** of post + comments + authors; `{post, comments[].author, network_hops, shards_contacted, note}` |

### Examples

```bash
# create / get user
curl -X POST localhost:8000/api/v1/users -H 'Content-Type: application/json' \
  -d '{"username":"ada","email":"ada@example.com","password":"password123"}'
curl localhost:8000/api/v1/users/1

# create / get post
curl -X POST localhost:8000/api/v1/posts -H 'Content-Type: application/json' \
  -d '{"user_id":1,"content":"hello"}'
curl localhost:8000/api/v1/posts/1

# routing, registry, scatter-gather, health
curl localhost:8000/api/v1/shards/route/user/101
curl localhost:8000/api/v1/shards
curl "localhost:8000/api/v1/sharded/posts?limit=50"
curl -X POST localhost:8000/api/v1/shards/shard-1/health-check
```

---

## Integrated system: the other routers

The sections above describe the canonical and shard-admin routers. The
integrated system adds the routers below. All of them live under `/api/v1`
and are listed here exactly as the running application reports them.

### Full endpoint catalogue (generated from the running application's OpenAPI)

**system** — 2 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/health` | Health |
| GET | `/ready` | Ready |

**users** — 10 endpoints

| Method | Path | Summary |
|---|---|---|
| DELETE | `/api/v1/users/{user_id}` | Delete User |
| DELETE | `/api/v1/users/{user_id}/follow/{following_id}` | Unfollow User |
| GET | `/api/v1/users` | List Users |
| GET | `/api/v1/users/{user_id}` | Get User |
| GET | `/api/v1/users/{user_id}/followers` | Followers |
| GET | `/api/v1/users/{user_id}/following` | Following |
| GET | `/api/v1/users/{user_id}/notifications` | Notifications |
| PATCH | `/api/v1/users/{user_id}` | Update User |
| POST | `/api/v1/users` | Create User |
| POST | `/api/v1/users/{user_id}/follow` | Follow User |

**posts** — 11 endpoints

| Method | Path | Summary |
|---|---|---|
| DELETE | `/api/v1/posts/{post_id}` | Delete Post |
| DELETE | `/api/v1/posts/{post_id}/like/{user_id}` | Unlike Post |
| GET | `/api/v1/posts` | Recent Posts |
| GET | `/api/v1/posts/{post_id}` | Get Post |
| GET | `/api/v1/posts/{post_id}/comments` | Comments |
| GET | `/api/v1/posts/{post_id}/hashtags` | Post Hashtags |
| GET | `/api/v1/posts/{post_id}/likes` | Post Likes |
| PATCH | `/api/v1/posts/{post_id}` | Update Post |
| POST | `/api/v1/posts` | Create Post |
| POST | `/api/v1/posts/{post_id}/comments` | Add Comment |
| POST | `/api/v1/posts/{post_id}/like` | Like Post |

**feed** — 4 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/feed/stats` | Feed Stats |
| GET | `/api/v1/feed/strategies` | Describe |
| GET | `/api/v1/users/{user_id}/feed` | Get Feed |
| POST | `/api/v1/feed/rebuild` | Rebuild Feed |

**graph** — 7 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/graph/stats` | Graph Stats |
| GET | `/api/v1/graph/users/{user_id}/degrees` | Degrees |
| GET | `/api/v1/graph/users/{user_id}/followers` | Followers |
| GET | `/api/v1/graph/users/{user_id}/following` | Following |
| GET | `/api/v1/graph/users/{user_id}/mutuals/{other_user_id}` | Mutuals |
| GET | `/api/v1/graph/users/{user_id}/suggestions` | Suggestions |
| POST | `/api/v1/graph/rebuild` | Rebuild Projection |

**search** — 4 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/search` | Search |
| GET | `/api/v1/search/autocomplete` | Autocomplete |
| GET | `/api/v1/search/stats` | Search Stats |
| POST | `/api/v1/search/reindex` | Reindex |

**media** — 5 endpoints

| Method | Path | Summary |
|---|---|---|
| DELETE | `/api/v1/media/{media_id}` | Delete Media |
| GET | `/api/v1/media/` | List Media |
| GET | `/api/v1/media/_stats/backend` | Backend Stats |
| GET | `/api/v1/media/{media_id}` | Download Media |
| POST | `/api/v1/media/upload` | Upload Media |

**cache** — 4 endpoints

| Method | Path | Summary |
|---|---|---|
| DELETE | `/api/v1/cache/posts/{post_id}` | Invalidate Post |
| GET | `/api/v1/cache/health` | Cache Health |
| GET | `/api/v1/cache/keys` | Cache Keys |
| GET | `/api/v1/cache/metrics` | Cache Metrics |

**denormalization** — 3 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/read-model/posts` | List Posts |
| GET | `/api/v1/read-model/stats` | Stats |
| POST | `/api/v1/read-model/rebuild` | Rebuild |

**replication** — 4 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/replication/describe` | Describe |
| GET | `/api/v1/replication/status` | Status |
| POST | `/api/v1/replication/demo/simulate-failover/{shard_id}` | Simulate Failover |
| POST | `/api/v1/replication/promote/{shard_id}` | Promote |

**sharding** — 21 endpoints

| Method | Path | Summary |
|---|---|---|
| DELETE | `/api/v1/sharded/posts/{post_id}` | Sharded Delete Post |
| DELETE | `/api/v1/sharded/users/{user_id}` | Sharded Delete User |
| GET | `/api/v1/sharded/posts` | Sharded Posts Scatter |
| GET | `/api/v1/sharded/posts/{post_id}` | Sharded Get Post |
| GET | `/api/v1/sharded/posts/{post_id}/comments` | Sharded Post Comments |
| GET | `/api/v1/sharded/posts/{post_id}/cross-shard` | Sharded Cross Shard |
| GET | `/api/v1/sharded/users/{user_id}` | Sharded Get User |
| GET | `/api/v1/shards` | List Shards |
| GET | `/api/v1/shards/distribution` | Shard Distribution |
| GET | `/api/v1/shards/route/user/{user_id}` | Route User |
| GET | `/api/v1/shards/route/{key}` | Route Key |
| GET | `/api/v1/shards/stats` | Shard Stats |
| GET | `/api/v1/shards/{shard_id}` | Get Shard |
| PATCH | `/api/v1/sharded/posts/{post_id}` | Sharded Update Post |
| PATCH | `/api/v1/sharded/users/{user_id}` | Sharded Update User |
| POST | `/api/v1/sharded/posts` | Sharded Create Post |
| POST | `/api/v1/sharded/users` | Sharded Create User |
| POST | `/api/v1/shards/demo/hot-user/{user_id}` | Hot User Demo |
| POST | `/api/v1/shards/rebalance` | Rebalance |
| POST | `/api/v1/shards/{shard_id}/health-check` | Health Check |
| POST | `/api/v1/shards/{shard_id}/simulate-down` | Simulate Down |

**observability** — 2 endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/metrics` | Metrics |
| GET | `/api/v1/metrics/summary` | Metrics Summary |

### Conventions worth knowing

* **`x-cache`** — `GET /api/v1/posts/{id}` answers `MISS` or `HIT`; writes
  invalidate `post:{id}`.
* **Derived systems are labelled in their own payloads** —
  `GET /api/v1/graph/stats` returns `derived_projection: true` and
  `source_of_truth: "postgresql:follows"`;
  `POST /api/v1/feed/rebuild` and `/read-model/rebuild` return
  `derived: true`; `POST /api/v1/search/reindex` returns `derived: true`.
* **Simulation is never hidden** —
  `POST /api/v1/replication/demo/simulate-failover/{shard}` returns
  `{"simulated": true, ...}`; real promotion is
  `POST /api/v1/replication/promote/{shard}` (409 when no standby exists).
* **Search results reference canonical ids** (`post_id`, `user_id`), never
  index-local ones, and carry a real BM25 `_score`.
* **Media uploads are idempotent by content**: the same bytes return the same
  `media_id` with `deduplicated: true`.

### Quick curl tour

```bash
# cache MISS then HIT
curl -D - -o /dev/null localhost:8000/api/v1/posts/1 | grep -i x-cache
curl -D - -o /dev/null localhost:8000/api/v1/posts/1 | grep -i x-cache

# graph, feed, search, media, read model
curl localhost:8000/api/v1/graph/users/1/followers
curl "localhost:8000/api/v1/users/1/feed?limit=5"
curl "localhost:8000/api/v1/search?q=sharding"
curl -X POST localhost:8000/api/v1/media/upload -F "owner_id=1" -F "file=@a.png"
curl localhost:8000/api/v1/read-model/stats

# rebuild every derived system from PostgreSQL
curl -X POST localhost:8000/api/v1/feed/rebuild
curl -X POST localhost:8000/api/v1/read-model/rebuild
curl -X POST localhost:8000/api/v1/search/reindex
curl -X POST localhost:8000/api/v1/graph/rebuild

# replication: real status, and the labelled simulation
curl localhost:8000/api/v1/replication/status
curl -X POST localhost:8000/api/v1/replication/demo/simulate-failover/shard-0

# observability
curl localhost:8000/api/v1/metrics      # JSON: counters, P50/P95/P99, cache, events
curl localhost:8000/metrics             # Prometheus
```
