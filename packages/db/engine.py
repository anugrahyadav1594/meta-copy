"""Canonical PostgreSQL engine (single source of truth in NORMALIZED mode)."""

from __future__ import annotations

from typing import Any

from common.config import Settings, get_settings
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def build_connect_args(settings: Settings) -> dict[str, Any]:
    """libpq connect args: connect timeout + server-side statement timeout."""
    return {
        "connect_timeout": settings.db_connect_timeout,
        "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
    }


def make_engine(url: str, settings: Settings, *, poolclass: Any | None = None) -> AsyncEngine:
    """Create a pooled async SQLAlchemy engine for a PostgreSQL URL.

    All access is parameterized through SQLAlchemy — no raw SQL string
    interpolation anywhere in the project.
    """
    kwargs: dict[str, Any] = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_pre_ping": True,
        "connect_args": build_connect_args(settings),
    }
    if poolclass is not None:
        kwargs["poolclass"] = poolclass
    return create_async_engine(url, **kwargs)


_engine: AsyncEngine | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide lazy singleton for the canonical engine."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_pre_ping=True,
            connect_args=build_connect_args(settings),
        )
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
