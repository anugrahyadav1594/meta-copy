# PostgreSQL infrastructure

## Canonical instance (`init/`)

Mounted into the `postgres` container's `/docker-entrypoint-initdb.d`, these
run once on an empty data volume in lexical order:

1. `001_extensions.sql` — no third-party extensions required; defines the
   `set_updated_at()` trigger function.
2. `002_schema.sql` — the canonical normalized 3NF tables, constraints, CHECKs,
   foreign keys, cascades, and `updated_at` triggers.
3. `003_indexes.sql` — composite and secondary indexes for the documented
   access paths (see `docs/DATABASE_SCHEMA.md`).
4. `004_seed.sql` — a tiny reference dataset so the API returns rows before
   `make seed`. The bulk deterministic datasets come from `scripts/seed.py`.

The source of truth for the schema is the SQLAlchemy models in
`packages/models/`. An integration test compares the structure created by
these SQL files against `Base.metadata` to prevent drift.

## Shards (`shards/shard-N/`)

Each shard is an **independent PostgreSQL container**
(`postgres-shard-0…4` in `docker-compose.yml`) carrying the SAME relational
schema, partitioned at application level. `001_schema.sql` in each directory
is **generated**, not hand-written:

```bash
python scripts/initialize_shards.py --write-sql --shard-count 5
```

On a live deployment you can create the schema directly on every `SHARD_n_URL`
with:

```bash
python scripts/initialize_shards.py        # uses SHARD_0_URL … from the environment
```

`shard-4` is the spare destination for the live 4 → 5 rebalancing
demonstration; it is excluded from active routing (`SHARD_COUNT=4`) until the
migration adds it to the ring.

## Foreign-key policy (important)

Hard foreign keys are kept only where the sharding key co-locates both rows.
Cross-shard references (`comments.user_id`, `likes.user_id`,
`follows.following_id`, `notifications.actor_id`) intentionally omit FKs and
are assembled by the application's cross-shard query layer.
