"""Consistent-hash ring with virtual nodes.

Compared to ``hash(key) % N``, consistent hashing ensures that adding or
removing a shard only relocates roughly ``1/(N+1)`` of the keys instead of
~``(N-1)/N`` of them.

The ring is a sorted list of vnode positions (0..2^32-1). A key is owned by
the first vnode encountered while walking clockwise from the key's position.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from router.hash_router import stable_hash
from router.virtual_nodes import VirtualNodeLayout


@dataclass(frozen=True)
class RingRoute:
    key: str
    shard_id: str
    position: int
    vnode: str
    shard_count: int
    strategy: str = "consistent_hash"


class ConsistentHashRing:
    def __init__(
        self,
        shard_ids: list[str],
        *,
        virtual_nodes_per_shard: int = 150,
    ) -> None:
        self.layout = VirtualNodeLayout(virtual_nodes_per_shard)
        self._shard_ids: list[str] = []
        self._positions: list[int] = []
        self._owners: list[str] = []  # parallel to _positions
        self._vnode_names: list[str] = []
        for sid in shard_ids:
            self.add_shard(sid, rebuild=False)
        self._rebuild()

    # ------------------------------------------------------------------ ring
    def _rebuild(self) -> None:
        vnodes = self.layout.build(self._shard_ids)
        self._positions = [v.position for v in vnodes]
        self._owners = [v.shard_id for v in vnodes]
        self._vnode_names = [v.name for v in vnodes]

    @property
    def shard_ids(self) -> list[str]:
        return list(self._shard_ids)

    @property
    def shard_count(self) -> int:
        return len(self._shard_ids)

    @property
    def virtual_nodes_per_shard(self) -> int:
        return self.layout.vnodes_per_shard

    def add_shard(self, shard_id: str, *, rebuild: bool = True) -> None:
        if shard_id in self._shard_ids:
            return
        self._shard_ids.append(shard_id)
        self._shard_ids.sort(key=lambda s: int(s.split("-")[1]) if "-" in s else s)
        if rebuild:
            self._rebuild()

    def remove_shard(self, shard_id: str, *, rebuild: bool = True) -> None:
        if shard_id not in self._shard_ids:
            return
        self._shard_ids.remove(shard_id)
        if rebuild:
            self._rebuild()

    def virtual_node_count(self, shard_id: str | None = None) -> int:
        if shard_id is None:
            return len(self._positions)
        return self._owners.count(shard_id)

    # --------------------------------------------------------------- routing
    def route(self, key: int | str) -> RingRoute:
        if not self._shard_ids:
            raise ValueError("ring is empty")
        pos = stable_hash(key)
        idx = bisect_left(self._positions, pos)
        if idx == len(self._positions):  # wrap around past the top
            idx = 0
        return RingRoute(
            key=str(key),
            shard_id=self._owners[idx],
            position=self._positions[idx],
            vnode=self._vnode_names[idx],
            shard_count=len(self._shard_ids),
        )

    def shard_for(self, key: int | str) -> str:
        return self.route(key).shard_id

    def distribution(self, keys: list[int | str]) -> dict[str, float]:
        """Measured percentage (0..100) of keys landing on each shard."""
        counts = {sid: 0 for sid in self._shard_ids}
        for k in keys:
            counts[self.shard_for(k)] += 1
        total = max(len(keys), 1)
        return {sid: round(100.0 * c / total, 2) for sid, c in counts.items()}

    # -------------------------------------------------------- key movement
    def planned_movement_for_add(self, shard_id: str) -> dict[str, object]:
        """Return which existing shards would surrender keys if added now.

        Keys are not required: with consistent hashing the new shard takes
        ownership of the arc segments immediately clockwise of each of its
        vnodes, which (before the fact) belonged to its clockwise neighbours.
        The returned ``assign`` function lets callers evaluate concrete keys.
        """
        if shard_id in self._shard_ids:
            return {"moved": {}, "assign": self.shard_for}

        from bisect import bisect_left as _bisect

        def owner_from(positions: list[int], owners: list[str], k: int | str) -> str:
            pos = stable_hash(k)
            idx = _bisect(positions, pos)
            if idx == len(positions):
                idx = 0
            return owners[idx]

        old_positions, old_owners = list(self._positions), list(self._owners)
        snapshot = list(self._shard_ids)

        def old_owner_fn(k: int | str) -> str:
            return owner_from(old_positions, old_owners, k)

        self.add_shard(shard_id)
        try:
            new_positions, new_owners = list(self._positions), list(self._owners)

            def new_owner(k: int | str) -> str:
                return owner_from(new_positions, new_owners, k)

            def moved_for(keys: list[int | str]) -> dict[str, int]:
                moved: dict[str, int] = {}
                for k in keys:
                    before = old_owner_fn(k)
                    after = new_owner(k)
                    if before != after:
                        moved[before] = moved.get(before, 0) + 1
                return moved

            return {
                "old_shards": snapshot,
                "new_shard": shard_id,
                "assign": new_owner,
                "old_assign": old_owner_fn,
                "moved_for": moved_for,
            }
        finally:
            self.remove_shard(shard_id)
