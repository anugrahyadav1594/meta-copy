"""Declarative base and shared column helpers for the canonical schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all canonical ORM models."""


def pk_column() -> Mapped[int]:
    """BIGINT primary key.

    BIGINT (not INT/SERIAL) is used because, in SHARDED mode, IDs are assigned
    by the application (key-aligned IDs) before insert and must never collide
    across shards.
    """
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


class CreatedMixin:
    """Rows that only carry a creation timestamp (immutable event rows)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TimestampMixin(CreatedMixin):
    """Mutable rows carrying both creation and update timestamps."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
