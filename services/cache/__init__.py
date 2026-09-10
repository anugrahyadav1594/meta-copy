"""Member 6 — distributed Redis cache-aside service.

Public entrypoint::

    from services.cache.cache import CacheProvider

The cache is an async, TTL-bound, rebuildable layer ABOVE the repositories.
PostgreSQL is always the source of truth.
"""

from services.cache.cache import CacheProvider

__all__ = ["CacheProvider"]
