"""Top-level ShardRouter.

Combines the modulo :class:`HashRouter` and the
:class:`ConsistentHashRing`, and holds the **access-pattern-aware** entity →
shard-key mapping. The mapping is configurable (constructor argument) but
defaults to the documented partition design:

=========== =============== ===============================================
Entity      Shard key       Why
=========== =============== ===============================================
users       id              user rows are addressed directly by user id
profiles    user_id         co-located with the user (1:1)
posts       author_id       author timeline locality; co-locates with user
comments    post_id         co-locates with the post they belong to
likes       post_id         co-locates with the post (like list)
follows     follower_id     a user's *outgoing* follows stay together
media       owner_id        a user's uploads stay together
notif.      recipient_id    the recipient's inbox stays together
=========== =============== ===============================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.enums import EntityName, RoutingStrategy

from router.consistent_hash import ConsistentHashRing
from router.hash_router import HashRouter, stable_hash

DEFAULT_ENTITY_SHARD_KEYS: dict[EntityName, str] = {
    EntityName.USERS: "id",
    EntityName.PROFILES: "user_id",
    EntityName.POSTS: "author_id",
    EntityName.COMMENTS: "post_id",
    EntityName.LIKES: "post_id",
    EntityName.FOLLOWS: "follower_id",
    EntityName.MEDIA: "owner_id",
    EntityName.NOTIFICATIONS: "recipient_id",
}


@dataclass(frozen=True)
class RouteDecision:
    entity: str | None
    shard_key: str | None
    key: str
    shard_id: str
    strategy: RoutingStrategy
    shard_count: int
    bucket: int | None = None
    virtual_node: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class ShardRouter:
    def __init__(
        self,
        shard_ids: list[str],
        *,
        strategy: RoutingStrategy = RoutingStrategy.CONSISTENT_HASH,
        virtual_nodes_per_shard: int = 150,
        entity_shard_keys: dict[EntityName, str] | None = None,
        hash_algo: str = "murmur3",
    ) -> None:
        if not shard_ids:
            raise ValueError("ShardRouter requires at least one shard")
        self._shard_ids = list(shard_ids)
        self.strategy = strategy
        self.hash_router = HashRouter(self._shard_ids, algo=hash_algo)
        self.ring = ConsistentHashRing(
            self._shard_ids, virtual_nodes_per_shard=virtual_nodes_per_shard
        )
        self.entity_shard_keys = dict(entity_shard_keys or DEFAULT_ENTITY_SHARD_KEYS)

    # ------------------------------------------------------------- topology
    @property
    def shard_ids(self) -> list[str]:
        return list(self._shard_ids)

    @property
    def shard_count(self) -> int:
        return len(self._shard_ids)

    @property
    def virtual_nodes_per_shard(self) -> int:
        return self.ring.virtual_nodes_per_shard

    def add_shard(self, shard_id: str) -> dict[str, Any]:
        """Register a shard in BOTH routing strategies.

        Returns movement-analysis callables for the rebalance planner.
        """
        if shard_id in self._shard_ids:
            return {"changed": False}
        movement = self.ring.planned_movement_for_add(shard_id)
        self._shard_ids.append(shard_id)
        self._shard_ids.sort(key=lambda s: int(s.split("-")[1]) if "-" in s else s)
        self.ring.add_shard(shard_id)
        self.hash_router.set_shards(self._shard_ids)
        return {"changed": True, "movement": movement}

    def remove_shard(self, shard_id: str) -> None:
        if shard_id not in self._shard_ids:
            return
        self._shard_ids.remove(shard_id)
        self.ring.remove_shard(shard_id)
        self.hash_router.set_shards(self._shard_ids)

    def set_strategy(self, strategy: RoutingStrategy | str) -> None:
        if isinstance(strategy, str):
            strategy = RoutingStrategy(strategy)
        self.strategy = strategy

    # ----------------------------------------------------------- key mapping
    def shard_key_for(self, entity: EntityName | str) -> str:
        if isinstance(entity, str):
            entity = EntityName(entity)
        if entity not in self.entity_shard_keys:
            raise KeyError(f"No shard-key mapping configured for entity '{entity}'")
        return self.entity_shard_keys[entity]

    # --------------------------------------------------------------- routing
    def route(
        self,
        key: int | str,
        *,
        strategy: RoutingStrategy | str | None = None,
    ) -> RouteDecision:
        strategy = self._resolve_strategy(strategy)
        if strategy == RoutingStrategy.HASH:
            r = self.hash_router.route(key)
            return RouteDecision(
                entity=None,
                shard_key=None,
                key=str(key),
                shard_id=r.shard_id,
                strategy=RoutingStrategy.HASH,
                shard_count=r.shard_count,
                bucket=r.bucket,
            )
        r = self.ring.route(key)
        return RouteDecision(
            entity=None,
            shard_key=None,
            key=str(key),
            shard_id=r.shard_id,
            strategy=RoutingStrategy.CONSISTENT_HASH,
            shard_count=r.shard_count,
            virtual_node=r.vnode,
        )

    def shard_for(
        self,
        key: int | str,
        *,
        strategy: RoutingStrategy | str | None = None,
    ) -> str:
        return self.route(key, strategy=strategy).shard_id

    def route_entity(
        self,
        entity: EntityName | str,
        key_value: int | str,
        *,
        strategy: RoutingStrategy | str | None = None,
    ) -> RouteDecision:
        if isinstance(entity, str):
            entity = EntityName(entity)
        column = self.shard_key_for(entity)
        decision = self.route(key_value, strategy=strategy)
        object.__setattr__(decision, "entity", entity.value)
        object.__setattr__(decision, "shard_key", column)
        return decision

    def route_row(self, entity: EntityName | str, row: dict[str, Any]) -> RouteDecision:
        """Route a full row using the entity's configured shard-key column."""
        if isinstance(entity, str):
            entity = EntityName(entity)
        column = self.shard_key_for(entity)
        if column not in row:
            raise KeyError(f"Row for '{entity.value}' is missing shard-key column '{column}'")
        return self.route_entity(entity, row[column])

    # ------------------------------------------------------ key alignment
    def generate_key_routed_to(
        self,
        target_shard: str,
        *,
        base: int | None = None,
        max_attempts: int = 100_000,
        strategy: RoutingStrategy | str | None = None,
    ) -> int:
        """Find an integer key that routes to ``target_shard``.

        Used to keep *derived* rows co-located: e.g. a post's shard key is
        ``author_id``, but the post is later read by its own primary key, so
        the post id is chosen such that ``route(post.id) == route(author_id)``.
        """
        if target_shard not in self._shard_ids:
            raise KeyError(f"Unknown target shard {target_shard!r}")
        strategy = self._resolve_strategy(strategy)
        candidate = base if base is not None else (stable_hash(target_shard) & 0x3FFFFFFF)
        for _ in range(max_attempts):
            if self.shard_for(candidate, strategy=strategy) == target_shard:
                return candidate
            candidate += 1
        raise RuntimeError(
            f"Could not find a key routing to {target_shard} in {max_attempts} attempts"
        )

    # ------------------------------------------------------------- analysis
    def distribution(
        self,
        keys: list[int | str],
        *,
        strategy: RoutingStrategy | str | None = None,
    ) -> dict[str, float]:
        strategy = self._resolve_strategy(strategy)
        if strategy == RoutingStrategy.HASH:
            return self.hash_router.distribution(keys)
        return self.ring.distribution(keys)

    def _resolve_strategy(self, strategy: RoutingStrategy | str | None) -> RoutingStrategy:
        if strategy is None:
            return self.strategy
        if isinstance(strategy, RoutingStrategy):
            return strategy
        return RoutingStrategy(strategy)
