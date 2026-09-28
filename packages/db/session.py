"""Async session factories and unit-of-work helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.engine import build_connect_args


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    engine: AsyncEngine,
    *,
    readonly: bool = False,
) -> AsyncIterator[AsyncSession]:
    """Transactional session scope around the canonical (or any) engine."""
    factory = make_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            if not readonly:
                await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def engine_for_url(
    url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 10,
    pool_timeout: int = 5,
    connect_timeout: int = 5,
    statement_timeout_ms: int = 5000,
) -> AsyncIterator[AsyncEngine]:
    """Short-lived engine for scripts / migrations against an arbitrary URL."""

    class _S:
        db_pool_size = pool_size
        db_max_overflow = max_overflow
        db_pool_timeout = pool_timeout
        db_connect_timeout = connect_timeout
        db_statement_timeout_ms = statement_timeout_ms

    engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        connect_args=build_connect_args(_S()),  # type: ignore[arg-type]
    )
    try:
        yield engine
    finally:
        await engine.dispose()
