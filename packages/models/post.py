"""posts — authored content. Shard key in SHARDED mode: ``author_id``."""

from __future__ import annotations

from common.enums import PostVisibility
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, pk_column


class Post(Base, TimestampMixin):
    __tablename__ = "posts"
    __table_args__ = (
        # Author timeline: "all posts by an author, newest first".
        Index("ix_posts_author_created", "author_id", "created_at"),
        # Global recency feeds / scatter-gather merge sorting.
        Index("ix_posts_created_at", "created_at"),
        CheckConstraint(
            f"visibility IN ('{PostVisibility.PUBLIC.value}', "
            f"'{PostVisibility.PRIVATE.value}', "
            f"'{PostVisibility.FOLLOWERS.value}')",
            name="ck_posts_visibility",
        ),
    )

    id: Mapped[int] = pk_column()
    # FK valid in BOTH modes: posts are sharded by author_id and therefore
    # co-located with the author row.
    author_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PostVisibility.PUBLIC.value
    )
