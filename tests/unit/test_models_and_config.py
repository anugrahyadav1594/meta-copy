"""Model metadata, config, and ID generator unit tests."""

from __future__ import annotations

from common.config import Settings
from common.enums import DeploymentMode
from db.ids import SequentialIdGenerator, SnowflakeIdGenerator
from models import Base

EXPECTED_TABLES = {
    "users",
    "profiles",
    "posts",
    "comments",
    "likes",
    "follows",
    "media",
    "notifications",
}


def test_canonical_tables_complete():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_model_column_shapes():
    users = Base.metadata.tables["users"]
    assert {c.name for c in users.columns} == {
        "id",
        "username",
        "email",
        "password_hash",
        "created_at",
        "updated_at",
    }
    assert users.c.id.type.__class__.__name__ == "BigInteger"
    notifications = Base.metadata.tables["notifications"]
    assert notifications.c.actor_id.nullable is True
    assert notifications.c.recipient_id.nullable is False


def test_fk_policy_matches_colocation_design():
    # hard FKs kept on co-located shard keys
    fks = {
        (tbl, fk.parent.name): fk.column.table.name
        for tbl, table in Base.metadata.tables.items()
        for fk in table.foreign_keys
    }
    assert fks[("posts", "author_id")] == "users"
    assert fks[("comments", "post_id")] == "posts"
    assert fks[("likes", "post_id")] == "posts"
    assert fks[("follows", "follower_id")] == "users"
    assert fks[("notifications", "recipient_id")] == "users"
    # cross-shard references deliberately have NO fk
    comment_cols = {fk.parent.name for fk in Base.metadata.tables["comments"].foreign_keys}
    assert "user_id" not in comment_cols
    follow_cols = {fk.parent.name for fk in Base.metadata.tables["follows"].foreign_keys}
    assert "following_id" not in follow_cols


def test_snowflake_ids_unique_and_monotone():
    gen = SnowflakeIdGenerator(slot=3)
    ids = [gen.next_id() for _ in range(5000)]
    assert len(set(ids)) == 5000
    assert ids == sorted(ids)
    assert all(i > 0 for i in ids)


def test_sequential_generator():
    g = SequentialIdGenerator()
    assert [g.next_id() for _ in range(3)] == [1, 2, 3]


def test_settings_defaults_and_override():
    s = Settings(mode="sharded", shard_count=5, shard_0_url="postgresql+psycopg://x/y")
    assert s.mode == DeploymentMode.SHARDED
    assert s.sharding_active is True
    urls = s.shard_urls()
    assert urls["shard-0"] == "postgresql+psycopg://x/y"


def test_only_normalized_and_sharded_are_implemented():
    assert DeploymentMode.NORMALIZED.is_implemented
    assert DeploymentMode.SHARDED.is_implemented
    assert not DeploymentMode.DENORMALIZED.is_implemented
    assert not DeploymentMode.FULL_DISTRIBUTED.is_implemented
