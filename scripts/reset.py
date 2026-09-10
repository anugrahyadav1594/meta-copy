"""Drop and recreate the schema on canonical DB and (optionally) all shards."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "packages"))
sys.path.insert(0, str(_ROOT / "services" / "shard-router"))
sys.path.insert(0, str(_ROOT / "apps"))

from common.config import get_settings  # noqa: E402
from db.shard_engine import ShardEngineManager  # noqa: E402
from models import Base  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


async def reset_url(url: str, name: str) -> None:
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        print(f"  reset {name}")
    finally:
        await engine.dispose()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reset MetaScale databases")
    parser.add_argument("--shards", action="store_true", help="also reset shards")
    args = parser.parse_args()
    settings = get_settings()
    await reset_url(settings.database_url, "canonical")
    if args.shards:
        urls = settings.shard_urls()
        manager = ShardEngineManager(urls, settings)
        for sid, url in urls.items():
            await reset_url(url, sid)
        await manager.dispose()


if __name__ == "__main__":
    asyncio.run(main())
