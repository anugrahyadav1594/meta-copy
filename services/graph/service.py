"""Member 9 — TAO-inspired social graph (re-architected).

The original ``abhishek-member-9`` branch used **SQLite as an independent
source of truth** (``better-sqlite3`` + its own users/posts/follows tables) in
a separate Node.js service. That is exactly what the integrated architecture
forbids, so the graph was re-implemented here:

* reads go through the canonical ``FollowRepository`` (PostgreSQL, sharded or
  not — the graph never learns the topology);
* the adjacency sets kept in memory are a **derived projection** with a TTL,
  rebuilt from PostgreSQL on demand. They can be dropped at any time without
  losing data: ``rebuild()`` restores them;
* no second database, no second runtime.

The API surface mirrors TAO's association API (``/followers``, ``/following``,
``/mutuals/{other}``, plus edge counts and 2-hop suggestions).
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from common.logging import get_logger

logger = get_logger("graph")


class FollowRepo(Protocol):
    async def follower_ids(self, user_id: int, limit: int = 5_000) -> list[int]: ...

    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]: ...

    async def list_followers(self, user_id: int, limit: int = 100) -> list[Any]: ...

    async def list_following(self, user_id: int, limit: int = 100) -> list[Any]: ...

    async def counts(self, user_id: int) -> tuple[int, int]: ...

    async def is_following(self, follower_id: int, following_id: int) -> bool: ...


class SocialGraph:
    """TAO-inspired graph access over canonical PostgreSQL.

    ``cache_ttl_seconds = 0`` disables the derived adjacency cache entirely
    (every call hits PostgreSQL) — useful for demonstrating that the cache is
    an optimisation, not a source of truth.
    """

    def __init__(self, follow_repo: FollowRepo, cache_ttl_seconds: int = 30) -> None:
        self.follows = follow_repo
        self.cache_ttl = cache_ttl_seconds
        self._following: dict[int, tuple[float, set[int]]] = {}
        self._followers: dict[int, tuple[float, set[int]]] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.rebuilds = 0

    # ------------------------------------------------------------- internals
    def _fresh(self, entry: tuple[float, set[int]] | None) -> set[int] | None:
        if entry is None:
            return None
        stored_at, value = entry
        if self.cache_ttl > 0 and (time.monotonic() - stored_at) <= self.cache_ttl:
            return value
        return None

    async def _following_set(self, user_id: int) -> set[int]:
        cached = self._fresh(self._following.get(user_id))
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.cache_misses += 1
        ids = set(await self.follows.following_ids(user_id))
        self._following[user_id] = (time.monotonic(), ids)
        return ids

    async def _follower_set(self, user_id: int) -> set[int]:
        cached = self._fresh(self._followers.get(user_id))
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.cache_misses += 1
        ids = set(await self.follows.follower_ids(user_id))
        self._followers[user_id] = (time.monotonic(), ids)
        return ids

    def invalidate(self, user_id: int | None = None) -> None:
        if user_id is None:
            self._following.clear()
            self._followers.clear()
        else:
            self._following.pop(user_id, None)
            self._followers.pop(user_id, None)

    # ------------------------------------------------------------------- API
    async def followers(self, user_id: int, limit: int = 100) -> list[int]:
        ids = await self._follower_set(user_id)
        return sorted(ids)[:limit]

    async def following(self, user_id: int, limit: int = 100) -> list[int]:
        ids = await self._following_set(user_id)
        return sorted(ids)[:limit]

    async def mutuals(self, user_id: int, other_user_id: int, limit: int = 100) -> list[int]:
        """Users followed by both (intersection)."""
        mine = await self._following_set(user_id)
        theirs = await self._following_set(other_user_id)
        return sorted(mine & theirs)[:limit]

    async def is_following(self, follower_id: int, following_id: int) -> bool:
        cached = self._fresh(self._following.get(follower_id))
        if cached is not None:
            self.cache_hits += 1
            return following_id in cached
        self.cache_misses += 1
        return await self.follows.is_following(follower_id, following_id)

    async def degrees(self, user_id: int) -> dict[str, int]:
        followers, following = await self.follows.counts(user_id)
        return {"followers": followers, "following": following}

    async def suggestions(self, user_id: int, limit: int = 10) -> list[int]:
        """2-hop 'people you may know': friends-of-friends, ranked by overlap."""
        following = await self._following_set(user_id)
        scores: dict[int, int] = {}
        for friend_id in list(following)[:50]:  # bounded fan-out
            for candidate in await self._following_set(friend_id):
                if candidate == user_id or candidate in following:
                    continue
                scores[candidate] = scores.get(candidate, 0) + 1
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [uid for uid, _ in ranked[:limit]]

    async def edge_count(self, node_id: int, edge_type: str = "following") -> int:
        if edge_type == "followers":
            return len(await self._follower_set(node_id))
        return len(await self._following_set(node_id))

    async def rebuild(self) -> dict[str, int]:
        """Rebuild the derived adjacency projection from canonical data.

        Proves the projection is disposable: everything here comes from
        PostgreSQL ``follows`` rows.
        """
        self.invalidate()
        # Touching both directions of every cached node is O(n) by design; in
        # a real deployment this would stream the edge table. Here it rebuilds
        # lazily on the next access.
        self.rebuilds += 1
        return {
            "derived": True,
            "source_of_truth": "postgresql:follows",
            "cached_nodes_following": len(self._following),
            "cached_nodes_followers": len(self._followers),
            "rebuilds": self.rebuilds,
        }

    def stats(self) -> dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "derived_projection": True,
            "source_of_truth": "postgresql:follows",
            "cache_ttl_seconds": self.cache_ttl,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_ratio": round(self.cache_hits / total, 4) if total else 0.0,
            "cached_nodes": len(self._following) + len(self._followers),
            "rebuilds": self.rebuilds,
        }
