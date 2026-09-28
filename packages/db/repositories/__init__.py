"""Repository abstractions and implementations.

The service layer depends ONLY on the abstract interfaces in
:mod:`db.repositories.base`. Concrete implementations are selected by
configuration:

* :mod:`db.repositories.canonical` — single PostgreSQL instance (NORMALIZED)
* :mod:`db.repositories.sharded`   — ShardRouter + per-shard engines (SHARDED)

Callers never learn which topology they are talking to: both families
implement the same interfaces, so feeds, graph, search, media and cache
modules work unchanged in NORMALIZED and SHARDED modes.

Layering (never inverted)::

    Service -> Cache adapter (M6) -> Repository -> Shard Router (M4)
                                                -> Replication provider (M5)
                                                -> PostgreSQL
"""

from db.repositories.base import (
    CommentRepository,
    FollowRepository,
    HashtagRepository,
    LikeRepository,
    MediaRepository,
    NotificationRepository,
    PostRepository,
    UserRepository,
)

__all__ = [
    "CommentRepository",
    "FollowRepository",
    "HashtagRepository",
    "LikeRepository",
    "MediaRepository",
    "NotificationRepository",
    "PostRepository",
    "UserRepository",
]
