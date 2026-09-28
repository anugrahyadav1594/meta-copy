"""media — *metadata* about media blobs (Haystack-inspired storage, Member 8).

The blob itself is never stored in PostgreSQL — only the immutable
``storage_key`` pointer and the ``checksum_sha256`` used for content-addressable
de-duplication. Shard key in SHARDED mode: ``owner_id`` (co-locates a user's
media with the user).
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
        # Content-addressable de-duplication lookup (Member 8).
        Index("ix_media_checksum", "checksum_sha256"),
        # "media attached to this post".
        Index("ix_media_post", "post_id"),
        CheckConstraint("size_bytes >= 0", name="ck_media_size_nonneg"),
        CheckConstraint(
            f"media_type IN ('{MediaType.IMAGE.value}', '{MediaType.VIDEO.value}', "
            f"'{MediaType.AUDIO.value}', '{MediaType.DOCUMENT.value}')",
            name="ck_media_type",
        ),
    )

    media_id: Mapped[int] = pk_column()
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Optional link to the post the blob is attached to (a blob may be
    # uploaded before its post exists). No FK: in SHARDED mode the post can
    # live on another shard than its owner's media.
    post_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Member 8 (Haystack-inspired content-addressable storage): blobs are
    # addressed and de-duplicated by the SHA-256 of their bytes. PostgreSQL
    # stores metadata + checksum only — never the binary itself.
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
