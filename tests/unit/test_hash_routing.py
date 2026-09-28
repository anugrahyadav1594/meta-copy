"""Unit tests for stable hash + modulo routing."""

from __future__ import annotations

import pytest
from common.enums import RoutingStrategy
from router.hash_router import HashRouter, stable_hash
from router.shard_router import ShardRouter

FOUR = [f"shard-{i}" for i in range(4)]


def test_stable_hash_is_deterministic_and_cross_process():
    # Same input always yields the same integer (unlike Python's salted hash).
    assert stable_hash(12345) == stable_hash("12345")
    assert stable_hash("metascale") == stable_hash("metascale")
    assert isinstance(stable_hash(1), int)
    assert stable_hash(1, algo="sha256") == stable_hash(1, algo="sha256")
    assert stable_hash(1, algo="murmur3") != stable_hash(1, algo="sha256")


def test_same_key_always_same_shard():
    routers = [HashRouter(FOUR) for _ in range(5)]
    decisions = {r.shard_for(12345) for r in routers}
    assert len(decisions) == 1


def test_different_keys_distribute_across_all_shards():
    router = HashRouter(FOUR)
    owners = {router.shard_for(k) for k in range(1, 5000)}
    assert owners == set(FOUR)


def test_distribution_is_even():
    router = HashRouter(FOUR)
    dist = router.distribution(list(range(1, 100_001)))
    for pct in dist.values():
        assert 22.0 < pct < 28.0, dist


def test_growing_modulo_shards_moves_most_keys():
    r4 = HashRouter(FOUR)
    r5 = HashRouter(FOUR + ["shard-4"])
    moved = sum(1 for k in range(1, 50_001) if r4.shard_for(k) != r5.shard_for(k))
    # ~80% must move under modulo (1 - 1/5)
    assert 0.74 < moved / 50_000 < 0.86


def test_shard_router_strategy_switch():
    router = ShardRouter(FOUR, strategy=RoutingStrategy.HASH)
    assert router.route(12345).strategy == RoutingStrategy.HASH
    assert router.shard_for(12345, strategy=RoutingStrategy.HASH) == HashRouter(FOUR).shard_for(
        12345
    )


def test_invalid_algo_rejected():
    with pytest.raises(ValueError):
        stable_hash("x", algo="nope")


def test_empty_router_rejected():
    with pytest.raises(ValueError):
        HashRouter([])
