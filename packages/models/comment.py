"""comments — shard key in SHARDED mode: ``post_id`` (co-locates with post).

``user_id`` deliberately has NO foreign key: a comment author may live on a
different shard than the post they commented on.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedMixin, pk_column


class Comment(Base, CreatedMixin):
    __tablename__ = "comments"
    __table_args__ = (
        # "comments for a post, oldest first" — the hottest access path.
        Index("ix_comments_post_created", "post_id", "created_at"),
        # "comments written by a user" (may fan out across shards).
        Index("ix_comments_user_id", "user_id"),
    )

    comment_id: Mapped[int] = pk_column()
    # FK valid in BOTH modes: comments are sharded by post_id -> co-located
    # with the post (and with the post's likes).
    post_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("posts.post_id", ondelete="CASCADE"),
        nullable=False,
    )
    # No FK: comment author may reside on another shard.
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Self-reference for threaded replies. No FK in the sharded DDL: the
    # parent comment is co-located (same post_id) in practice, but the DDL is
    # generated once for both modes and kept free of cross-shard guarantees.
    parent_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
