# MetaScale developer Makefile.
# Docker Compose is the primary workflow; `test`/`lint`/`format` run locally.

PYTHON ?= python3
COMPOSE := docker compose
export PYTHONPATH := $(PWD)/packages:$(PWD)/services/shard-router:$(PWD)/apps:$(PWD)
export DOCKER_BUILDKIT := 1

.PHONY: help install up down reset sharding cache dev-cache seed seed-small seed-medium seed-large \
        shard-seed shard-init api test test-unit test-integration lint format \
        benchmark benchmark-routing benchmark-db health migrate logs

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Create a virtualenv and install dev dependencies
	$(PYTHON) -m venv .venv && . .venv/bin/activate && \
		pip install -e ".[dev]" -r requirements.txt

# ----------------------------------------------------------------- lifecycle
up:  ## Start canonical PostgreSQL + API (NORMALIZED mode)
	$(COMPOSE) up -d postgres api

sharding:  ## Start 4 (+1 spare) independent PostgreSQL shards + API (SHARDED)
	SHARDING_ENABLED=true MODE=SHARDED $(COMPOSE) --profile sharding up -d

cache:  ## Start canonical PostgreSQL + Redis cache + API (cache enabled)
	$(COMPOSE) --profile cache -f docker-compose.yml -f docker-compose.cache.yml up -d --build

dev-cache:  ## No-Docker: embedded PostgreSQL + in-memory (fakeredis) cache
	CACHE_ENABLED=true REDIS_URL=memory:// $(PYTHON) scripts/dev_server.py --reset --seed small --port 8000

down:  ## Stop containers (keeps data volumes)
	$(COMPOSE) down

reset:  ## Stop containers and DELETE all database volumes
	$(COMPOSE) down -v

# ---------------------------------------------------------------- migrations
migrate:  ## Run Alembic migrations against canonical DATABASE_URL
	$(PYTHON) -m alembic upgrade head

shard-init:  ## Create schema on live shards (SHARD_n_URL) and regenerate shard SQL
	$(PYTHON) scripts/initialize_shards.py --write-sql --canonical

# -------------------------------------------------------------------- seeding
seed: seed-small  ## Default seed = small

seed-small:  ## 100 users / 1k posts / 5k comments / 10k likes (canonical)
	$(COMPOSE) run --rm -e SEED_SIZE=small api python scripts/seed.py --size small

seed-medium:  ## 1,000 users / 25k posts / 100k comments / 250k likes
	$(COMPOSE) run --rm -e SEED_SIZE=medium api python scripts/seed.py --size medium

seed-large:  ## Large dataset (tune with SEED_USERS / SEED_POSTS ...)
	$(COMPOSE) run --rm -e SEED_SIZE=large api python scripts/seed.py --size large

shard-seed:  ## Seed ALL shards (requires `make sharding`)
	SHARDING_ENABLED=true MODE=SHARDED $(COMPOSE) --profile sharding run --rm \
		-e SHARDING_ENABLED=true -e MODE=SHARDED api python scripts/seed.py --sharded --size small

shard-seed-both:  ## Seed canonical and shards with the same dataset
	SHARDING_ENABLED=true MODE=SHARDED $(COMPOSE) --profile sharding run --rm \
		-e SHARDING_ENABLED=true api python scripts/seed.py --both --size small

# ----------------------------------------------------------------------- run
api:  ## Run the API locally (uvicorn, reload)
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# No-Docker convenience: real embedded PostgreSQL 16 clusters via pgserver
dev:  ## Run NORMALIZED API with embedded PostgreSQL (no Docker)
	$(PYTHON) scripts/dev_server.py --reset --seed small --port 8000

dev-sharded:  ## Run SHARDED API with 4 embedded shards + seeded data
	$(PYTHON) scripts/dev_server.py --sharded --reset --seed small --sharded-seed --port 8000

seed-local:  ## Seed the embedded/canonical DB without Docker
	$(PYTHON) scripts/seed.py --size $(or $(SIZE),small)

shard-sql:  ## Regenerate per-shard first-boot DDL from the ORM models
	$(PYTHON) scripts/initialize_shards.py --write-sql --shard-count 5

health:  ## Probe /health and /ready
	$(PYTHON) scripts/healthcheck.py

# ---------------------------------------------------------------------- tests
test:  ## Full test suite (starts embedded PostgreSQL 16 clusters if available)
	$(PYTHON) -m pytest tests services/shard-router/tests services/cache/tests

test-unit:
	$(PYTHON) -m pytest tests/unit services/shard-router/tests -q

test-integration:
	$(PYTHON) -m pytest tests/integration tests/contract -q

lint:  ## Static checks
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .

format:  ## Auto-format
	$(PYTHON) -m black .
	$(PYTHON) -m ruff check --fix .

# ----------------------------------------------------------------- benchmark
benchmark: benchmark-routing benchmark-db  ## All sharding benchmarks

benchmark-routing:  ## Pure router benchmarks (no database needed)
	$(PYTHON) benchmarks/sharding/benchmark_hash.py
	$(PYTHON) benchmarks/sharding/benchmark_consistent_hash.py

benchmark-db:  ## DB-backed scaling scenarios A-E (embedded PG or SHARD_n_URL)
	$(PYTHON) benchmarks/sharding/benchmark_scaling.py

logs:
	$(COMPOSE) logs -f api
