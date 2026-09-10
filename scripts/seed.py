"""Seed the canonical database and/or every shard with deterministic data.

Examples
--------
    python scripts/seed.py --size small
    python scripts/seed.py --size medium --sharded
    DATABASE_URL=... SHARDING_ENABLED=true python scripts/seed.py --size small --sharded

Insertion order honours foreign keys. In sharded mode every row is placed via
the same ShardRouter the application uses, so co-located rows land together
and every foreign key remains satisfiable on its shard.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "packages"))
sys.path.insert(0, str(_ROOT / "services" / "shard-router"))
sys.path.insert(0, str(_ROOT / "apps"))

from common.config import get_settings  # noqa: E402
from db.engine import get_engine  # noqa: E402
from db.session import make_session_factory  # noqa: E402
from db.shard_engine import ShardEngineManager  # noqa: E402
from models import Base  # noqa: E402
from router.shard_router import ShardRouter  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from scripts._dataset import DatasetBuilder  # noqa: E402

# Foreign-key-safe insertion order
TABLE_ORDER = [
    "users",
    "media",
    "profiles",
    "posts",
    "follows",
    "comments",
    "likes",
    "notifications",
]
CHUNK = 2_000


async def ensure_schema(engine) -> None:  # noqa: ANN001
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def reset_tables(engine) -> None:  # noqa: ANN001
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def seed_canonical(rows: dict[str, list[dict]], *, reset: bool) -> dict[str, int]:
    settings = get_settings()
    engine = get_engine(settings)
    if reset:
        await reset_tables(engine)
    else:
        await ensure_schema(engine)
    factory = make_session_factory(engine)
    counts = {}
    started = time.perf_counter()
    for table_name in TABLE_ORDER:
        data = rows[table_name]
        table = Base.metadata.tables[table_name]
        total = 0
        for i in range(0, len(data), CHUNK):
            async with factory() as session:
                await session.execute(
                    pg_insert(table).on_conflict_do_nothing(), data[i : i + CHUNK]
                )
                await session.commit()
            total += min(CHUNK, len(data) - i)
            print(f"\r  {table_name:<15} {total:>10,}/{len(data)}", end="", flush=True)
        counts[table_name] = len(data)
        print()
    print(f"canonical seed complete in {time.perf_counter() - started:.1f}s")
    await engine.dispose()
    return counts


async def seed_shards(rows: dict[str, list[dict]], *, reset: bool) -> dict[str, int]:
    settings = get_settings()
    urls = settings.shard_urls()
    if not urls:
        raise SystemExit("No SHARD_n_URL configured; start shards (make sharding) or pass URLs.")
    router = ShardRouter(
        list(urls),
        strategy=settings.sharding_strategy,
        virtual_nodes_per_shard=settings.virtual_nodes_per_shard,
    )
    manager = ShardEngineManager(urls, settings)

    # schemas
    for sid in router.shard_ids:
        engine = await manager.engine_for(sid)
        if reset:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        else:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    key_cols = {
        "users": "id",
        "media": "owner_id",
        "profiles": "user_id",
        "posts": "author_id",
        "follows": "follower_id",
        "comments": "post_id",
        "likes": "post_id",
        "notifications": "recipient_id",
    }

    started = time.perf_counter()
    per_shard_counts = {sid: 0 for sid in router.shard_ids}
    for table_name in TABLE_ORDER:
        data = rows[table_name]
        key_col = key_cols[table_name]
        buckets: dict[str, list[dict]] = {sid: [] for sid in router.shard_ids}
        for row in data:
            buckets[router.shard_for(row[key_col])].append(row)
        table = Base.metadata.tables[table_name]
        for sid, bucket in buckets.items():
            per_shard_counts[sid] += len(bucket)
            for i in range(0, len(bucket), CHUNK):
                async with manager.session_scope(sid) as session:
                    await session.execute(
                        pg_insert(table).on_conflict_do_nothing(), bucket[i : i + CHUNK]
                    )
        dist = ", ".join(f"{sid}={len(buckets[sid]):,}" for sid in router.shard_ids)
        print(f"  {table_name:<15} {len(data):>9,} rows  [{dist}]")
    print(f"sharded seed complete in {time.perf_counter() - started:.1f}s")
    print("per-shard totals:", per_shard_counts)
    await manager.dispose()
    return per_shard_counts


def parse_overrides() -> dict[str, int]:
    mapping = {
        "SEED_USERS": "users",
        "SEED_POSTS": "posts",
        "SEED_COMMENTS": "comments",
        "SEED_LIKES": "likes",
        "SEED_FOLLOWS": "follows",
        "SEED_MEDIA": "media",
        "SEED_NOTIFICATIONS": "notifications",
    }
    out = {}
    for env, key in mapping.items():
        val = os.environ.get(env)
        if val:
            out[key] = int(val)
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed MetaScale")
    parser.add_argument(
        "--size", default=os.environ.get("SEED_SIZE", "small"), choices=["small", "medium", "large"]
    )
    parser.add_argument(
        "--sharded", action="store_true", help="seed shards instead of canonical DB"
    )
    parser.add_argument("--both", action="store_true", help="seed canonical AND shards")
    parser.add_argument("--no-reset", action="store_true", help="append instead of wiping")
    args = parser.parse_args()

    builder = DatasetBuilder(
        args.size, seed=get_settings().seed_random_seed, overrides=parse_overrides()
    )
    print(f"Building {args.size} deterministic dataset (seed={builder.seed})...")

    if args.sharded or args.both:
        # Build rows WITH router alignment so post/media ids hash to owner shards
        settings = get_settings()
        urls = settings.shard_urls()
        router = (
            ShardRouter(
                list(urls),
                strategy=settings.sharding_strategy,
                virtual_nodes_per_shard=settings.virtual_nodes_per_shard,
            )
            if urls
            else None
        )
        sharded_rows = builder.build(router)
        print("dataset:", builder.summary(sharded_rows))
        await seed_shards(sharded_rows, reset=not args.no_reset)

    if not args.sharded or args.both:
        canonical_rows = builder.build(None)
        print("dataset:", builder.summary(canonical_rows))
        await seed_canonical(canonical_rows, reset=not args.no_reset)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts  # noqa: F401  (package marker for `scripts._dataset`)

    asyncio.run(main())
