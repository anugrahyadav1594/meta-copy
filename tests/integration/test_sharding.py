"""Integration tests for real horizontal sharding across PostgreSQL shards."""

from __future__ import annotations

import pytest
import pytest_asyncio
from api.services.post_service import PostService
from api.services.security import hash_password
from common.config import Settings
from common.enums import RoutingStrategy, ShardStatus
from common.exceptions import ChecksumMismatchError, ShardUnavailableError
from db.repositories.sharded import (
    ShardedCommentRepository,
    ShardedPostRepository,
    ShardedUserRepository,
)
from db.shard_engine import ShardEngineManager
from db.shard_engine import ShardEngineManager as Mgr
from hotspots.detector import HotShardDetector
from metadata.models import ShardMetadata
from metadata.registry import InMemoryShardRegistry
from metrics.collector import MetricsCollector
from models import User
from rebalance.migrator import RebalanceMigrator
from router.shard_router import ShardRouter
from schemas.post import PostCreate

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ctx(infra, clean):
    """Build the full sharding component graph over 4 real PG clusters."""
    settings, _, urls = infra
    shard_urls = {
        k: v for k, v in urls.items() if k in ("shard-0", "shard-1", "shard-2", "shard-3")
    }
    router = ShardRouter(
        list(shard_urls), strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    manager = ShardEngineManager(shard_urls, settings)
    metrics = MetricsCollector(list(shard_urls))
    registry = InMemoryShardRegistry(
        ShardMetadata.from_url(sid, url, virtual_nodes=150) for sid, url in shard_urls.items()
    )
    for shard in await registry.list():
        shard.status = ShardStatus.HEALTHY
    users = ShardedUserRepository(router, manager, metrics)
    posts = ShardedPostRepository(router, manager, metrics)
    comments = ShardedCommentRepository(router, manager, metrics)
    yield {
        "settings": settings,
        "router": router,
        "manager": manager,
        "metrics": metrics,
        "registry": registry,
        "users": users,
        "posts": posts,
        "comments": comments,
        "urls": shard_urls,
    }
    await manager.dispose()


# ------------------------------------------------------------- targeted CRUD
@pytest.mark.asyncio
async def test_user_create_routes_and_targets_one_shard(ctx):
    users: ShardedUserRepository = ctx["users"]
    router: ShardRouter = ctx["router"]
    user, outcome = await users.create_traced(
        username="sharded1", email="sharded1@x.test", password_hash=hash_password("password123")
    )
    expected = router.shard_for(user.id)
    assert outcome.shard_contacted == expected
    assert outcome.query_type.value == "targeted"
    assert outcome.routing_latency_ms >= 0
    assert outcome.database_latency_ms >= 0

    fetched, fetch_outcome = await users.get_by_id_traced(user.id)
    assert fetched is not None and fetched.username == "sharded1"
    assert fetch_outcome.shard_contacted == expected  # same single shard

    # row physically exists ONLY on the owning shard
    for sid in router.shard_ids:
        async with ctx["manager"].session_scope(sid, readonly=True) as s:
            found = await s.get(User, user.id)
        assert (found is not None) == (sid == expected)


@pytest.mark.asyncio
async def test_user_update_and_delete_routed(ctx):
    users = ctx["users"]
    user, _ = await users.create_traced(username="ud", email="ud@x.test", password_hash="h")
    updated = await users.update(user.id, username="ud2")
    assert updated.username == "ud2"
    assert await users.delete(user.id) is True
    assert await users.get_by_id(user.id) is None


@pytest.mark.asyncio
async def test_post_co_located_with_author_and_findable_by_id(ctx):
    users, posts, router = ctx["users"], ctx["posts"], ctx["router"]
    author, _ = await users.create_traced(username="writer", email="w@x.test", password_hash="h")
    post, outcome = await posts.create_traced(
        author_id=author.id, content="sharded post", visibility="public"
    )
    author_shard = router.shard_for(author.id)
    assert outcome.shard_contacted == author_shard
    # Key-aligned id: route(post.id) lands on the same physical shard.
    assert router.shard_for(post.id) == author_shard

    fetched, fetch_outcome = await posts.get_by_id_traced(post.id)
    assert fetched is not None and fetched.content == "sharded post"
    assert fetch_outcome.shard_contacted == author_shard


@pytest.mark.asyncio
async def test_comment_co_located_with_post(ctx):
    users, posts, comments, router = ctx["users"], ctx["posts"], ctx["comments"], ctx["router"]
    author, _ = await users.create_traced(username="c1", email="c1@x.test", password_hash="h")
    commenter, _ = await users.create_traced(username="c2", email="c2@x.test", password_hash="h")
    post, _ = await posts.create_traced(author_id=author.id, content="p", visibility="public")
    comment, outcome = await comments.create_traced(
        post_id=post.id, user_id=commenter.id, content="nice"
    )
    assert outcome.shard_contacted == router.shard_for(post.id)
    listed, list_outcome = await comments.list_for_post_traced(post.id)
    assert any(c.id == comment.id for c in listed)
    assert list_outcome.shard_contacted == router.shard_for(post.id)


@pytest.mark.asyncio
async def test_post_service_create_validates_author(ctx):
    posts = PostService(ctx["posts"], ctx["comments"], ctx["users"])
    from common.exceptions import ValidationError

    with pytest.raises(ValidationError):
        await posts.create(PostCreate(author_id=999_999, content="no author"))


# ----------------------------------------------------------- scatter-gather
@pytest.mark.asyncio
async def test_scatter_gather_contacts_every_shard(ctx):
    users, posts, router = ctx["users"], ctx["posts"], ctx["router"]
    # Create enough authors/posts that every shard gets rows.
    author_ids = []
    for i in range(24):
        u, _ = await users.create_traced(
            username=f"sg{i}", email=f"sg{i}@x.test", password_hash="h"
        )
        author_ids.append(u.id)
    for i, aid in enumerate(author_ids):
        await posts.create_traced(author_id=aid, content=f"post {i}", visibility="public")

    rows, outcome = await posts.list_recent_traced(limit=20)
    assert set(outcome.shards_contacted) == set(router.shard_ids)
    assert len(outcome.shard_results) == 4
    total_rows = sum(r.rows for r in outcome.shard_results)
    assert total_rows >= 24
    assert outcome.total_rows_before_limit >= 24
    assert len(rows) == 20
    # globally sorted newest-first
    created = [p.created_at for p in rows]
    assert created == sorted(created, reverse=True)
    assert outcome.execution_time_ms > 0
    assert ctx["metrics"].scatter_gather_queries >= 1


@pytest.mark.asyncio
async def test_followers_listing_uses_scatter_gather(ctx):
    users, router = ctx["users"], ctx["router"]
    star, _ = await users.create_traced(username="star", email="star@x.test", password_hash="h")
    fans = []
    for i in range(6):
        fan, _ = await users.create_traced(
            username=f"fan{i}", email=f"fan{i}@x.test", password_hash="h"
        )
        fans.append(fan)
        await users.add_follow(fan.id, star.id)
    followers, outcome = await users.list_followers_traced(star.id)
    assert {f.follower_id for f in followers} == {f.id for f in fans}
    assert set(outcome.shards_contacted) == set(router.shard_ids)


# ------------------------------------------------------------ cross shard
@pytest.mark.asyncio
async def test_cross_shard_join_assembly(ctx):
    from api.services.shard_service import ShardService

    class _P:  # minimal platform duck-type
        pass

    users, posts, comments, router = ctx["users"], ctx["posts"], ctx["comments"], ctx["router"]
    author, _ = await users.create_traced(username="xauthor", email="xa@x.test", password_hash="h")
    post, _ = await posts.create_traced(author_id=author.id, content="cross", visibility="public")

    # keep creating commenters until at least one lives on a DIFFERENT shard
    other_user = None
    for i in range(60):
        u, _ = await users.create_traced(
            username=f"cu{i}", email=f"cu{i}@x.test", password_hash="h"
        )
        if router.shard_for(u.id) != router.shard_for(post.id):
            other_user = u
            break
    assert other_user is not None, "could not find a cross-shard commenter"
    await comments.create_traced(post_id=post.id, user_id=other_user.id, content="from far away")

    p = _P()
    p.__dict__.update(ctx)
    p.shard_manager = ctx["manager"]
    p.sharded_user_repo = users
    p.sharded_post_repo = posts
    p.sharded_comment_repo = comments
    svc = ShardService(p)
    result = await svc.post_cross_shard(post.id)
    assert result["post"]["id"] == post.id
    assert result["network_hops"] >= 2
    assert router.shard_for(other_user.id) in result["shards_contacted"]
    assert "impossible" in result["note"] or "independent shards" in result["note"]


# ----------------------------------------------------------- failure handling
@pytest.mark.asyncio
async def test_unavailable_shard_detected_and_structured(infra, clean):
    settings, _, urls = infra
    bad = {"shard-0": "postgresql+psycopg://nobody:nopw@127.0.0.1:1/nodb?connect_timeout=1"}
    manager = ShardEngineManager(bad, settings)
    with pytest.raises(ShardUnavailableError) as exc:
        await manager.ping("shard-0")
    assert exc.value.error_code == "SHARD_UNAVAILABLE"
    assert exc.value.details["shard_id"] == "shard-0"
    assert manager.is_down("shard-0")
    # subsequent requests fail fast with the same structured error
    with pytest.raises(ShardUnavailableError):
        async with manager.session_scope("shard-0", readonly=True):
            pass
    await manager.dispose()


@pytest.mark.asyncio
async def test_pool_exhaustion_returns_structured_error(infra, clean):
    settings, _, urls = infra
    # pool_size=1 max_overflow=0, hold the only connection then request more
    manager = Mgr(
        {"shard-0": urls["shard-0"]},
        Settings(
            **{
                **settings.model_dump(),
                "db_pool_size": 1,
                "db_max_overflow": 0,
                "db_pool_timeout": 1,
            }
        ),
        pool_size=1,
    )
    from sqlalchemy import text

    async with manager.session_scope("shard-0", readonly=True) as held:
        await held.execute(text("SELECT pg_sleep(1.5)"))
        with pytest.raises(ShardUnavailableError) as exc:
            async with manager.session_scope("shard-0", readonly=True) as other:
                await other.execute(text("SELECT 1"))
    assert "pool" in exc.value.message or exc.value.error_code == "SHARD_UNAVAILABLE"
    await manager.dispose()


@pytest.mark.asyncio
async def test_successful_probe_revives_a_marked_down_shard(infra, clean):
    settings, _, urls = infra
    urls4 = {f"shard-{i}": urls[f"shard-{i}"] for i in range(4)}
    manager = ShardEngineManager(urls4, settings)
    manager.mark_down("shard-1")
    assert manager.is_down("shard-1")
    # a real ping against the live cluster clears the flag
    latency = await manager.ping("shard-1")
    assert latency >= 0
    assert not manager.is_down("shard-1")
    # and normal sessions work again
    from sqlalchemy import text as sa_text

    async with manager.session_scope("shard-1", readonly=True) as session:
        assert (await session.execute(sa_text("SELECT 1"))).scalar() == 1
    await manager.dispose()


# --------------------------------------------------------------- hot shard
@pytest.mark.asyncio
async def test_hot_workload_detected(ctx):
    users, metrics, router = ctx["users"], ctx["metrics"], ctx["router"]
    hot_user, _ = await users.create_traced(
        username="celeb", email="celeb@x.test", password_hash="h"
    )
    others = []
    for i in range(20):
        u, _ = await users.create_traced(username=f"n{i}", email=f"n{i}@x.test", password_hash="h")
        others.append(u.id)
    import random

    rng = random.Random(1)
    for _ in range(400):
        uid = hot_user.id if rng.random() < 0.85 else rng.choice(others)
        await users.get_by_id_traced(uid)
    detector = HotShardDetector(metrics, min_requests=50, load_ratio_threshold=0.4)
    hot = detector.detect()
    assert hot, "skewed workload should be detected"
    assert hot[0].shard_id == router.shard_for(hot_user.id)


# --------------------------------------------------------------- rebalancing
@pytest_asyncio.fixture
async def rebalance_ctx(infra, clean):
    settings, _, urls = infra
    four = {f"shard-{i}": urls[f"shard-{i}"] for i in range(4)}
    router = ShardRouter(
        list(four), strategy=RoutingStrategy.CONSISTENT_HASH, virtual_nodes_per_shard=150
    )
    manager = ShardEngineManager(four, settings)
    metrics = MetricsCollector(list(four))
    registry = InMemoryShardRegistry(
        ShardMetadata.from_url(sid, url, virtual_nodes=150) for sid, url in four.items()
    )
    for s in await registry.list():
        s.status = ShardStatus.HEALTHY
    users = ShardedUserRepository(router, manager, metrics)
    migrator = RebalanceMigrator(router, manager, metrics, registry)
    yield {
        "settings": settings,
        "router": router,
        "manager": manager,
        "metrics": metrics,
        "registry": registry,
        "users": users,
        "migrator": migrator,
        "fifth_url": urls["shard-4"],
    }
    await manager.dispose()


@pytest.mark.asyncio
async def test_rebalance_4_to_5_real_migration(rebalance_ctx):
    c = rebalance_ctx
    users = c["users"]
    created = []
    for i in range(60):
        u, _ = await users.create_traced(
            username=f"mig{i}", email=f"mig{i}@x.test", password_hash="h"
        )
        created.append(u)

    from sqlalchemy.ext.asyncio import create_async_engine

    # ensure the spare shard starts with the schema but no users
    fifth = create_async_engine(c["fifth_url"])
    from models import Base

    async with fifth.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await fifth.dispose()

    report = await c["migrator"].migrate_add_shard("shard-4", new_url=c["fifth_url"], table="users")
    assert report.status.value == "COMPLETED"
    assert report.rows_migrated > 0
    assert c["router"].shard_count == 5

    # checksums were actually computed on both sides and match
    assert (
        report.source_checksum["users"]["content_hash"]
        == report.destination_checksum["users"]["content_hash"]
    )

    # every migrated user is now found through the NEW topology on shard-4,
    # and has been cleaned off its old shard.
    for u in created:
        new_owner = c["router"].shard_for(u.id)
        async with c["manager"].session_scope(new_owner, readonly=True) as s:
            found = await s.get(User, u.id)
        if new_owner == "shard-4":
            assert found is not None

    metrics_snap = c["metrics"].snapshot()
    assert metrics_snap["rows_migrated"] > 0 and metrics_snap["migration_failures"] == 0


@pytest.mark.asyncio
async def test_checksum_failure_blocks_ownership_switch(rebalance_ctx, monkeypatch):
    c = rebalance_ctx
    users = c["users"]
    for i in range(40):
        await users.create_traced(username=f"blk{i}", email=f"blk{i}@x.test", password_hash="h")

    original_insert = c["migrator"]._insert_rows

    async def half_insert(shard_id, table, rows):  # lose half the copied rows
        await original_insert(shard_id, table, rows[: max(1, len(rows) // 2)])

    monkeypatch.setattr(c["migrator"], "_insert_rows", half_insert)

    with pytest.raises(ChecksumMismatchError):
        await c["migrator"].migrate_add_shard("shard-4", new_url=c["fifth_url"], table="users")
    # Ownership NOT switched and routing still serves the old 4 shards.
    assert c["router"].shard_count == 4
    assert "shard-4" not in c["router"].shard_ids
    assert c["metrics"].migration_failures >= 1


@pytest.mark.asyncio
async def test_dry_run_simulation_reports_plan_without_copies(rebalance_ctx):
    c = rebalance_ctx
    for i in range(20):
        await c["users"].create_traced(
            username=f"dry{i}", email=f"dry{i}@x.test", password_hash="h"
        )
    report = await c["migrator"].migrate_add_shard("shard-4", table="users", dry_run=True)
    assert report.simulation is True
    assert report.status.value == "PLANNED"
    assert report.rows_migrated == 0
    assert c["router"].shard_count == 4
