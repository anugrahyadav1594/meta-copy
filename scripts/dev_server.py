"""Run MetaScale locally WITHOUT Docker using embedded PostgreSQL 16.

Starts one canonical cluster plus five shard clusters as independent
postgres processes (pgserver wheel — same binaries Docker would run, but via
unix sockets), initializes schemas, optionally seeds, then serves the API.

Examples
--------
    python scripts/dev_server.py                 # NORMALIZED mode
    python scripts/dev_server.py --sharded       # SHARDED mode, 4 shards
    python scripts/dev_server.py --sharded --seed small --port 8000

Data lives in .pgdata/dev and persists across runs. This is a convenience for
laptops/CI; docker compose remains the canonical environment.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "packages"))
sys.path.insert(0, str(_ROOT / "services" / "shard-router"))
sys.path.insert(0, str(_ROOT / "apps"))

from models import Base  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from tests.support.pgclusters import PgClusters  # noqa: E402

CLUSTER_NAMES = ["canonical", "shard-0", "shard-1", "shard-2", "shard-3", "shard-4"]


async def init_schema(uris: dict[str, str], names: list[str], reset: bool) -> None:
    for name in names:
        engine = create_async_engine(uris[name])
        try:
            async with engine.begin() as conn:
                if reset:
                    await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sharded", action="store_true", help="enable SHARDED mode")
    parser.add_argument("--seed", choices=["small", "medium", "large"], default=None)
    parser.add_argument("--sharded-seed", action="store_true", help="seed shards too (with --seed)")
    parser.add_argument("--reset", action="store_true", help="drop/recreate schemas")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--strategy", default="consistent_hash")
    parser.add_argument("--vnodes", type=int, default=150)
    args = parser.parse_args()

    root = _ROOT / ".pgdata" / "dev"
    clusters = PgClusters(root, CLUSTER_NAMES)
    uris = clusters.start()
    print("embedded PostgreSQL 16 clusters started:")
    for name, uri in uris.items():
        print(f"  {name:<10} {uri}")

    active = CLUSTER_NAMES if args.sharded else ["canonical"]
    asyncio.run(init_schema(uris, active, args.reset))

    # configure settings via environment BEFORE importing the app
    os.environ["DATABASE_URL"] = uris["canonical"]
    if args.sharded:
        os.environ["MODE"] = "SHARDED"
        os.environ["SHARDING_ENABLED"] = "true"
        os.environ["SHARD_COUNT"] = "4"
        os.environ["SHARDING_STRATEGY"] = args.strategy
        os.environ["VIRTUAL_NODES_PER_SHARD"] = str(args.vnodes)
        for i in range(5):
            os.environ[f"SHARD_{i}_URL"] = uris[f"shard-{i}"]
    else:
        os.environ["MODE"] = "NORMALIZED"
        os.environ["SHARDING_ENABLED"] = "false"

    if args.seed:
        from common.config import get_settings
        from router.shard_router import ShardRouter

        from scripts._dataset import DatasetBuilder
        from scripts.seed import seed_canonical, seed_shards

        get_settings.cache_clear()
        builder = DatasetBuilder(args.seed)

        async def do_seed() -> None:
            rows = builder.build(None)
            await seed_canonical(rows, reset=False)
            if args.sharded and args.sharded_seed:
                settings = get_settings()
                urls = settings.shard_urls()  # active shards only (< SHARD_COUNT)
                router = ShardRouter(
                    list(urls),
                    strategy=settings.sharding_strategy,
                    virtual_nodes_per_shard=settings.virtual_nodes_per_shard,
                )
                shard_rows = builder.build(router)
                await seed_shards(shard_rows, reset=False)

        asyncio.run(do_seed())
        get_settings.cache_clear()

    import uvicorn

    print(
        f"\nMetaScale API starting on http://{args.host}:{args.port} "
        f"(mode={'SHARDED' if args.sharded else 'NORMALIZED'})"
    )
    print("Docs: /docs  |  stop with Ctrl-C\n")
    try:
        uvicorn.run("api.main:app", host=args.host, port=args.port, reload=False)
    finally:
        clusters.stop()


if __name__ == "__main__":
    main()
