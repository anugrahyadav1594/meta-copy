"""Distributed query execution primitives (Member 4).

* :mod:`queries.targeted`       — route then touch exactly ONE shard
* :mod:`queries.scatter_gather` — fan out to all shards, merge/limit
* :mod:`queries.cross_shard`    — explicit application-side distributed join
"""

from queries.cross_shard import CrossShardExecutor
from queries.scatter_gather import ScatterGatherExecutor
from queries.targeted import TargetedExecutor, TargetedOutcome

__all__ = [
    "CrossShardExecutor",
    "ScatterGatherExecutor",
    "TargetedExecutor",
    "TargetedOutcome",
]
