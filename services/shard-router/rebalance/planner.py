"""Rebalance planning: decide which keys must move when topology changes.

Adding shard N under consistent hashing only moves keys whose new clockwise
vnode owner is the new shard (~1/(N+1) of keys, spread across old shards).
Under modulo hashing nearly every key's bucket changes — the planner measures
the real difference rather than asserting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.enums import RoutingStrategy
from router.shard_router import DEFAULT_ENTITY_SHARD_KEYS, ShardRouter

# A table is migrated together with the co-located tables of its locality
# group — rows must be inserted child-after-parent and deleted parent-last.
LOCALITY_GROUPS: dict[str, list[tuple[str, str]]] = {
    "users": [("users", "id"), ("profiles", "user_id")],
    "posts": [("posts", "author_id")],
    "comments": [("comments", "post_id"), ("likes", "post_id")],
    "notifications": [("notifications", "recipient_id")],
    "media": [("media", "owner_id")],
    "follows": [("follows", "follower_id")],
}

PRIMARY_KEY: dict[str, str] = {
    "users": "id",
    "profiles": "id",
    "posts": "id",
    "comments": "id",
    "likes": "id",
    "follows": "follower_id",  # composite PK; first component used for checksums
    "media": "id",
    "notifications": "id",
}


def key_column_for(table: str) -> str:
    for entity, column in DEFAULT_ENTITY_SHARD_KEYS.items():
        if entity.value == table:
            return column
    raise KeyError(f"unknown table {table}")


@dataclass
class TableMove:
    table: str
    key_column: str
    # (source_shard, destination_shard) -> shard-key values to relocate
    routes: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    @property
    def keys_to_move(self) -> int:
        return sum(len(keys) for keys in self.routes.values())

    def sources(self) -> list[str]:
        return sorted({src for src, _ in self.routes})


@dataclass
class RebalancePlan:
    new_shard_id: str
    strategy: RoutingStrategy
    tables: list[TableMove]
    dry_run: bool = False

    @property
    def keys_to_move(self) -> int:
        # keys of the anchor table (e.g. users) drive the move
        return self.tables[0].keys_to_move if self.tables else 0

    def describe(self) -> dict[str, Any]:
        return {
            "new_shard_id": self.new_shard_id,
            "strategy": self.strategy.value,
            "tables": [
                {
                    "table": t.table,
                    "key_column": t.key_column,
                    "keys_to_move": t.keys_to_move,
                    "per_route": {
                        f"{src}->{dst}": len(keys) for (src, dst), keys in t.routes.items()
                    },
                }
                for t in self.tables
            ],
            "dry_run": self.dry_run,
        }


class RebalancePlanner:
    """Builds plans against a *shadow* ring (live routing is untouched)."""

    def __init__(self, router: ShardRouter) -> None:
        self.router = router

    def _shadow_router_with(self, new_shard_id: str) -> ShardRouter:
        shadow = ShardRouter(
            self.router.shard_ids,
            strategy=self.router.strategy,
            virtual_nodes_per_shard=self.router.virtual_nodes_per_shard,
        )
        shadow.add_shard(new_shard_id)
        return shadow

    async def plan_for_add(
        self,
        new_shard_id: str,
        *,
        table: str = "users",
        manager: Any | None = None,
        dry_run: bool = False,
    ) -> RebalancePlan:
        """Inspect real rows on current shards and compute concrete moves."""
        if new_shard_id in self.router.shard_ids:
            raise ValueError(f"{new_shard_id} already part of the ring")

        shadow = self._shadow_router_with(new_shard_id)
        group = LOCALITY_GROUPS.get(table, [(table, key_column_for(table))])
        moves: list[TableMove] = []

        for tbl, key_col in group:
            tm = TableMove(table=tbl, key_column=key_col)
            # Only the anchor table needs to scan keys; co-located tables
            # inherit the same key set routed on the same column semantics.
            keys_by_shard: dict[str, list[int]] = {}
            if manager is not None:
                keys_by_shard = await self._collect_keys(manager, tbl, key_col)
            for src, keys in keys_by_shard.items():
                for key in keys:
                    dst = shadow.shard_for(key)
                    if dst == new_shard_id and dst != src:
                        tm.routes.setdefault((src, dst), []).append(key)
            moves.append(tm)

        # Make co-located tables reuse the anchor table's key values (they
        # key on a different column, e.g. profiles.user_id == users.id).
        anchor = moves[0]
        for tm in moves[1:]:
            for (src, dst), keys in list(anchor.routes.items()):
                tm.routes[(src, dst)] = list(keys)
        return RebalancePlan(
            new_shard_id=new_shard_id,
            strategy=self.router.strategy,
            tables=moves,
            dry_run=dry_run,
        )

    async def _collect_keys(self, manager: Any, table: str, key_col: str) -> dict[str, list[int]]:
        """Fetch existing shard-key values from each live shard."""
        from models import Base
        from sqlalchemy import select

        table_obj = Base.metadata.tables[table]
        column = table_obj.c[key_col]
        out: dict[str, list[int]] = {}
        for sid in self.router.shard_ids:
            async with manager.session_scope(sid, readonly=True) as session:
                # DISTINCT shard keys present on this shard.
                res = await session.execute(select(column).distinct())
                values = [row[0] for row in res if row[0] is not None]
            out[sid] = sorted(values)
        return out

    @staticmethod
    def expected_modulo_movement(old_n: int, new_n: int) -> float:
        """For hash%N -> hash%M, expected fraction of keys that change shard."""
        return 1.0 - (1.0 / new_n)

    @staticmethod
    def expected_consistent_movement(old_n: int) -> float:
        return 1.0 / (old_n + 1)
