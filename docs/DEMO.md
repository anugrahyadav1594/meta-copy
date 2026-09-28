# DEMO — the 20-step faculty walkthrough

Every step below runs against the **live system**. There is no slideware here:
the script `scripts/demo.py` issues real HTTP requests and prints the real
responses. If a step breaks, the script stops with a non-zero exit code.

```bash
# 1. start the system (no Docker required)
MODE=FULL_DISTRIBUTED CACHE_ENABLED=true REDIS_URL=memory:// DENORMALIZED_ENABLED=true \
  FEED_STRATEGY=push .venv/bin/python scripts/dev_server.py --sharded --seed small --sharded-seed --port 8000

# 2. run the demo (add --pause 1.5 to slow it down while you narrate)
make demo                                  # or: .venv/bin/python scripts/demo.py
.venv/bin/python scripts/demo.py --base-url http://192.168.1.10:8000
```

Status: **VERIFIED** — all 20 steps executed successfully against a running
instance (`DEMO COMPLETE — 20 steps`). Output quoted below is from a real run.

---

## Step 1 — Liveness (`GET /health`)

The process is up. It touches no dependency, so a database outage can never
cause a restart loop.

```json
{"status": "healthy", "service": "metascale-api", "mode": "FULL_DISTRIBUTED", "uptime_seconds": 4.868}
```

## Step 2 — Readiness (`GET /ready`)

Which layers are active and which dependencies are reachable. Optional
backends report `disabled` and never fail the check.

```json
{"canonical_postgres": "ready",
 "shards": {"shard-0": "ready", "shard-1": "ready", "shard-2": "ready", "shard-3": "ready"},
 "redis": "ready",
 "rabbitmq": "disabled (event_bus=memory)",
 "opensearch": "disabled (search_provider=memory)",
 "minio": "disabled (media_backend=local_filesystem)",
 "replication": {"active": false, "per_shard": {...}}}
```

## Step 3 — Create users (`POST /api/v1/users`)

Writes go to PostgreSQL. In `SHARDED` mode the row is placed by the shard
router; the server log shows the decision:

```json
{"operation":"targeted_write","entity":"users","key":"380100900397514752",
 "shard_id":"shard-2","strategy":"consistent_hash","routing_latency_ms":0.0206,
 "database_latency_ms":17.859}
```

## Step 4 — Shard routing (`GET /api/v1/shards/route/user/{id}`)

Ask the router where a user lives and why: `shard_id`, `strategy`
(consistent hash + 150 virtual nodes per shard), `routing_latency_ms`.

## Step 5 — Create a post + hashtags (`POST /api/v1/posts`, `GET /posts/{id}/hashtags`)

`#metascale` and `#demo` are extracted into the 3NF vocabulary
(`hashtags` + `post_hashtags`), not concatenated into a text column.

## Step 6/7 — Cache-aside, MISS then HIT (`GET /api/v1/posts/{id}`)

* first read → `x-cache: MISS`, row loaded through the repository
* second read → `x-cache: HIT`, PostgreSQL not touched, payload identical

The cache sits **above** the repository and **below** the service; the shard
router has no idea a cache exists.

## Step 8 — Cache metrics (`GET /api/v1/cache/metrics`)

Real counters: `cache_hits`, `cache_misses`, `hit_ratio`,
`db_queries_avoided`, `hot_keys` (a key crossing the access threshold is
logged as `cache_hot_key`).

## Step 9 — Write invalidates (`PATCH /api/v1/posts/{id}`)

The update deletes `post:{id}`; the next read is a `MISS` and returns the new
content. Log line: `cache_invalidated key=post:380100902570164227`.

## Step 10/11/12 — Comments and likes

* `POST /posts/{id}/comments` → `comment_id`
* `POST /posts/{id}/like` twice → `like_count` stays `1` (UNIQUE
  `(post_id, user_id)`; the API is idempotent instead of raising)
* `GET /posts/{id}/likes` and `/comments` read the counters back from
  PostgreSQL — never from the cache

## Step 13 — Follows (`POST /api/v1/users/{id}/follow`)

`{"created": true}` the first time, `{"created": false}` on repeat; the
`FOLLOW_CREATED` domain event is published only for a genuinely new edge.

## Step 14 — Follow relationships (canonical `follows` table)

`followers` and `following` are read from the **canonical `follows` table**.
There is no separate graph store in this iteration: Member 9's graph projection
is excluded from the integrated system and preserved under
`legacy/member9-graph/`.

```bash
GET /api/v1/users/{id}/followers   # -> [{"follower_id": …, "following_id": …, "created_at": …}]
GET /api/v1/users/{id}/following
```

Relationships are plain rows scattered over the shards, so the endpoint
scatter-gathers like any other unkeyed read. Nothing is precomputed, and there
is no second store to fall out of sync with PostgreSQL.

## Step 15 — Feed: pull vs fan-out-on-write (`GET /api/v1/users/{id}/feed`)

With `FEED_STRATEGY=push` the demo shows the classic trade-off: bob followed
alice **after** the post was written, so his fan-out table is empty —
fan-out-on-write only reaches followers that exist at write time.

* `GET /api/v1/feed/strategies` → `{"strategy": "push", "push_table_is_derived": true}`
* bob's feed → `count: 0`
* `POST /api/v1/feed/rebuild` → `{"rebuilt": true, "rows": N, "derived": true}`
* bob's feed again → the post, `"source": "push_projection"`

With `FEED_STRATEGY=pull` (default) the same endpoint scatter-gathers the
followed authors' posts and ranks by recency — no derived table at all.

## Step 16 — Search, derived (`GET /api/v1/search?q=`, `/autocomplete?prefix=`)

`POST /api/v1/search/reindex` rebuilds the index from PostgreSQL (scattering
over the shards when sharding is on). Results carry canonical `post_id` /
`user_id` and a real BM25 score:

```json
{"post_id": 380100902570164227, "author_username": "demo_alice_...",
 "tags": ["metascale"], "_score": 0.182322}
```

## Step 17 — Media: SHA-256 content addressing (`POST /api/v1/media/upload`)

Upload the same bytes twice:

* first upload → `"deduplicated": false`, blob written
* second upload → **same `media_id`**, `"deduplicated": true`, no second blob
* `GET /api/v1/media/_stats/backend` →

```json
{"backend": "local_filesystem", "uploads": 1, "deduplicated_uploads": 1, "blobs": 3,
 "note": "local filesystem adapter — NOT object storage (MinIO is optional)"}
```

PostgreSQL stores metadata + checksum only; the blob is addressed by its
SHA-256 (`14/52/1452f17e....png`).

## Step 18 — Denormalized read model (`/api/v1/read-model/*`)

`stats` → `{"derived": true, "events_applied": N, "errors": 0}`;
`rebuild` recomputes every row from canonical + shards; `posts` reads the
single denormalized table with no joins.

## Step 19 — Sharding internals

`GET /api/v1/shards` (per-shard status and row counts),
`GET /api/v1/shards/distribution` (key distribution over the ring),
`GET /api/v1/shards/stats` (`total_requests`, `healthy_shards`, `hot_shards`,
p50/p95/p99 routing and database latency),
`GET /api/v1/shards/route/{key}` (route any raw key),
`POST /api/v1/shards/rebalance` (online 4 → 5 migration with row
verification).

## Step 20 — Replication and observability: real vs labelled

`GET /api/v1/replication/status` reports what is genuinely configured
(`replication_active: false` when no `SHARD_n_REPLICA_URL` is set, plus
per-shard `read_routing`). Then:

```json
POST /api/v1/replication/demo/simulate-failover/shard-2
{"simulated": true, "steps": ["detect primary unhealthy",
  "SELECT pg_promote() on the standby  [NOT EXECUTED IN SIMULATION]", ...],
 "note": "SIMULATION: no PostgreSQL command is issued and routing is unchanged."}
```

Real promotion is `POST /api/v1/replication/promote/{shard_id}`, which
refuses (409) when no physical standby is configured.

Finally `GET /api/v1/metrics` (JSON: counters, P50/P95/P99, cache, events,
search, read model, shards) and `GET /metrics` (Prometheus exposition —
~27 `metascale_*` metric families, all measured).

---

## Optional: the same demo on Docker

```bash
MODE=FULL_DISTRIBUTED CACHE_ENABLED=true DENORMALIZED_ENABLED=true \
  docker compose --profile distributed up -d --build
make demo
```

Grafana (`localhost:3000`) and Prometheus (`localhost:9090`) scrape
`GET /metrics` when the `observability` profile is enabled.

## If a step fails

The script prints the failing method, path, status code and response body,
then exits non-zero. The usual causes are in the troubleshooting table in
[RUNBOOK.md](RUNBOOK.md) — most often: the API is not running, or
`FEED_STRATEGY=push` was expected while the server started with `pull`.
