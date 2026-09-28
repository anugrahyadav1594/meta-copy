"""Unit tests for the consistent-hash ring and virtual nodes."""

from __future__ import annotations

from common.enums import RoutingStrategy
from router.consistent_hash import ConsistentHashRing
from router.shard_router import ShardRouter

FOUR = [f"shard-{i}" for i in range(4)]
KEYS = list(range(1, 100_001))


def test_ring_deterministic():
    a = ConsistentHashRing(FOUR, virtual_nodes_per_shard=150)
    b = ConsistentHashRing(FOUR, virtual_nodes_per_shard=150)
    for k in (1, 42, 101, 99999, "user:77"):
        assert a.shard_for(k) == b.shard_for(k)


def test_every_shard_has_configured_vnodes():
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=123)
    for sid in FOUR:
        assert ring.virtual_node_count(sid) == 123
    assert ring.virtual_node_count() == 4 * 123


def test_all_shards_receive_keys():
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=150)
    owners = {ring.shard_for(k) for k in KEYS[:20_000]}
    assert owners == set(FOUR)


def test_add_shard_moves_only_about_fair_share():
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=150)
    before = {k: ring.shard_for(k) for k in KEYS}
    ring.add_shard("shard-4")
    moved = sum(1 for k in KEYS if before[k] != ring.shard_for(k))
    pct = moved / len(KEYS) * 100
    # expect roughly 1/(N+1) = 20% with comfortable tolerance
    assert 8.0 < pct < 32.0, pct


def test_remove_shard_restores_ownership():
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=150)
    original = {k: ring.shard_for(k) for k in KEYS[:20_000]}
    ring.add_shard("shard-4")
    ring.remove_shard("shard-4")
    restored = sum(1 for k in KEYS[:20_000] if ring.shard_for(k) == original[k])
    assert restored == len(KEYS[:20_000])


def test_virtual_nodes_materially_improve_distribution():
    import statistics

    def stdev(vnodes: int) -> float:
        ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=vnodes)
        counts = {sid: 0 for sid in FOUR}
        for k in KEYS:
            counts[ring.shard_for(k)] += 1
        pcts = [100 * c / len(KEYS) for c in counts.values()]
        return statistics.pstdev(pcts)

    one_vnode = stdev(1)
    many_vnodes = stdev(150)
    assert many_vnodes < one_vnode / 3, (one_vnode, many_vnodes)
    # 150 vnodes should be very close to fair (25%)
    assert many_vnodes < 1.0


def test_shard_router_key_alignment():
    router = ShardRouter(
        FOUR, strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    # A post id aligned to author 123's shard must route exactly there.
    author_shard = router.shard_for(123)
    aligned = router.generate_key_routed_to(author_shard, base=20_000_000)
    assert router.shard_for(aligned) == author_shard
    assert aligned > 0


def test_shard_router_key_alignment_hash_strategy():
    router = ShardRouter(FOUR, strategy=RoutingStrategy.HASH)
    for author in range(1, 50):
        target = router.shard_for(author)
        aligned = router.generate_key_routed_to(target, base=20_000_000 + author * 10)
        assert router.shard_for(aligned) == target
