"""Repository abstractions and implementations.

The service layer depends ONLY on the abstract interfaces in
:mod:`db.repositories.base`. Concrete implementations are selected by
configuration:

* :mod:`db.repositories.canonical` — single PostgreSQL instance (NORMALIZED)
* :mod:`db.repositories.sharded`   — ShardRouter + per-shard engines (SHARDED)

Future members slot in below/around these interfaces without changing the
API or the service layer:

* Member 5 (replication) replaces ``ShardConnectionProvider``;
* Member 6 (caching) wraps a repository with cache-aside on cache miss::

      GET post -> Redis -> (miss) -> Repository -> ShardRouter -> PostgreSQL
"""

from db.repositories.base import CommentRepository, PostRepository, UserRepository

__all__ = [
    "CommentRepository",
    "PostRepository",
    "UserRepository",
]
