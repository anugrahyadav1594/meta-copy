<<<<<<< HEAD
# MetaScale — Member 1: Database Schema & Normalization

This module contains the **canonical relational database** for the MetaScale project.

MetaScale is a social-media database project used to study how a normalized relational design can later be optimized using:

- denormalization
- sharding
- replication
- caching
- feed generation
- search/indexing
- distributed services

## What this module owns

Member 1 owns the source-of-truth relational model:

1. Database tables
2. Primary keys
3. Foreign keys
4. Unique constraints
5. Check constraints
6. Indexes
7. 3NF-oriented design
8. Seed/demo data
9. ER diagram
10. Database documentation

The later modules should treat this schema as the **canonical source of truth**.

---

## Recommended stack

- PostgreSQL
- SQL migrations
- Docker/PostgreSQL later through the main project

No frontend is required for this module.

---

## Folder structure

```text
metascale-member1/
├── README.md
├── .gitignore
├── docs/
│   ├── schema.md
│   ├── normalization.md
│   └── er-diagram.md
└── database/
    ├── 001_schema.sql
    ├── 002_indexes.sql
    └── 003_seed.sql
```

---

## How to run

### 1. Create the database

```sql
CREATE DATABASE metascale;
```

Then connect to it:

```bash
psql -U postgres -d metascale
```

### 2. Run schema

```bash
psql -U postgres -d metascale -f database/001_schema.sql
```

### 3. Run indexes

```bash
psql -U postgres -d metascale -f database/002_indexes.sql
```

### 4. Load demo data

```bash
psql -U postgres -d metascale -f database/003_seed.sql
```

---

## Design principle

The database is intentionally **normalized first**.

For example, a post stores only:

```text
user_id
caption
created_at
```

It does not repeatedly store:

```text
username
email
user_name
profile information
```

because those values belong to the user/profile records.

This reduces update anomalies and keeps one authoritative copy of each fact.

---

## How teammates should build on this

Do not directly redesign these tables inside another module.

Use this database as the source of truth.

Examples:

- Member 2 can optimize queries using indexes and `EXPLAIN ANALYZE`.
- Member 3 can create denormalized read tables/materialized views.
- Member 4 can shard tables using a user-based routing strategy.
- Member 5 can configure PostgreSQL replication.
- Member 6 can cache frequently-read records.
- Member 7 can build feed tables/services.
- Member 8 can store media externally and keep only metadata here.
- Member 9 can create a social-graph access layer.
- Member 10 can index searchable text externally.

---

## Suggested Git workflow

Each member should create a branch:

```bash
git checkout -b feature/member-1-schema
```

After completing work:

```bash
git add .
git commit -m "feat(database): add normalized schema and seed data"
git push -u origin feature/member-1-schema
```

Then create a Pull Request into `main`.

The team should not all push directly to `main`.

---

## Viva statement

> "My responsibility is to build the canonical normalized relational database. I first identify the entities and relationships of the social-media system, then convert them into tables with primary and foreign keys. The design follows normalization up to 3NF so that each fact has a clear owner and redundancy is minimized. This database becomes the source of truth for the rest of the distributed architecture."
=======
# MetaScale

**An educational, distributed social-media database platform.**

MetaScale is an academic project that builds a realistic, fully runnable
backend demonstrating how very large social platforms structure their data
layer: a normalized relational source of truth, horizontal **sharding**,
replication-ready seams, cache-aside seams, denormalized read-model seams,
graph/feed/media/search projections, and observability.

> MetaScale is an **independent educational/research project** inspired by
> publicly discussed distributed database and storage concepts. It is **not
> affiliated with, sponsored by, or an official implementation of Meta,
> Facebook, or Instagram**, and it does not reproduce any proprietary
> infrastructure. Architecture components are described as **Meta-inspired**,
> **TAO-inspired**, or **Haystack-inspired** where applicable.

This milestone ships:

1. The **shared foundation** every team member builds on — one canonical
   normalized PostgreSQL schema, a deterministic mock dataset, repository
   abstractions, and extension points for future modules.
2. The complete **Member 4 — Database Sharding (ShardRouter)** module:
   deterministic hash routing, a consistent-hash ring with virtual nodes,
   independent per-shard PostgreSQL connection pools, targeted queries,
   scatter-gather, an application-side cross-shard join, hot-shard detection,
   a celebrity-workload generator, an online rebalancing workflow with
   checksum verification, shard health checks, metrics, and real benchmarks.

---

## Architecture

```text
                    ┌──────────────────────┐
                    │       Client         │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │   FastAPI REST API   │  /api/v1
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │    Service Layer     │
                    └──────────┬───────────┘
                               ▼
        (future) ┌─►  Cache (Redis, M6)   │  cache miss
                 │    └─────────┬─────────┘
                    ┌──────────┴───────────┐
                    │   Repository (iface) │  ◄── mode-agnostic
                    └──────────┬───────────┘
              NORMALIZED       │        SHARDED
                 │             ▼             │
                 │   ┌──────────────────┐    │
                 │   │  Shard Router M4 │    │
                 │   └───────┬──────────┘    │
                 │   ┌───────┼──────────┐    │
                 ▼   ▼       ▼          ▼    ▼
        ┌──────────────┐  shard-0 … shard-N (independent PostgreSQL)
        │  Canonical   │   │   (future replica selection = Member 5)
        │  PostgreSQL  │
        └──────────────┘
```

PostgreSQL is always the **source of truth**. Redis, search indexes, graph
projections, feeds, and caches are derived systems and never become canonical.

### Two deployment modes

| Mode | Status | Description |
|------|--------|-------------|
| `NORMALIZED` | **IMPLEMENTED** | single canonical PostgreSQL instance |
| `SHARDED` | **IMPLEMENTED** | N independent PostgreSQL shards + ShardRouter |
| `DENORMALIZED` | reserved (Member 3) | enum exists, behavior not built |
| `SHARDED_REPLICATED` | reserved (Member 5) | provider seam exists |
| `SHARDED_CACHED` | reserved (Member 6) | cache seam exists |
| `FULL_DISTRIBUTED` | reserved | all members |

---

## Technology stack

- Python 3.11+/3.12, FastAPI, Pydantic v2
- SQLAlchemy 2.x (async), psycopg 3, Alembic
- PostgreSQL 16 (single canonical instance + independent shard containers)
- Redis/RabbitMQ/OpenSearch/MinIO/Prometheus/Grafana as **profiled placeholders**
- Docker + Docker Compose
- pytest / pytest-asyncio / HTTPX, Ruff, Black, MyPy

No heavy frameworks are pulled in; the deterministic hash is a self-contained
MurmurHash3 implementation, and password hashing uses stdlib PBKDF2.

---

## Project structure

```text
metascale/
├── apps/api/                 FastAPI app: main, dependencies, routes, services
├── services/shard-router/    Member 4: router, metadata, health, queries,
│                             rebalance, hotspots, metrics
├── packages/
│   ├── common/               config (Pydantic Settings), enums, exceptions, logging
│   ├── models/               ONE canonical SQLAlchemy schema (shared by all)
│   ├── schemas/              Pydantic request/response models
│   ├── db/                   engines, sessions, shard engine manager, repositories
│   └── events/               event contracts + seams for future members
├── infrastructure/postgres/  init SQL (canonical) + generated per-shard DDL
├── migrations/               Alembic
├── scripts/                  seed, reset, healthcheck, initialize_shards, dev_server
├── benchmarks/sharding/      hash, consistent-hash, and A–E scaling benchmarks
├── tests/                    unit / integration / contract (+ embedded PG)
├── docs/                     ARCHITECTURE, DATABASE_SCHEMA, API_CONTRACT,
│                             SHARDING, MODULE_OWNERS, DEVELOPMENT
└── docker-compose.yml        canonical + 5 shard containers + future placeholders
```

---

## Quick start (Docker — recommended)

```bash
cp .env.example .env

# Demo 1 — normalized mode
make up                 # canonical PostgreSQL + API
make seed-small         # deterministic 100 users / 1k posts / 5k comments / 10k likes
# API: http://localhost:8000/docs

# Demo 2..6 — sharding (four + one spare INDEPENDENT PostgreSQL containers)
make sharding           # starts postgres-shard-0..4 and the API in SHARDED mode
make shard-init         # create schema on every shard (also regenerates shard SQL)
make shard-seed         # seed all shards through the real router
```

Shard containers are genuinely separate PostgreSQL processes
(`postgres-shard-0` … `postgres-shard-4`), not four tables in one database.
The extra `postgres-shard-4` is the destination for the live 4 → 5
rebalancing demo.

### Quick start without Docker

For laptops/CI without a Docker daemon, `scripts/dev_server.py` starts real
embedded **PostgreSQL 16** clusters (via the `pgserver` wheel) — one canonical
plus five shard processes — and serves the API:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python scripts/dev_server.py --sharded --seed small --sharded-seed --reset
# → http://localhost:8000/docs
```

The test suite uses the same mechanism, so sharding is exercised against real
PostgreSQL everywhere, never mocks.

---

## Seeding

| Command | Users | Posts | Comments | Likes |
|---------|------:|------:|---------:|------:|
| `make seed-small`  | 100 | 1,000 | 5,000 | 10,000 |
| `make seed-medium` | 1,000 | 25,000 | 100,000 | 250,000 |
| `make seed-large`  | 5,000 | 200,000 | 800,000 | 2,000,000 |

Large is tunable via `SEED_USERS`, `SEED_POSTS`, `SEED_COMMENTS`,
`SEED_LIKES`, `SEED_FOLLOWS`, `SEED_MEDIA`, `SEED_NOTIFICATIONS`.
Generation is **deterministic** (`SEED_RANDOM_SEED`, default fixed), so every
member/environment gets identical data. In sharded mode, post and media ids
are *key-aligned* so each derived row lands on — and remains findable on —
its owner's shard.

---

## The six demos

```bash
# 1. Normalized DB
curl localhost:8000/api/v1/users/1
curl localhost:8000/api/v1/posts/1

# 2. Routing decisions (answers are computed, never hard-coded)
curl localhost:8000/api/v1/shards/route/user/101
curl "localhost:8000/api/v1/shards/route/12345?strategy=hash"

# 3. Targeted query — exactly ONE shard contacted, with timing
curl localhost:8000/api/v1/sharded/users/101

# 4. Scatter-gather — fans out to ALL shards, merges/sorts/limits
curl "localhost:8000/api/v1/sharded/posts?limit=50"

# 5. Hot shard — real skewed (celebrity) workload, then detection
curl -X POST "localhost:8000/api/v1/shards/demo/hot-user/42?requests=500&skew=0.9"
curl localhost:8000/api/v1/shards/stats

# 5b. Failure handling (SIMULATION of an unavailable shard)
curl -X POST localhost:8000/api/v1/shards/shard-0/simulate-down
curl localhost:8000/api/v1/shards                 # shard-0 = UNAVAILABLE
curl -X POST localhost:8000/api/v1/shards/shard-0/health-check   # restore

# 6. Online rebalancing 4 → 5, with copy → checksum → switch → cleanup
curl -X POST localhost:8000/api/v1/shards/rebalance \
     -H 'Content-Type: application/json' -d '{"table":"users"}'
```

---

## Testing & benchmarks

```bash
make test           # unit + integration + contract (boots embedded PostgreSQL)
make test-unit
make test-integration
make lint && make format

make benchmark              # all three benchmarks
make benchmark-routing      # pure router (no DB): hash + consistent hash
make benchmark-db           # scenarios A–E against real PostgreSQL clusters
```

The scaling benchmark compares **A** single PostgreSQL, **B** four shards
(targeted), **C** four shards + scatter-gather, **D** the celebrity/hot-user
workload, and **E** a 4 → 5 online rebalance. It writes JSON + CSV to
`benchmarks/results/raw/` (a committed, measured sample lives in
`benchmarks/results/samples/`). Every number is measured at runtime — nothing
is fabricated or hard-coded.

---

## API surface (summary)

See [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) and the live OpenAPI at
`/docs` / `/openapi.json`.

- System: `GET /health`, `GET /ready`
- Canonical, mode-aware: `users`, `posts`, `posts/{id}/comments`,
  `users/{id}/followers`
- Shard admin: `GET /api/v1/shards`, `…/shards/{id}`, `…/shards/stats`,
  `…/shards/distribution`, `…/shards/route/{key}`,
  `…/shards/rebalance`, `…/shards/{id}/health-check`
- Sharded data (caller never chooses a shard): `/api/v1/sharded/users…`,
  `/api/v1/sharded/posts…`, `/api/v1/sharded/posts/{id}/cross-shard`

Structured errors never leak SQL, stack traces, or credentials, e.g.

```json
{ "error": "SHARD_UNAVAILABLE", "message": "Target shard 'shard-2' is currently unavailable",
  "shard_id": "shard-2" }
```

---

## Team module boundaries

See [`docs/MODULE_OWNERS.md`](docs/MODULE_OWNERS.md). In short:

- **Member 1** owns the canonical schema, normalization, migrations, seed data.
- **Member 4 (this repo's implemented module)** owns shard routing, the
  registry, connection management, scatter-gather, cross-shard assembly,
  hot-shard detection, rebalancing, and sharding benchmarks.
- **Member 5** will choose the *replica* **inside** the shard Member 4 chose
  (the `ShardConnectionProvider` seam).
- **Member 6** will add Redis cache-aside **above** the repository (the
  `CacheProvider` seam); the router never knows a cache exists.

Future members integrate strictly through the interfaces in
`packages/db/repositories/base.py` and `packages/events/contracts.py` and must
not modify the canonical schema.

## License

[Apache License 2.0](LICENSE).
>>>>>>> origin/arena/01a08be4-meta-copy
