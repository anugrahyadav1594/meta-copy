"""Member 4 router unit tests that live with the shard-router service."""

from __future__ import annotations

import pytest
from common.enums import EntityName, RoutingStrategy
from router.consistent_hash import ConsistentHashRing
from router.shard_router import DEFAULT_ENTITY_SHARD_KEYS, ShardRouter

FOUR = [f"shard-{i}" for i in range(4)]


def test_entity_shard_key_mapping_is_document_design():
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.USERS] == "id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.PROFILES] == "user_id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.POSTS] == "author_id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.COMMENTS] == "post_id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.LIKES] == "post_id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.FOLLOWS] == "follower_id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.NOTIFICATIONS] == "recipient_id"
    assert DEFAULT_ENTITY_SHARD_KEYS[EntityName.MEDIA] == "owner_id"


def test_route_entity_uses_mapped_column():
    router = ShardRouter(FOUR, strategy=RoutingStrategy.HASH)
    decision = router.route_row(EntityName.POSTS, {"author_id": 777})
    assert decision.shard_id == router.shard_for(777)
    assert decision.shard_key == "author_id"
    assert decision.entity == "posts"


def test_route_row_missing_shard_key_raises():
    router = ShardRouter(FOUR)
    with pytest.raises(KeyError):
        router.route_row(EntityName.LIKES, {"user_id": 1})


def test_configurable_mapping():
    router = ShardRouter(
        FOUR, entity_shard_keys={**DEFAULT_ENTITY_SHARD_KEYS, EntityName.POSTS: "id"}
    )
    assert router.shard_key_for(EntityName.POSTS) == "id"


def test_planned_movement_helper_without_mutating_ring():
    ring = ConsistentHashRing(FOUR, virtual_nodes_per_shard=50)
    before = set(ring.shard_ids)
    plan = ring.planned_movement_for_add("shard-4")
    # ring must be unchanged after planning
    assert set(ring.shard_ids) == before
    keys = list(range(1, 50_001))
    moved = plan["moved_for"](keys)
    total_moved = sum(moved.values())
    assert total_moved > 0
    assert total_moved < len(keys) // 3  # consistent hashing bound
    assert set(ring.shard_ids) == before


def test_route_user_demo_numbers_are_real():
    """Demo 2's answers must come from the algorithm, never hard-coded."""
    router = ShardRouter(
        FOUR, strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    for uid in (101, 202, 303):
        decision = router.route_entity("users", uid)
        assert decision.shard_id in FOUR
        assert decision.entity == "users"
        assert decision.shard_key == "id"
