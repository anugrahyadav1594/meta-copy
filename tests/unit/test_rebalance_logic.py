"""Planner/verifier logic that does not require a live database."""

from __future__ import annotations

import pytest
from common.enums import RoutingStrategy
from rebalance.planner import (
    LOCALITY_GROUPS,
    RebalancePlanner,
    key_column_for,
)
from rebalance.verifier import ChecksumVerifier
from router.shard_router import ShardRouter

FOUR = [f"shard-{i}" for i in range(4)]


def test_modulo_vs_consistent_expected_movement():
    planner = RebalancePlanner(ShardRouter(FOUR, strategy=RoutingStrategy.HASH))
    assert planner.expected_modulo_movement(4, 5) == pytest.approx(0.8)
    assert planner.expected_consistent_movement(4) == pytest.approx(0.2)


def test_key_column_mapping():
    assert key_column_for("posts") == "author_id"
    assert key_column_for("likes") == "post_id"
    with pytest.raises(KeyError):
        key_column_for("nope")


def test_locality_groups_exist_for_core_tables():
    assert LOCALITY_GROUPS["users"][0] == ("users", "id")


def test_shadow_ring_does_not_mutate_live_router():
    router = ShardRouter(FOUR, strategy=RoutingStrategy.CONSISTENT_HASH)
    planner = RebalancePlanner(router)
    shadow = planner._shadow_router_with("shard-4")
    assert shadow.shard_count == 5
    assert router.shard_count == 4  # live ring untouched during planning


@pytest.mark.asyncio
async def test_verifier_rejects_unknown_table():
    verifier = ChecksumVerifier()
    with pytest.raises(ValueError):
        await verifier.checksum_for_keys(
            session=None, table="secrets; DROP TABLE users;", key_column="id", keys=[1]
        )
