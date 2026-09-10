"""profiles — 1:1 user display information (3NF separation from accounts)."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, pk_column


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"
    __table_args__ = (
        # one profile per user, and the dominant lookup path (by user_id)
        Index("ix_profiles_user_id", "user_id", unique=True),
    )

    id: Mapped[int] = pk_column()
    # FK valid in BOTH modes: profiles are sharded by user_id -> co-located
    # with their user.
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Application-level reference to media(id); no hard FK to avoid a
    # profiles <-> media cyclic DDL dependency.
    avatar_media_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
