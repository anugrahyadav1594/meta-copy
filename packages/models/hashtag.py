"""hashtags — the tag vocabulary, and the post <-> hashtag association.

``hashtags`` is a small lookup table (one row per distinct tag); it exists to
keep the schema in 3NF instead of repeating tag strings on every post.
``post_hashtags`` is the pure association table.

Shard keys in SHARDED mode:
* ``hashtags``       -> ``hashtag_id``
* ``post_hashtags``  -> ``post_id`` (co-locates the tag edges with the post,
  exactly like comments and likes).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedMixin, pk_column


class Hashtag(Base, CreatedMixin):
    __tablename__ = "hashtags"
    __table_args__ = (Index("ix_hashtags_tag", "tag", unique=True),)

    hashtag_id: Mapped[int] = pk_column()
    tag: Mapped[str] = mapped_column(String(100), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Hashtag hashtag_id={self.hashtag_id} tag={self.tag!r}>"


class PostHashtag(Base, CreatedMixin):
    __tablename__ = "post_hashtags"
    __table_args__ = (
        Index("ix_post_hashtags_hashtag", "hashtag_id"),
        # No FK on hashtag_id: the hashtag row may live on another shard.
        # post_id keeps its FK because edges are co-located with the post.
    )

    post_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("posts.post_id", ondelete="CASCADE"),
        primary_key=True,
    )
    hashtag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
