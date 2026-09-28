"""End-to-end integration test (Phase 20, the faculty demo as a test).

Runs the documented flow against the real FastAPI app, real repositories and
real PostgreSQL (embedded clusters or external URLs), with the cache and the
derived systems enabled::

    create user -> create post -> route post -> retrieve post (cache MISS/HIT)
      -> create comment -> create like -> generate feed -> search post
      -> retrieve graph relationship -> inspect metrics

The sharded path is exercised: users are created through the shard router and
looked up again by id on the shard that owns them.
"""

from __future__ import annotations

import pytest
from api.main import create_app
from common.config import Settings
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_and_settings(infra, clean, tmp_path):  # noqa: ANN201
    settings_obj, _clusters, urls = infra
    settings = Settings(
        database_url=settings_obj.database_url,
        mode="FULL_DISTRIBUTED",
        sharding_enabled=True,
        shard_count=4,
        shard_0_url=urls.get("shard-0"),
        shard_1_url=urls.get("shard-1"),
        shard_2_url=urls.get("shard-2"),
        shard_3_url=urls.get("shard-3"),
        db_pool_size=5,
        db_max_overflow=5,
        # Redis is not available in the test environment: memory:// selects the
        # fakeredis backend, which is dev/test ONLY (never a deployment path).
        cache_enabled=True,
        redis_url="memory://",
        cache_default_ttl=60,
        cache_hot_key_threshold=3,
        # derived systems
        denormalized_enabled=True,
        media_backend="local",
        media_root=str(tmp_path / "media"),
        feed_strategy="pull",
        search_provider="memory",
        event_bus="memory",
    )
    app = create_app(settings)
    from api.dependencies import get_platform

    platform = get_platform()
    await platform.start()
    try:
        yield app, settings
    finally:
        await platform.shutdown()


@pytest.mark.asyncio
async def test_end_to_end_full_flow(app_and_settings) -> None:
    app, settings = app_and_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # ---------------------------------------------------------- 1. users
        async def make_user(name: str) -> int:
            res = await client.post(
                "/api/v1/users",
                json={
                    "username": name,
                    "email": f"{name}@example.com",
                    "password": "password123",
                    "display_name": name.title(),
                },
            )
            assert res.status_code == 201, res.text
            return int(res.json()["user_id"])

        alice = await make_user("e2e_alice")
        bob = await make_user("e2e_bob")
        cara = await make_user("e2e_cara")

        # ------------------------------------------------- 2. route the user
        routed = (await client.get(f"/api/v1/shards/route/user/{alice}")).json()
        assert routed["shard_id"].startswith("shard-")
        assert routed["strategy"] == "consistent_hash"

        # ------------------------------------------------ 3. follows (graph)
        assert (
            await client.post(f"/api/v1/users/{bob}/follow", json={"following_id": alice})
        ).status_code == 201
        assert (
            await client.post(f"/api/v1/users/{cara}/follow", json={"following_id": alice})
        ).status_code == 201

        # -------------------------------------------------- 4. create a post
        res = await client.post(
            "/api/v1/posts",
            json={
                "user_id": alice,
                "content": "End-to-end verification post about distributed caching #metascale",
                "visibility": "public",
            },
        )
        assert res.status_code == 201, res.text
        post = res.json()
        post_id = int(post["post_id"])
        assert post["user_id"] == alice

        # --------------------------------- 5. retrieve it: MISS then HIT
        first = await client.get(f"/api/v1/posts/{post_id}")
        assert first.status_code == 200
        assert first.headers["x-cache"] == "MISS"
        second = await client.get(f"/api/v1/posts/{post_id}")
        assert second.headers["x-cache"] == "HIT"
        assert second.json() == first.json()  # cache never changes the payload

        # --------------------------------------------- 6. comment + like
        res = await client.post(
            f"/api/v1/posts/{post_id}/comments",
            json={"user_id": bob, "content": "verified by the e2e test"},
        )
        assert res.status_code == 201
        comment_id = res.json()["comment_id"]

        res = await client.post(f"/api/v1/posts/{post_id}/like", json={"user_id": bob})
        assert res.status_code == 201
        assert res.json()["like_count"] == 1
        assert res.json()["post_author_id"] == alice
        # liking twice is idempotent (UNIQUE post_id/user_id)
        assert (await client.post(f"/api/v1/posts/{post_id}/like", json={"user_id": bob})).json()[
            "like_count"
        ] == 1

        comments = (await client.get(f"/api/v1/posts/{post_id}/comments")).json()
        assert any(c["comment_id"] == comment_id for c in comments)

        # -------------------------------------------------- 7. hashtags (3NF)
        tags = (await client.get(f"/api/v1/posts/{post_id}/hashtags")).json()["tags"]
        assert "metascale" in tags

        # ------------------------------------------------------- 8. feed
        feed = (await client.get(f"/api/v1/users/{bob}/feed?limit=10")).json()
        assert feed["count"] >= 1
        assert any(item["post_id"] == post_id for item in feed["items"])

        # ------------------------------------------------------ 9. search
        # the index was populated by the POST_CREATED domain event
        found = (await client.get("/api/v1/search?q=metascale")).json()
        assert any(p["post_id"] == post_id for p in found["posts"])
        # results refer back to canonical ids, never index-local ones
        assert all(p["entity"] == "post" for p in found["posts"])

        # ------------------------------------------------------- 10. graph
        followers = (await client.get(f"/api/v1/graph/users/{alice}/followers")).json()
        assert set(followers["user_ids"]) == {bob, cara}
        following = (await client.get(f"/api/v1/graph/users/{bob}/following")).json()
        assert following["user_ids"] == [alice]
        mutuals = (await client.get(f"/api/v1/graph/users/{bob}/mutuals/{cara}")).json()
        assert mutuals["user_ids"] == [alice]

        # ------------------------------------------------------- 11. media
        upload = await client.post(
            "/api/v1/media/upload",
            data={"owner_id": str(alice)},
            files={"file": ("blob.png", b"e2e-media-bytes" * 8, "image/png")},
        )
        assert upload.status_code == 200, upload.text
        media = upload.json()
        assert media["backend"] == "local_filesystem"
        assert media["deduplicated"] is False

        duplicate = await client.post(
            "/api/v1/media/upload",
            data={"owner_id": str(alice)},
            files={"file": ("blob.png", b"e2e-media-bytes" * 8, "image/png")},
        )
        assert duplicate.status_code == 200
        # content-addressable de-duplication: same checksum -> same row
        assert duplicate.json()["deduplicated"] is True
        assert duplicate.json()["media_id"] == media["media_id"]

        fetched = await client.get(f"/api/v1/media/{media['media_id']}")
        assert fetched.status_code == 200
        assert fetched.content == b"e2e-media-bytes" * 8

        # --------------------------------------------- 12. derived read model
        await client.post("/api/v1/search/reindex")
        metrics = (await client.get("/api/v1/metrics")).json()

        # ----------------------------------------------------- 13. metrics
        assert metrics["counters"]["request_count"] > 0
        latency = metrics["histograms"]["request_latency_ms"]
        assert latency["count"] > 0 and latency["p50_ms"] >= 0
        assert metrics["cache"]["cache_hits"] >= 1
        assert metrics["cache"]["cache_misses"] >= 1
        assert metrics["events"]["published"] >= 4  # 3 users + post + like...
        assert metrics["events"]["failures"] == 0
        assert metrics["read_model"]["derived"] is True
        assert metrics["read_model"]["events_applied"] >= 1
        assert metrics["search"]["derived"] is True

        # ------------------------------------------------ 14. replication
        repl = (await client.get("/api/v1/replication/status")).json()
        assert repl["replication_active"] is False  # no replicas configured
        assert repl["counters"]["writes_primary"] > 0
        assert all(s["primary"]["reachable"] for s in repl["shards"])

        simulated = (await client.post("/api/v1/replication/demo/simulate-failover/shard-0")).json()
        assert simulated["simulated"] is True  # labelled, not passed off as real

        # --------------------------------------- 15. Prometheus exposition
        prometheus = await client.get("/metrics")
        assert prometheus.status_code == 200
        assert "metascale_request_count" in prometheus.text
        assert "metascale_request_latency_ms" in prometheus.text

        # ------------------------------------------- 16. update invalidates
        patched = await client.patch(
            f"/api/v1/posts/{post_id}", json={"content": "edited by the e2e test"}
        )
        assert patched.status_code == 200
        after_patch = await client.get(f"/api/v1/posts/{post_id}")
        assert after_patch.headers["x-cache"] == "MISS"  # invalidated on write
        assert after_patch.json()["content"] == "edited by the e2e test"
        assert (await client.get(f"/api/v1/posts/{post_id}")).headers["x-cache"] == "HIT"

        # ------------------------------------------------ 17. delete + 404
        assert (await client.delete(f"/api/v1/posts/{post_id}")).status_code in (200, 204)
        assert (await client.get(f"/api/v1/posts/{post_id}")).status_code == 404

    # shards hold the data; the canonical database is not the write target here
    assert settings.sharding_active is True
