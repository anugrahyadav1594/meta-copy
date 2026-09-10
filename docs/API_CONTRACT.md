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
| GET | `/health` | — | `{status:"healthy"}` | no dependencies |
| GET | `/ready` | — | `{status, mode, checks:{canonical_postgres, shards?}}` | `SELECT 1` on canonical and (when enabled) every shard |

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
| POST | `/api/v1/posts` | `{author_id,content,visibility?}` | 201 `PostRead` | validates author exists; routed by author in SHARDED |
| GET | `/api/v1/posts` | `limit` | `PostRead[]` | recency; scatter-gather + merge in SHARDED |
| GET | `/api/v1/posts/{id}` | — | `PostRead` or 404 | targeted (key-aligned id) |
| PATCH | `/api/v1/posts/{id}` | `{content?,visibility?}` | `PostRead` | targeted |
| DELETE | `/api/v1/posts/{id}` | — | 204 | cascades comments/likes |
| GET | `/api/v1/posts/{id}/comments` | `limit` | `CommentRead[]` | co-located with post (targeted in SHARDED) |
| POST | `/api/v1/posts/{id}/comments` | `{user_id,content}` | 201 `CommentRead` | routed by post id |

`UserRead`: `{id,username,email,created_at,updated_at}`.
`PostRead`: `{id,author_id,content,visibility,created_at,updated_at}`.
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
| POST | `/api/v1/sharded/posts` | routed by `author_id`; post id key-aligned to author shard |
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
  -d '{"author_id":1,"content":"hello"}'
curl localhost:8000/api/v1/posts/1

# routing, registry, scatter-gather, health
curl localhost:8000/api/v1/shards/route/user/101
curl localhost:8000/api/v1/shards
curl "localhost:8000/api/v1/sharded/posts?limit=50"
curl -X POST localhost:8000/api/v1/shards/shard-1/health-check
```
