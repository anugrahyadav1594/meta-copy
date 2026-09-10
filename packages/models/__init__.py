"""Canonical SQLAlchemy ORM models for MetaScale.

This is the *single* normalized 3NF schema used by:

* the canonical PostgreSQL database (NORMALIZED mode), and
* every physical shard (SHARDED mode) — shards hold the same DDL, partitioned
  by application-level shard keys.

Future members (denormalization, replication, caching, search, ...) MUST read
these models rather than define their own incompatible relational schema.
Derived systems are projections of this source of truth — PostgreSQL remains
canonical.
"""

from __future__ import annotations

from models.base import Base
from models.comment import Comment
from models.follow import Follow
from models.like import Like
from models.media import Media
from models.notification import Notification
from models.post import Post
from models.profile import Profile
from models.user import User

__all__ = [
    "Base",
    "User",
    "Profile",
    "Post",
    "Comment",
    "Like",
    "Follow",
    "Media",
    "Notification",
]
