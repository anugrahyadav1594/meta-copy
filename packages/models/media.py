"""media — *metadata* about media blobs (Haystack-inspired storage is future).

The blob itself is not stored in PostgreSQL — only the immutable
``storage_key`` pointer. Shard key in SHARDED mode: ``owner_id`` (co-locates
a user's media with the user).
"""

from __future__ import annotations

from common.enums import MediaType
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedMixin, pk_column


class Media(Base, CreatedMixin):
    __tablename__ = "media"
    __table_args__ = (
        Index("ix_media_owner", "owner_id"),
        CheckConstraint("size_bytes >= 0", name="ck_media_size_nonneg"),
        CheckConstraint(
            f"media_type IN ('{MediaType.IMAGE.value}', '{MediaType.VIDEO.value}', "
            f"'{MediaType.AUDIO.value}', '{MediaType.DOCUMENT.value}')",
            name="ck_media_type",
        ),
    )

    id: Mapped[int] = pk_column()
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
