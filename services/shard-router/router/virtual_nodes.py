"""Virtual node (vnode) placement for the consistent-hash ring.

A physical shard gets ``V`` points on the ring (``shard-2#vn0007`` etc.).
More vnodes -> each shard owns many small arc segments -> dramatically more
even key distribution and smoother data movement on topology changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from router.hash_router import stable_hash


def vnode_name(shard_id: str, index: int) -> str:
    return f"{shard_id}#vn{index:04d}"


@dataclass(frozen=True)
class VirtualNode:
    shard_id: str
    index: int
    position: int

    @property
    def name(self) -> str:
        return vnode_name(self.shard_id, self.index)


class VirtualNodeLayout:
    """Generates deterministic vnode placements for a set of shards."""

    def __init__(self, virtual_nodes_per_shard: int = 150) -> None:
        if virtual_nodes_per_shard < 1:
            raise ValueError("virtual_nodes_per_shard must be >= 1")
        self.vnodes_per_shard = virtual_nodes_per_shard

    def build(self, shard_ids: list[str]) -> list[VirtualNode]:
        nodes: list[VirtualNode] = []
        for sid in shard_ids:
            for i in range(self.vnodes_per_shard):
                name = vnode_name(sid, i)
                # Hash the vnode NAME (not just the shard id) so that the
                # V replicas land at distinct, well-spread ring positions.
                nodes.append(VirtualNode(sid, i, stable_hash(name)))
        nodes.sort(key=lambda vn: vn.position)
        return nodes

    def positions_for(self, shard_id: str) -> list[int]:
        return [stable_hash(vnode_name(shard_id, i)) for i in range(self.vnodes_per_shard)]
