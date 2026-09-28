# API Contract — MetaScale integrated system

Single FastAPI application, single versioned prefix: **`/api/v1`**.
This file is generated from the running service's OpenAPI document
(`GET /openapi.json`) plus the conventions that OpenAPI cannot express.

> Educational/research project inspired by publicly discussed database and
> infrastructure concepts. Not affiliated with, endorsed by, or a reproduction
> of Meta's proprietary systems.

## Conventions

| Concern | Rule |
| --- | --- |
| Prefix | every endpoint lives under `/api/v1` except the unversioned operational routes `/health`, `/ready`, `/metrics` |
| Request id | `X-Request-ID` is generated per request (or echoed from the client) and returned on every response; the same id appears in structured logs and in every change-stream record |
| Cache signal | `GET /api/v1/posts/{post_id}` returns `X-Cache: HIT \| MISS \| BYPASS` — the UI shows this header, it never guesses |
| Errors | `{"error": "CODE", "message": "...", "details": {}}` with `4xx` for client errors, `5xx` never leaking SQL or stack traces |
| Ids | 64-bit application-generated ids (`user_id`, `post_id`, `comment_id`, `like_id`, `media_id`, `notification_id`, `hashtag_id`); the follow table uses the composite key `(follower_id, following_id)` |
| Timestamps | ISO-8601 with timezone (UTC) |
| Derived data | any response built from a projection carries `"derived": true` and always includes the canonical ids |
| Secrets | `password_hash` never appears in any response, including the database explorer and the change stream |

## Health vocabulary

`GET /api/v1/health` probes **every** dependency the deployment declares and
classifies each component as `healthy`, `degraded` or `unavailable`:

* **required** — the API process, canonical PostgreSQL and every configured
  shard. A required component that is not healthy makes the whole system
  `unavailable`.
* **optional / fail-open** — Redis, RabbitMQ, OpenSearch, MinIO, replication.
  Enabled but unreachable ⇒ `degraded`. Never configured ⇒ reported
  `unavailable` with `detail: "not configured"` (this is by design and does not
  degrade the system).
* Overall: `unavailable` if any required component is down, else `degraded` if
  an optional one is, else `healthy`.

## Routes

**/api/v1/benchmarks**
  `GET /api/v1/benchmarks/architectures`
  `GET /api/v1/benchmarks/operations`
  `POST /api/v1/benchmarks/compare`
  `POST /api/v1/benchmarks/run`
**/api/v1/cache**
  `DELETE /api/v1/cache/posts/{post_id}`
  `GET /api/v1/cache/health`
  `GET /api/v1/cache/keys`
  `GET /api/v1/cache/metrics`
**/api/v1/database**
  `DELETE /api/v1/database/changes`
  `GET /api/v1/database/changes`
  `GET /api/v1/database/changes/stats`
  `GET /api/v1/database/overview`
  `GET /api/v1/database/tables`
  `GET /api/v1/database/tables/{table}/rows`
**/api/v1/events**
  `GET /api/v1/events`
  `GET /api/v1/events/recent`
  `GET /api/v1/events/types`
**/api/v1/feed**
  `GET /api/v1/feed/stats`
  `GET /api/v1/feed/strategies`
  `POST /api/v1/feed/rebuild`
**/api/v1/health**
  `GET /api/v1/health`
**/api/v1/media**
  `DELETE /api/v1/media/{media_id}`
  `GET /api/v1/media/`
  `GET /api/v1/media/_stats/backend`
  `GET /api/v1/media/{media_id}`
  `POST /api/v1/media/upload`
**/api/v1/metrics**
  `GET /api/v1/metrics`
  `GET /api/v1/metrics/summary`
**/api/v1/observability**
  `GET /api/v1/observability/latency`
  `GET /api/v1/observability/metrics`
  `GET /api/v1/observability/status`
**/api/v1/posts**
  `DELETE /api/v1/posts/{post_id}`
  `DELETE /api/v1/posts/{post_id}/like/{user_id}`
  `GET /api/v1/posts`
  `GET /api/v1/posts/{post_id}`
  `GET /api/v1/posts/{post_id}/comments`
  `GET /api/v1/posts/{post_id}/hashtags`
  `GET /api/v1/posts/{post_id}/likes`
  `PATCH /api/v1/posts/{post_id}`
  `POST /api/v1/posts`
  `POST /api/v1/posts/{post_id}/comments`
  `POST /api/v1/posts/{post_id}/like`
**/api/v1/read-model**
  `GET /api/v1/read-model/posts`
  `GET /api/v1/read-model/stats`
  `POST /api/v1/read-model/rebuild`
**/api/v1/replication**
  `GET /api/v1/replication/describe`
  `GET /api/v1/replication/status`
  `POST /api/v1/replication/demo/simulate-failover/{shard_id}`
  `POST /api/v1/replication/promote/{shard_id}`
**/api/v1/search**
  `GET /api/v1/search`
  `GET /api/v1/search/autocomplete`
  `GET /api/v1/search/stats`
  `POST /api/v1/search/reindex`
**/api/v1/sharded**
  `DELETE /api/v1/sharded/posts/{post_id}`
  `DELETE /api/v1/sharded/users/{user_id}`
  `GET /api/v1/sharded/posts`
  `GET /api/v1/sharded/posts/{post_id}`
  `GET /api/v1/sharded/posts/{post_id}/comments`
  `GET /api/v1/sharded/posts/{post_id}/cross-shard`
  `GET /api/v1/sharded/users/{user_id}`
  `PATCH /api/v1/sharded/posts/{post_id}`
  `PATCH /api/v1/sharded/users/{user_id}`
  `POST /api/v1/sharded/posts`
  `POST /api/v1/sharded/users`
**/api/v1/shards**
  `GET /api/v1/shards`
  `GET /api/v1/shards/distribution`
  `GET /api/v1/shards/route/user/{user_id}`
  `GET /api/v1/shards/route/{key}`
  `GET /api/v1/shards/stats`
  `GET /api/v1/shards/{shard_id}`
  `POST /api/v1/shards/demo/hot-user/{user_id}`
  `POST /api/v1/shards/rebalance`
  `POST /api/v1/shards/{shard_id}/health-check`
  `POST /api/v1/shards/{shard_id}/simulate-down`
**/api/v1/users**
  `DELETE /api/v1/users/{user_id}`
  `DELETE /api/v1/users/{user_id}/follow/{following_id}`
  `GET /api/v1/users`
  `GET /api/v1/users/{user_id}`
  `GET /api/v1/users/{user_id}/feed`
  `GET /api/v1/users/{user_id}/followers`
  `GET /api/v1/users/{user_id}/following`
  `GET /api/v1/users/{user_id}/notifications`
  `PATCH /api/v1/users/{user_id}`
  `POST /api/v1/users`
  `POST /api/v1/users/{user_id}/follow`
**/health**
  `GET /health`
**/ready**
  `GET /ready`

## Payload notes for the integration surface

### `GET /api/v1/database/changes`

One record per **canonical write** performed by this process:

```json
{
  "change_id": "chg-00000007",
  "operation": "INSERT",
  "table": "posts",
  "primary_key": {"post_id": 380155530590552066},
  "shard": "shard-0",
  "before": null,
  "after": {"post_id": 380155530590552066, "user_id": 380155530179510272, "content": "..."},
  "timestamp": "2026-09-28T22:55:41.442Z",
  "request_id": "b129f6ecfa90",
  "service": "api",
  "latency_ms": 4.1
}
```

* `before` is read back for single-key UPDATE/DELETE where the repository
  exposes `get_by_id`; composite-key tables report the key only.
* The buffer is bounded (`CHANGES_ENABLED`, `CHANGE_LOG_CAPACITY`, default
  1000) and lives in the process: it is cleared on restart, and the response
  says so (`stats.scope`).
* `DELETE /api/v1/database/changes` clears it; it never touches the database.

### `GET /api/v1/events/recent`

```json
{
  "event_id": "94f40e799d5a485981f972991c8b61a4",
  "event_type": "FOLLOW_CREATED",
  "entity_type": "user",
  "entity_id": 380155530179510272,
  "timestamp": "2026-09-28T22:55:41.503Z",
  "payload": {"follower_id": 380155530506665984, "following_id": 380155530179510272},
  "source": "api",
  "shard": null
}
```

With `EVENT_BUS=rabbitmq` the events really leave the process, so
`retained: false` is returned instead of a fabricated history.

### `POST /api/v1/benchmarks/run?operation=…&iterations=…`

Measured at call time. `cached_read` reports both phases:

```json
{
  "operation": "cached_read",
  "cold": {"p50_ms": 1.501, "p95_ms": 2.1, "count": 40},
  "warm": {"p50_ms": 0.122, "p95_ms": 0.31, "count": 40},
  "cache_hits": 27,
  "cache_misses": 27,
  "cache_backend": "memory"
}
```

Unavailable paths return `{"available": false, "reason": "..."}` — never a
guessed number.
