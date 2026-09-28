# RUNBOOK — how to run MetaScale

Status labels used below:

* **VERIFIED** — executed in this repository (Python 3.11, no Docker) while
  writing this runbook; the output quoted is real.
* **DOCKER** — requires a Docker daemon. **Not verified in the sandbox that
  produced this file (no Docker CLI available)**; the compose files are
  syntactically complete and profile-driven, but start them yourself before
  relying on them.

Everything runs as **one API process** on `http://0.0.0.0:8000` under a single
prefix, `/api/v1/`. Interactive docs: `http://localhost:8000/docs`.

---

## 1. Install (VERIFIED)

```bash
make install                     # python3 -m venv .venv && pip install -e ".[dev]" -r requirements.txt
# equivalent, step by step:
python3 -m venv .venv
. .venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]" -r requirements.txt
```

Requirements: Python 3.11+ (the Dockerfile pins 3.12), and for the
no-Docker path the `pgserver` wheel, which bundles PostgreSQL 16 binaries and
creates real clusters under `.pgdata/`.

---

## 2. Run the system (VERIFIED)

### 2.1 Normalized — canonical PostgreSQL only

```bash
make dev
# = .venv/bin/python scripts/dev_server.py --reset --seed small --port 8000
```

### 2.2 Sharded — 4 shards (+1 spare), seeded through the shard router

```bash
make dev-sharded
# = .venv/bin/python scripts/dev_server.py --sharded --reset --seed small --sharded-seed --port 8000
```

### 2.3 Full distributed (recommended demo) — sharding + cache + derived systems

```bash
MODE=FULL_DISTRIBUTED CACHE_ENABLED=true REDIS_URL=memory:// DENORMALIZED_ENABLED=true \
  .venv/bin/python scripts/dev_server.py --sharded --seed small --sharded-seed --port 8000
```

`REDIS_URL=memory://` selects the **fakeredis** backend: dev/test ONLY. For a
real cache, run Redis and set `REDIS_URL=redis://localhost:6379/0`.

Restart without re-seeding (data persists in `.pgdata/dev/`):

```bash
MODE=FULL_DISTRIBUTED CACHE_ENABLED=true REDIS_URL=memory:// DENORMALIZED_ENABLED=true \
  .venv/bin/python scripts/dev_server.py --sharded --port 8000
```

Observed startup on seeded data: canonical seed 2.5 s, shard seed 2.8 s, API
ready in ~7 s total; shards received 3 628 / 5 192 / 4 882 / 6 698 rows for
the `small` dataset.

### 2.4 Feed strategy

```bash
FEED_STRATEGY=push   # fan-out-on-write: reads the derived user_feed table
FEED_STRATEGY=pull   # default: follow graph -> candidate posts -> rank
```

With `push`, rebuild the fan-out table after seeding:
`curl -X POST localhost:8000/api/v1/feed/rebuild`.

### 2.5 Docker (DOCKER, unverified here)

```bash
make up                                   # canonical PostgreSQL + API (NORMALIZED)
make sharding                             # + 4 shard containers (profile "sharding")
make cache                                # + Redis (profile "cache")
docker compose --profile sharding --profile cache --profile observability up -d
docker compose --profile messaging up -d   # RabbitMQ (EVENT_BUS=rabbitmq)
docker compose --profile search up -d      # OpenSearch (SEARCH_PROVIDER=opensearch)
docker compose --profile media up -d       # MinIO (MEDIA_BACKEND=minio)
docker compose config                      # validate before starting
make logs / make down / make reset
```

---

## 3. Configuration (all via environment; no credentials in source)

| Variable | Meaning |
| --- | --- |
| `MODE` | `NORMALIZED` · `DENORMALIZED` · `SHARDED` · `SHARDED_REPLICATED` · `SHARDED_CACHED` · `FULL_DISTRIBUTED` |
| `DATABASE_URL` | canonical PostgreSQL (source of truth) |
| `SHARD_0_URL` … `SHARD_4_URL` | independent shard clusters |
| `SHARDING_ENABLED`, `SHARD_COUNT` | force sharding on/off, number of active shards |
| `REDIS_URL` | Redis; `memory://` = fakeredis (dev/test only) |
| `CACHE_ENABLED`, `CACHE_DEFAULT_TTL`, `CACHE_HOT_KEY_THRESHOLD` | Member 6 cache |
| `RABBITMQ_URL`, `EVENT_BUS` | `memory` (default) · `rabbitmq` · `off` |
| `OPENSEARCH_URL`, `SEARCH_PROVIDER` | `memory` (default) · `opensearch` |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MEDIA_BACKEND` | `local` (default) · `minio` |
| `DENORMALIZED_ENABLED`, `FEED_STRATEGY` | derived read model, feed strategy |
| `SHARD_n_REPLICA_URL`, `REPLICATION_ENABLED` | read replicas per shard |

Copy `.env.example` to `.env` and edit; nothing secret is committed.

---

## 4. Health, readiness, metrics

```bash
curl localhost:8000/health        # process alive
curl localhost:8000/ready         # dependencies reachable (canonical PG, each shard)
curl localhost:8000/api/v1/metrics        # JSON: counters, P50/P95/P99, cache, events, shards
curl localhost:8000/metrics               # Prometheus exposition (scrape target)
python3 scripts/healthcheck.py            # same probes, scripted (make health)
```

`/ready` reports `canonical_postgres` and `shards`; optional dependencies that
are disabled never fail the app.

---

## 5. The end-to-end demo chain (VERIFIED)

```bash
# 1 create users
A=$(curl -sS -X POST localhost:8000/api/v1/users -H 'content-type: application/json' \
     -d '{"username":"alice","email":"alice@example.com","password":"password123"}' | jq -r .user_id)
B=$(curl -sS -X POST localhost:8000/api/v1/users -H 'content-type: application/json' \
     -d '{"username":"bob","email":"bob@example.com","password":"password123"}' | jq -r .user_id)

# 2 follow (graph edge, real PostgreSQL row)
curl -sS -X POST localhost:8000/api/v1/users/$B/follow -H 'content-type: application/json' \
  -d "{\"following_id\":$A}"

# 3 create a post  -> 4 see which shard owns the user
P=$(curl -sS -X POST localhost:8000/api/v1/posts -H 'content-type: application/json' \
     -d "{\"user_id\":$A,\"content\":\"distributed caching demo #metascale\"}" | jq -r .post_id)
curl -sS localhost:8000/api/v1/shards/route/user/$A

# 5 read it twice: X-Cache goes MISS then HIT
curl -sS -D - -o /dev/null localhost:8000/api/v1/posts/$P | grep -i x-cache
curl -sS -D - -o /dev/null localhost:8000/api/v1/posts/$P | grep -i x-cache

# 6 comment + like (counters are real, not assumed)
curl -sS -X POST localhost:8000/api/v1/posts/$P/comments -H 'content-type: application/json' \
  -d "{\"user_id\":$B,\"content\":\"nice\"}"
curl -sS -X POST localhost:8000/api/v1/posts/$P/like -H 'content-type: application/json' \
  -d "{\"user_id\":$B}"

# 7 feed     8 graph     9 search     10 hashtags
curl -sS "localhost:8000/api/v1/users/$B/feed?limit=5"
curl -sS localhost:8000/api/v1/graph/users/$A/followers
curl -sS "localhost:8000/api/v1/search?q=metascale"
curl -sS localhost:8000/api/v1/posts/$P/hashtags

# 11 media (SHA-256 content addressing; upload twice -> same media_id)
curl -sS -X POST localhost:8000/api/v1/media/upload -F "owner_id=$A" -F "file=@a.png"
curl -sS -X POST localhost:8000/api/v1/media/upload -F "owner_id=$A" -F "file=@a.png"

# 12 derived systems are rebuildable, never authoritative
curl -sS -X POST localhost:8000/api/v1/search/reindex
curl -sS -X POST localhost:8000/api/v1/feed/rebuild          # needs FEED_STRATEGY=push
curl -sS -X POST localhost:8000/api/v1/read-model/rebuild
curl -sS -X POST localhost:8000/api/v1/graph/rebuild

# 13 replication: real status vs clearly labelled simulation
curl -sS localhost:8000/api/v1/replication/status
curl -sS -X POST localhost:8000/api/v1/replication/demo/simulate-failover/shard-0   # SIMULATED
```

Measured on seeded data: feed rebuild → 38 627 rows, search reindex → 2 103
documents, read-model rebuild → 2 003 rows, `GET /metrics` → 79 Prometheus
metric lines.

---

## 6. Tests, lint, format (VERIFIED)

```bash
make test                       # 138 passed
make test-unit                  # unit only (no database needed)
make test-integration           # integration + contract (starts embedded PostgreSQL)
make lint                       # ruff + black --check   -> clean
make format                     # black + ruff --fix
```

Direct invocation (what `make test` runs):

```bash
.venv/bin/python -m pytest tests services/shard-router/tests services/cache/tests
```

The suite starts real PostgreSQL 16 clusters under `.pgdata/test/` (via
`pgserver`) unless `SHARD_n_URL` / `DATABASE_URL` are set externally, in which
case those are used.

---

## 7. Benchmarks

```bash
make benchmark-routing          # hash vs consistent-hash, no database required
make benchmark-db               # scaling scenarios against embedded PG or SHARD_n_URL
```

Benchmarks emit real JSON/CSV under `benchmarks/results/`. Nothing is
fabricated: every number comes from work that was actually executed.

---

## 8. Migrations and seeding

```bash
make migrate                    # alembic upgrade head (canonical)
make shard-init                 # create schema on live shards + regenerate shard DDL
make shard-sql                  # regenerate per-shard first-boot SQL from the ORM
make seed-small | seed-medium | seed-large | shard-seed | shard-seed-both
```

No-Docker equivalents:

```bash
.venv/bin/python scripts/seed.py --size small
.venv/bin/python scripts/seed.py --sharded --size small
```

---

## 9. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `XDG_RUNTIME_DIR is not set` warning | harmless `pgserver`/`platformdirs` warning |
| `memory:// cache requires fakeredis` | `pip install -e ".[dev]"` (dev extra) |
| `/api/v1/feed/rebuild` → 400 | projector only exists when `FEED_STRATEGY=push` |
| search returns nothing for a new post | index is event-driven; wait for `POST_CREATED` or call `/api/v1/search/reindex` |
| derived feed/read model empty in SHARDED mode | run the rebuild endpoints — they scatter over the shards, a plain canonical query finds nothing |
| a shard is down | `/api/v1/shards/health`; the router skips it and records `unavailable` in metrics |
| port 8000 already in use | `--port 8001` |

---

## 10. Known limitations

* Streaming replication: the `ShardConnectionProvider` (read/write split,
  health, lag, promotion) is implemented and compose ships replica
  containers, but **it was not verified end-to-end here** (no Docker CLI).
  The `/api/v1/replication/demo/simulate-failover` endpoint is explicitly
  labelled `simulated: true` — it issues no PostgreSQL command.
* Projection rebuilds load the base tables into the API process and join in
  Python, because rows are placed by access pattern (`posts` by `user_id`,
  `follows` by `follower_id`, `likes`/`comments` by `post_id`) and a join
  inside a shard would miss rows. Fine for the seeded datasets; a production
  system would stream this through a batch job.
* Cache, search and blob storage default to in-process/in-filesystem backends
  (`fakeredis`, in-memory inverted index, local directory). They are real
  implementations behind the documented interfaces, not the deployment
  backends.
* OpenSearch, RabbitMQ and MinIO adapters are implemented behind their
  interfaces but were exercised only by unit tests and config checks here.
