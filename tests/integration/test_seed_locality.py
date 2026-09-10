"""Deterministic seeding + shard co-location invariants."""

from __future__ import annotations

import pytest
from common.enums import RoutingStrategy
from db.shard_engine import ShardEngineManager
from models import Base, Comment, Follow, Like, Media, Notification, Post, Profile, User
from router.shard_router import ShardRouter
from sqlalchemy.dialects.postgresql import insert as pg_insert

from scripts._dataset import DatasetBuilder

pytestmark = pytest.mark.integration

KEY_COLS = {
    "users": "id",
    "media": "owner_id",
    "profiles": "user_id",
    "posts": "author_id",
    "follows": "follower_id",
    "comments": "post_id",
    "likes": "post_id",
    "notifications": "recipient_id",
}
ORDER = ["users", "media", "profiles", "posts", "follows", "comments", "likes", "notifications"]


@pytest.mark.asyncio
async def test_deterministic_rows_and_shard_locality(infra, clean):
    settings, _, urls = infra
    shard_urls = {f"shard-{i}": urls[f"shard-{i}"] for i in range(4)}
    router = ShardRouter(
        list(shard_urls), strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    manager = ShardEngineManager(shard_urls, settings)

    # determinism: same seed -> same rows
    b1 = DatasetBuilder(
        "small",
        seed=42,
        overrides={
            "users": 30,
            "posts": 120,
            "comments": 300,
            "likes": 400,
            "follows": 100,
            "media": 40,
            "notifications": 200,
        },
    ).build(router)
    b2 = DatasetBuilder(
        "small",
        seed=42,
        overrides={
            "users": 30,
            "posts": 120,
            "comments": 300,
            "likes": 400,
            "follows": 100,
            "media": 40,
            "notifications": 200,
        },
    ).build(router)
    assert b1 == b2

    # place rows by router decision
    for name in ORDER:
        buckets: dict[str, list[dict]] = {sid: [] for sid in shard_urls}
        key = KEY_COLS[name]
        for row in b1[name]:
            buckets[router.shard_for(row[key])].append(row)
        for sid, rows in buckets.items():
            for i in range(0, len(rows), 500):
                async with manager.session_scope(sid) as session:
                    await session.execute(
                        pg_insert(Base.metadata.tables[name]).on_conflict_do_nothing(),
                        rows[i : i + 500],
                    )

    # invariant 1: every post is physically found on route(post.id)'s shard
    for post in b1["posts"]:
        sid = router.shard_for(post["id"])
        async with manager.session_scope(sid, readonly=True) as session:
            found = await session.get(Post, post["id"])
        assert found is not None and found.author_id == post["author_id"]

    # invariant 2: comments & likes co-located with their post
    for comment in b1["comments"]:
        sid = router.shard_for(comment["post_id"])
        async with manager.session_scope(sid, readonly=True) as session:
            assert await session.get(Comment, comment["id"]) is not None
    for like in b1["likes"]:
        sid = router.shard_for(like["post_id"])
        async with manager.session_scope(sid, readonly=True) as session:
            exists = (await session.get(Like, like["id"])) is not None
        assert exists

    # invariant 3: users+profiles, media, follows, notifications co-located
    for profile in b1["profiles"]:
        sid = router.shard_for(profile["user_id"])
        async with manager.session_scope(sid, readonly=True) as session:
            assert await session.get(Profile, profile["id"]) is not None
            assert await session.get(User, profile["user_id"]) is not None
    for media in b1["media"]:
        sid = router.shard_for(media["owner_id"])
        async with manager.session_scope(sid, readonly=True) as session:
            assert await session.get(Media, media["id"]) is not None
    for follow in b1["follows"]:
        sid = router.shard_for(follow["follower_id"])
        async with manager.session_scope(sid, readonly=True) as session:
            row = await session.get(
                Follow,
                {"follower_id": follow["follower_id"], "following_id": follow["following_id"]},
            )
            assert row is not None
    for notif in b1["notifications"]:
        sid = router.shard_for(notif["recipient_id"])
        async with manager.session_scope(sid, readonly=True) as session:
            assert await session.get(Notification, notif["id"]) is not None

    # invariant 4: rows are genuinely spread across ALL shards
    for name in ("users", "posts"):
        owners = {router.shard_for(row[KEY_COLS[name]]) for row in b1[name]}
        assert owners == set(shard_urls), (name, owners)

    await manager.dispose()


def test_dataset_size_profiles():
    small = DatasetBuilder("small", seed=1).build(None)
    assert len(small["users"]) == 100
    assert len(small["posts"]) == 1_000
    assert len(small["likes"]) == 10_000
    assert small["users"][0]["username"]
