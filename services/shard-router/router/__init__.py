"""MetaScale ShardRouter — Member 4 module.

Pure routing/topology logic lives in this package and has NO database
dependency: it can be unit-tested in isolation and reused by any transport.

* :mod:`router.hash_router`       — stable-hash + modulo routing
* :mod:`router.consistent_hash`   — consistent-hash ring
* :mod:`router.virtual_nodes`     — vnode placement / distribution analysis
* :mod:`router.shard_router`      — top-level router + entity shard-key mapping
"""

from router.consistent_hash import ConsistentHashRing
from router.hash_router import HashRouter, stable_hash
from router.shard_router import RouteDecision, ShardRouter
from router.virtual_nodes import VirtualNodeLayout

__all__ = [
    "ConsistentHashRing",
    "HashRouter",
    "RouteDecision",
    "ShardRouter",
    "VirtualNodeLayout",
    "stable_hash",
]
