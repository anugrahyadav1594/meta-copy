"""follows — directed follow edges.

Shard key in SHARDED mode: ``follower_id`` (co-locates a user's *outgoing*
follows with the follower). ``following_id`` has NO FK: the followee may live
on another shard. Primary key is the natural composite (follower_id,
following_id); a user cannot follow themselves.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedMixin


class Follow(Base, CreatedMixin):
    __tablename__ = "follows"
    __table_args__ = (
        PrimaryKeyConstraint("follower_id", "following_id", name="pk_follows"),
        # Incoming follows: "who follows this user" -> followers listing.
        Index("ix_follows_following", "following_id"),
        CheckConstraint(
            "follower_id <> following_id",
            name="ck_follows_no_self_follow",
        ),
    )

    # FK on follower only: follows are sharded by follower_id -> co-located.
    follower_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # No FK: followee may live on another shard.
    following_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
