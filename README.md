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

**This repository now holds the complete integrated system** — every member
module composed into one FastAPI application, one event contract, one compose
file and one frontend. See [docs/INTEGRATION_AUDIT.md](docs/INTEGRATION_AUDIT.md)
for the full member-by-member audit and [docs/FINAL_DEMO.md](docs/FINAL_DEMO.md)
for how to run the demo.

## Final integrated system (current state)

```text
Client (Architecture Control Center, React + TS)
  -> FastAPI  /api/v1
  -> Service layer            business rules, domain events
  -> Cache adapter (M6)       Redis cache-aside, above the repository
  -> Repository               canonical | sharded
  -> Shard Router (M4)        consistent hashing, virtual nodes, rebalance
  -> Replication provider (M5) primary / replica inside a shard
  -> PostgreSQL               SOURCE OF TRUTH (canonical + shards)

Derived, rebuildable projections hang off the event bus:
  denormalized read model (M3) · feed (M7) · media metadata (M8) · search (M10)
```

| Piece | Where |
| --- | --- |
| One API, 96 endpoints under `/api/v1` | `apps/api/`, contract in [docs/API_CONTRACT.md](docs/API_CONTRACT.md) |
| Health across API + PostgreSQL + shards + Redis + RabbitMQ + OpenSearch + MinIO | `GET /api/v1/health` → `healthy \| degraded \| unavailable` |
| Live database change stream (operation/table/key/shard/before/after) | `GET /api/v1/database/changes`, [docs/DATABASE_DESIGN.md](docs/DATABASE_DESIGN.md) |
| One event contract, two real transports | `GET /api/v1/events`, [docs/EVENTS.md](docs/EVENTS.md) |
| Live benchmarks (no hard-coded numbers) | `GET /api/v1/benchmarks/*`, [docs/BENCHMARKING.md](docs/BENCHMARKING.md) |
| Architecture Control Center (dashboard / observability / benchmarks) | `frontend/`, [docs/FRONTEND_DEMO.md](docs/FRONTEND_DEMO.md) |
| Member 9 (graph) | **excluded this iteration** — reference copy in `legacy/member9-graph/` |

### Run the whole thing

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -r requirements.txt     # or: make install

make test                 # 141 tests against real PostgreSQL clusters
make dev-full             # API, FULL_DISTRIBUTED: http://localhost:8000/docs
make frontend             # UI:  http://localhost:5173/dashboard   (second shell)
make demo-full            # or a one-command 20-step scripted demo
```

With Docker: `MODE=FULL_DISTRIBUTED CACHE_ENABLED=true DENORMALIZED_ENABLED=true
docker compose --profile distributed --profile observability up -d --build`.

### Documentation

| Document | Purpose |
| --- | --- |
| [docs/INTEGRATION_AUDIT.md](docs/INTEGRATION_AUDIT.md) | member → module → status → integration work → final API |
| [docs/FINAL_DEMO.md](docs/FINAL_DEMO.md) | how to run and present the demo |
| [docs/PRESENTATION_GUIDE.md](docs/PRESENTATION_GUIDE.md) | per-member: problem, concept, demo, trade-offs, what NOT to claim |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/architecture.md](docs/architecture.md) | layered design and Mermaid diagrams |
| [docs/API_CONTRACT.md](docs/API_CONTRACT.md) | every endpoint, generated from OpenAPI |
| [docs/EVENTS.md](docs/EVENTS.md) · [docs/DATABASE_DESIGN.md](docs/DATABASE_DESIGN.md) · [docs/SHARDING.md](docs/SHARDING.md) · [docs/REPLICATION.md](docs/REPLICATION.md) · [docs/CACHING.md](docs/CACHING.md) · [docs/DENORMALIZATION.md](docs/DENORMALIZATION.md) · [docs/BENCHMARKING.md](docs/BENCHMARKING.md) · [docs/FRONTEND_DEMO.md](docs/FRONTEND_DEMO.md) | per-subsystem detail |

---

## Earlier milestones (foundation + Member 4)

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
├── services/                 member modules: cache (M6), denormalization (M3),
│                             feed (M7), media (M8), replication (M5),
│                             search (M10), observability (M11)
│   └── shard-router/         Member 4: router, metadata, health, queries,
│                             rebalance, hotspots, metrics
├── frontend/                 Architecture Control Center (React + TS + Vite)
├── legacy/member9-graph/     Member 9: EXCLUDED this iteration, reference only
├── benchmarks/               unified A–F benchmark + sharding benchmarks
├── tests/                    unit / integration / contract (+ embedded PG)
├── docs/                     INTEGRATION_AUDIT, FINAL_DEMO, PRESENTATION_GUIDE,
│                             ARCHITECTURE, architecture (Mermaid), API_CONTRACT,
│                             EVENTS, DATABASE_DESIGN, SHARDING, REPLICATION,
│                             CACHING, DENORMALIZATION, BENCHMARKING,
│                             FRONTEND_DEMO, MODULE_OWNERS, DEVELOPMENT
└── docker-compose.yml        profiles: core (always on) · distributed ·
                              observability (+ fine-grained per-backend)
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

### Architecture Control Center (frontend)

```bash
make frontend          # npm install + Vite dev server on :5173 (proxies /api)
# open http://localhost:5173/dashboard
make frontend-build    # build to frontend/dist, served by the API at /dashboard
```

The UI shows the live request path (request → service → cache → shard router →
database → event → derived), the database change stream, the shard map, cache
hits and misses, the denormalized side-by-side, real benchmarks and the
observability metrics. Simulated subsystems are labelled `SIMULATED`, and
concept-inspired ones `INSPIRED BY` — see
[docs/FRONTEND_DEMO.md](docs/FRONTEND_DEMO.md).

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
- **Member 2** owns query-shape/index work; indexes live in the schema and the
  effect is measured, not asserted.
- **Member 3** owns the denormalized read model (a rebuildable projection).
- **Member 4** owns shard routing, the registry, connection management,
  scatter-gather, cross-shard assembly, hot-shard detection, rebalancing and
  sharding benchmarks.
- **Member 5** chooses the *replica* **inside** the shard Member 4 chose (the
  `ShardConnectionProvider` seam).
- **Member 6** owns Redis cache-aside **above** the repository (the
  `CacheProvider` seam); the router never knows a cache exists.
- **Members 7, 8, 10, 11** own feed, media, search and observability — all
  derived systems fed by the event bus.
- **Member 9** is **excluded from this iteration** (reference copy in
  `legacy/member9-graph/`); relationships are read from `follows`.
- **Member 12** owns benchmarking **and** system integration (one app, one
  contract, one compose file, docs, UI).

Future members integrate strictly through the interfaces in
`packages/db/repositories/base.py` and `packages/events/contracts.py` and must
not modify the canonical schema.

## License

[Apache License 2.0](LICENSE).
