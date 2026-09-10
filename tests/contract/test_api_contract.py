"""API contract tests driven through FastAPI (httpx ASGITransport).

Covers both deployment modes against real PostgreSQL clusters:
NORMALIZED (single canonical DB) and SHARDED (four independent shards).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from api.main import create_app
from common.enums import RoutingStrategy
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.contract


@pytest_asyncio.fixture
async def normalized_client(infra, clean):
    settings, _, urls = infra
    from common.config import Settings

    canon_settings = Settings(
        database_url=settings.database_url,
        debug=False,
        mode="NORMALIZED",
        sharding_enabled=False,
        db_pool_size=5,
        db_max_overflow=5,
        db_connect_timeout=10,
        shard_0_url=None,
        shard_1_url=None,
        shard_2_url=None,
        shard_3_url=None,
    )
    app = create_app(canon_settings)
    from api.dependencies import get_platform

    platform = get_platform()
    await platform.start()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await platform.shutdown()


@pytest_asyncio.fixture
async def sharded_client(infra, clean):
    settings, _, urls = infra
    from common.config import Settings

    shard_settings = Settings(
        database_url=settings.database_url,
        debug=False,
        mode="SHARDED",
        sharding_enabled=True,
        sharding_strategy=RoutingStrategy.CONSISTENT_HASH,
        shard_count=4,
        shard_0_url=urls["shard-0"],
        shard_1_url=urls["shard-1"],
        shard_2_url=urls["shard-2"],
        shard_3_url=urls["shard-3"],
        db_pool_size=5,
        db_max_overflow=5,
        db_connect_timeout=10,
        hot_shard_min_requests=50,
    )
    app = create_app(shard_settings)
    from api.dependencies import get_platform

    platform = get_platform()
    await platform.start()
    await platform.health_checker.check_all()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await platform.shutdown()


# =======================================================================
@pytest.mark.asyncio
async def test_health_and_readiness_normalized(normalized_client):
    c = normalized_client
    assert (await c.get("/health")).json()["status"] == "healthy"
    ready = await c.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["mode"] == "NORMALIZED"


@pytest.mark.asyncio
async def test_user_and_post_crud_contract(normalized_client):
    c = normalized_client
    # create user
    r = await c.post(
        "/api/v1/users",
        json={"username": "alice", "email": "alice@example.com", "password": "password123"},
    )
    assert r.status_code == 201, r.text
    user = r.json()
    assert user["username"] == "alice"

    r = await c.get(f"/api/v1/users/{user['id']}")
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"

    # 404 structured envelope
    r = await c.get("/api/v1/users/999999")
    assert r.status_code == 404
    assert r.json()["error"] == "NOT_FOUND"

    # validation envelope (bad email)
    r = await c.post("/api/v1/users", json={"username": "x", "email": "bad", "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"] == "VALIDATION_ERROR"

    # create post
    r = await c.post("/api/v1/posts", json={"author_id": user["id"], "content": "hello world"})
    assert r.status_code == 201, r.text
    post = r.json()

    r = await c.get(f"/api/v1/posts/{post['id']}")
    assert r.status_code == 200 and r.json()["content"] == "hello world"

    # comment + listing
    r = await c.post(
        f"/api/v1/posts/{post['id']}/comments", json={"user_id": user["id"], "content": "nice post"}
    )
    assert r.status_code == 201
    r = await c.get(f"/api/v1/posts/{post['id']}/comments")
    assert r.status_code == 200 and len(r.json()) == 1


@pytest.mark.asyncio
async def test_followers_contract(normalized_client):
    c = normalized_client
    a = (
        await c.post(
            "/api/v1/users",
            json={"username": "star_a", "email": "star_a@example.com", "password": "password123"},
        )
    ).json()
    b = (
        await c.post(
            "/api/v1/users",
            json={"username": "fan_b", "email": "fan_b@example.com", "password": "password123"},
        )
    ).json()
    # follow edge is created directly through the model-less API? use shard-less
    from api.dependencies import get_platform

    await get_platform().user_repo.add_follow(b["id"], a["id"])
    followers = (await c.get(f"/api/v1/users/{a['id']}/followers")).json()
    assert any(f["follower_id"] == b["id"] for f in followers)


@pytest.mark.asyncio
async def test_sharded_endpoints_disabled_in_normalized_mode(normalized_client):
    r = await normalized_client.get("/api/v1/shards")
    assert r.status_code == 501
    assert r.json()["error"] == "NOT_CONFIGURED"


# =======================================================================
@pytest.mark.asyncio
async def test_shard_registry_and_routing(sharded_client):
    c = sharded_client
    r = await c.get("/api/v1/shards")
    assert r.status_code == 200
    body = r.json()
    assert body["shard_count"] == 4
    assert len(body["shards"]) == 4
    assert {s["status"] for s in body["shards"]} == {"HEALTHY"}

    r = await c.get("/api/v1/shards/route/user/101")
    assert r.status_code == 200
    routed = r.json()
    assert routed["key"] == "101"
    assert routed["entity"] == "users"
    assert routed["shard_id"].startswith("shard-")
    assert routed["strategy"] in ("hash", "consistent_hash")

    r = await c.get("/api/v1/shards/route/101?strategy=hash")
    assert r.json()["strategy"] == "hash"

    # demo users 101/202/303 are real algorithm outputs
    for uid in (101, 202, 303):
        d = (await c.get(f"/api/v1/shards/route/user/{uid}")).json()
        assert d["shard_id"] in {f"shard-{i}" for i in range(4)}


@pytest.mark.asyncio
async def test_distribution_is_measured_and_even(sharded_client):
    r = await sharded_client.get("/api/v1/shards/distribution?sample_size=50000")
    dist = r.json()["distribution"]
    assert len(dist) == 4
    for point in dist:
        assert 21.0 < point["percentage"] < 29.0, point


@pytest.mark.asyncio
async def test_sharded_user_and_post_crud_targeted(sharded_client):
    c = sharded_client
    r = await c.post(
        "/api/v1/sharded/users",
        json={"username": "sharduser", "email": "sharduser@example.com", "password": "password123"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    uid = body["data"]["id"]
    owner_shard = body["routing"]["shard_contacted"]

    r = await c.get(f"/api/v1/sharded/users/{uid}")
    assert r.status_code == 200
    assert r.json()["routing"]["shard_contacted"] == owner_shard
    assert r.json()["data"]["username"] == "sharduser"

    # generic endpoint ALSO routes through shards in SHARDED mode
    r = await c.get(f"/api/v1/users/{uid}")
    assert r.status_code == 200 and r.json()["id"] == uid

    # post routes to the author's shard and is findable by its own id
    r = await c.post("/api/v1/sharded/posts", json={"author_id": uid, "content": "routed"})
    assert r.status_code == 201, r.text
    post_body = r.json()
    pid = post_body["data"]["id"]
    assert post_body["routing"]["shard_contacted"] == owner_shard

    r = await c.get(f"/api/v1/sharded/posts/{pid}")
    assert r.status_code == 200
    assert r.json()["routing"]["shard_contacted"] == owner_shard


@pytest.mark.asyncio
async def test_scatter_gather_endpoint_contacts_all_shards(sharded_client):
    c = sharded_client
    # spread users/posts across shards
    user_ids = []
    for i in range(16):
        r = await c.post(
            "/api/v1/sharded/users",
            json={
                "username": f"sc{i}user",
                "email": f"sc{i}user@example.com",
                "password": "password123",
            },
        )
        user_ids.append(r.json()["data"]["id"])
    for uid in user_ids:
        await c.post("/api/v1/sharded/posts", json={"author_id": uid, "content": f"p{uid}"})

    r = await c.get("/api/v1/sharded/posts?limit=30")
    assert r.status_code == 200
    timing = r.json()["timing"]
    assert len(timing["shards_contacted"]) == 4
    assert timing["total_rows"] >= 16
    assert timing["query_type"] == "scatter_gather"
    assert len(r.json()["data"]) <= 30


@pytest.mark.asyncio
async def test_cross_shard_demo_endpoint(sharded_client):
    c = sharded_client
    # find/craft a post with a commenter on a different shard
    author = (
        await c.post(
            "/api/v1/sharded/users",
            json={"username": "xauthor", "email": "xauthor@example.com", "password": "password123"},
        )
    ).json()
    post = (
        await c.post(
            "/api/v1/sharded/posts", json={"author_id": author["data"]["id"], "content": "join me"}
        )
    ).json()
    post_shard = post["routing"]["shard_contacted"]
    commenter_on_other = None
    for i in range(80):
        u = (
            await c.post(
                "/api/v1/sharded/users",
                json={
                    "username": f"xc{i}",
                    "email": f"xc{i}@example.com",
                    "password": "password123",
                },
            )
        ).json()
        sid = (await c.get(f"/api/v1/shards/route/user/{u['data']['id']}")).json()["shard_id"]
        if sid != post_shard:
            commenter_on_other = u["data"]["id"]
            break
    assert commenter_on_other is not None
    await c.post(
        f"/api/v1/posts/{post['data']['id']}/comments",
        json={"user_id": commenter_on_other, "content": "cross shard!"},
    )

    r = await c.get(f"/api/v1/sharded/posts/{post['data']['id']}/cross-shard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["network_hops"] >= 2
    assert len(body["shards_contacted"]) >= 2
    assert "impossible" in body["note"]


@pytest.mark.asyncio
async def test_hot_user_demo_and_stats(sharded_client):
    c = sharded_client
    # create the celebrity so reads succeed
    celeb = (
        await c.post(
            "/api/v1/sharded/users",
            json={"username": "celeb", "email": "celeb@example.com", "password": "password123"},
        )
    ).json()
    r = await c.post(f"/api/v1/shards/demo/hot-user/{celeb['data']['id']}?requests=400&skew=0.9")
    assert r.status_code == 200
    body = r.json()
    loads = body["per_shard"]
    hot = max(loads.values(), key=lambda d: d["requests"])
    assert hot["requests"] > 300
    assert body["hot_shards"], "celebrity workload should trigger hot-shard detection"

    stats = (await c.get("/api/v1/shards/stats")).json()
    assert stats["total_requests"] > 300
    assert "p95_latency_ms" in stats["shards"][body["hot_shard"]]
    assert body["hot_shard"] in stats["shards"]


@pytest.mark.asyncio
async def test_rebalance_dry_run_contract(sharded_client):
    c = sharded_client
    for i in range(8):
        await c.post(
            "/api/v1/sharded/users",
            json={"username": f"rb{i}", "email": f"rb{i}@example.com", "password": "password123"},
        )
    r = await c.post("/api/v1/shards/rebalance", json={"table": "users", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["simulation"] is True
    assert body["status"] == "PLANNED"
    assert body["destination_shard_id"] == "shard-4"
    assert [s["phase"] for s in body["steps"] if s["phase"] == "PLANNED"]


@pytest.mark.asyncio
async def test_health_check_endpoint(sharded_client):
    r = await sharded_client.post("/api/v1/shards/shard-1/health-check")
    assert r.status_code == 200
    assert r.json()["reachable"] is True

    r = await sharded_client.post("/api/v1/shards/nope/health-check")
    assert r.status_code == 404
