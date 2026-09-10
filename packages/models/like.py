"""likes — shard key in SHARDED mode: ``post_id`` (co-locates with post).

A user can like a post at most once -> UNIQUE(post_id, user_id). ``user_id``
has no FK because a liker may reside on a different shard than the post.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedMixin, pk_column


class Like(Base, CreatedMixin):
    __tablename__ = "likes"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_likes_post_user"),
        # "who liked this post" and "likes for a post" both start at post_id.
        Index("ix_likes_post_id", "post_id"),
        # "everything a user liked" (may fan out across shards).
        Index("ix_likes_user_id", "user_id"),
    )

    id: Mapped[int] = pk_column()
    # FK valid in BOTH modes: likes are sharded by post_id.
    post_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # No FK: the liker may live on another shard.
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
