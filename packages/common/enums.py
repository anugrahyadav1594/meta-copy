"""Enumerations shared across MetaScale.

These enums are part of the *shared foundation*: every future module
(denormalization, replication, caching, search, ...) must reuse them so that
all team members speak the same vocabulary.
"""

from __future__ import annotations

import enum


class DeploymentMode(str, enum.Enum):
    """Top-level deployment modes for the platform.

    Only ``NORMALIZED`` and ``SHARDED`` are implemented in this milestone.
    The other values are *reserved* extension points owned by other team
    members and must not be implemented by the sharding module.
    """

    NORMALIZED = "NORMALIZED"
    SHARDED = "SHARDED"

    # ---- reserved for future members (not implemented) -----------------
    DENORMALIZED = "DENORMALIZED"  # Member 3
    SHARDED_REPLICATED = "SHARDED_REPLICATED"  # Member 4 + Member 5
    SHARDED_CACHED = "SHARDED_CACHED"  # + Member 6
    FULL_DISTRIBUTED = "FULL_DISTRIBUTED"  # all members

    @property
    def is_implemented(self) -> bool:
        return self in (DeploymentMode.NORMALIZED, DeploymentMode.SHARDED)


class RoutingStrategy(str, enum.Enum):
    """Shard routing strategies supported by Member 4's ShardRouter."""

    HASH = "hash"  # stable hash + modulo
    CONSISTENT_HASH = "consistent_hash"  # ring with virtual nodes


class ShardStatus(str, enum.Enum):
    """Lifecycle/health states for a shard."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    REBALANCING = "REBALANCING"
    BOOTSTRAPPING = "BOOTSTRAPPING"


class QueryType(str, enum.Enum):
    """Categories of sharded queries, used for metrics and logging."""

    TARGETED = "targeted"
    SCATTER_GATHER = "scatter_gather"
    CROSS_SHARD = "cross_shard"
    WRITE = "write"


class RebalancePhase(str, enum.Enum):
    """Explicit, auditable phases for an online data migration.

    The migration never silently deletes data: cleanup only happens *after*
    verification succeeds and ownership has switched.
    """

    PLANNED = "PLANNED"
    COPYING = "COPYING"
    VERIFYING = "VERIFYING"
    SWITCHING = "SWITCHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"  # verification failed -> ownership NOT switched


class PostVisibility(str, enum.Enum):
    """Visibility values for posts (CHECK constrained in the schema)."""

    PUBLIC = "public"
    PRIVATE = "private"
    FOLLOWERS = "followers"


class MediaType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class NotificationType(str, enum.Enum):
    LIKE = "like"
    COMMENT = "comment"
    FOLLOW = "follow"
    MENTION = "mention"
    SYSTEM = "system"


class EntityName(str, enum.Enum):
    """Canonical names of the shardable social-graph entities.

    The configured shard-key mapping (see router config) maps each entity to
    the column that should be hashed. Access-pattern-aware — do NOT blindly
    hash the primary key for every table.
    """

    USERS = "users"
    PROFILES = "profiles"
    POSTS = "posts"
    COMMENTS = "comments"
    LIKES = "likes"
    FOLLOWS = "follows"
    MEDIA = "media"
    NOTIFICATIONS = "notifications"
