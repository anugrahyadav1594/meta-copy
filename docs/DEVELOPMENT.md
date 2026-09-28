# Development guide

## Requirements

- Python 3.11+ (3.12 in the Docker image) and Docker + Docker Compose for the
  container workflow.
- Without Docker, the `pgserver` dev dependency runs embedded PostgreSQL 16
  clusters used by tests, benchmarks, and `scripts/dev_server.py`.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # or: pip install -r requirements-dev.txt
cp .env.example .env
```

`PYTHONPATH` used everywhere (also exported by the Makefile):
`packages:services/shard-router:apps:.`.

## Running

```bash
# Docker
make up                 # canonical PostgreSQL + API (NORMALIZED)
make seed-small
make sharding           # 4 active + 1 spare independent shard containers + API
make shard-init         # create schema on shards, regenerate shard DDL
make shard-seed         # seed shards through the router
make cache              # PostgreSQL + Redis cache + API (Member 6)
make down               # stop; `make reset` also removes volumes

# No Docker (embedded PostgreSQL)
python scripts/dev_server.py --sharded --seed small --sharded-seed --reset
python scripts/dev_server.py                       # NORMALIZED
make dev-cache          # NORMALIZED + memory:// fakeredis cache (dev/test only)
```

API: http://localhost:8000/docs.

## Migrations

The schema is defined in `packages/models`. Canonical migrations use Alembic:

```bash
make migrate                       # alembic upgrade head against DATABASE_URL
# regenerate per-shard first-boot SQL from the models:
python scripts/initialize_shards.py --write-sql --shard-count 5
```

Docker first-boot uses `infrastructure/postgres/init/*.sql`; an integration
test asserts the hand-written SQL matches the ORM metadata.

## Testing

```bash
make test                 # boots real PostgreSQL 16 clusters (pgserver)
pytest tests/unit -q      # fast, no DB
pytest tests/integration -q
pytest tests/contract -q  # drives the FastAPI app over ASGI
```

To run tests against your own containers instead of embedded clusters, export
`DATABASE_URL` and `SHARD_0_URL … SHARD_4_URL` first; the conftest prefers
external URLs when present.

## Lint / format

```bash
make lint     # ruff + black --check
make format   # black + ruff --fix
```

## Benchmarks

```bash
make benchmark-routing     # deterministic routing; movement; vnode spread
make benchmark-db          # A single, B 4-shard targeted, C scatter-gather,
                           # D hot user, E 4→5 rebalance against real PG
# knobs: BENCH_REQUESTS, BENCH_CONCURRENCY, BENCH_SCATTER
```

Outputs: `benchmarks/results/raw/{json,csv}`; committed measured samples in
`benchmarks/results/samples/`. Never edit results by hand.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/seed.py` | deterministic seed (`--size`, `--sharded`, `--both`, `--no-reset`) |
| `scripts/reset.py` | drop/recreate schema on canonical (`--shards` too) |
| `scripts/initialize_shards.py` | live schema creation and/or shard-DDL generation |
| `scripts/dev_server.py` | embedded-PostgreSQL dev API (no Docker) |
| `scripts/healthcheck.py` | CLI probe of `/health` + `/ready` |

## Configuration

All settings come from environment / `.env` via Pydantic Settings
(`packages/common/config.py`). Key values: `MODE`, `SHARDING_ENABLED`,
`SHARDING_STRATEGY`, `SHARD_COUNT`, `VIRTUAL_NODES_PER_SHARD`,
`SHARD_n_URL`, hot-shard thresholds, pool sizes/timeouts, `SEED_*`. Credentials
are never hard-coded; `.env` is git-ignored.

## Compose profiles

`postgres`/`api` start by default; the shards sit behind the `sharding`
profile. The `cache` profile (Member 6, Redis) is IMPLEMENTED and is enabled
together with its overlay via `make cache` (compose
`docker-compose.cache.yml`, which sets `CACHE_ENABLED=true` and waits for the
Redis healthcheck). The remaining services are individually profiled
placeholders: `messaging` (rabbitmq), `search` (opensearch), `storage`
(minio), `observability` (prometheus, grafana). Examples:
`docker compose --profile sharding up`,
`docker compose --profile cache -f docker-compose.yml -f docker-compose.cache.yml up`.
See [CACHE.md](CACHE.md) for the cache design and endpoints.

## Troubleshooting

- **Stale embedded clusters:** delete `.pgdata/` (git-ignored) and re-run.
- **`NOT_CONFIGURED` on `/shards`:** the API is in NORMALIZED mode; start with
  shard URLs / `MODE=SHARDED`.
- **Port conflicts in Docker:** shards map host ports 5540–5544; canonical is
  5432, API 8000.
