"""Integration tests for the integrated-system routes added for the final
iteration: aggregate health, database explorer + change stream, event-bus
introspection, observability surface and live benchmarks.

All of them run against real PostgreSQL (the shared ``infra``/``clean``
fixtures) and real measurements — no mocked numbers.
"""

from __future__ import annotations

import pytest
from api.main import create_app
from common.config import Settings
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_and_platform(infra, clean, tmp_path):  # noqa: ANN201
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
        cache_enabled=True,
        redis_url="memory://",
        denormalized_enabled=True,
        media_backend="local",
        media_root=str(tmp_path / "media"),
        feed_strategy="pull",
        search_provider="memory",
        event_bus="memory",
        changes_enabled=True,
    )
    app = create_app(settings)
    from api.dependencies import get_platform

    platform = get_platform()
    await platform.start()
    try:
        yield app, settings, platform
    finally:
        await platform.shutdown()


async def _make_user(client: AsyncClient, name: str) -> int:
    res = await client.post(
        "/api/v1/users",
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": "password123",
            "display_name": name,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["user_id"]


async def test_api_v1_health_reports_every_dependency(app_and_platform) -> None:
    app, settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get("/api/v1/health")).json()

    assert body["status"] in {"healthy", "degraded", "unavailable"}
    assert body["mode"] == "FULL_DISTRIBUTED"
    components = body["components"]
    # required: the API process, canonical PostgreSQL and every shard
    assert {"api", "postgresql", "shards"} <= set(components)
    assert components["postgresql"]["status"] == "healthy"
    assert components["shards"]["status"] == "healthy"
    assert components["shards"]["count"] == 4
    # optional backends are probed and classified with the same vocabulary
    for optional in ("redis", "rabbitmq", "opensearch", "minio"):
        assert components[optional]["status"] in {"healthy", "degraded", "unavailable"}
        assert components[optional]["required"] is False
    # the cache is enabled in this fixture -> healthy
    assert components["redis"]["status"] == "healthy"
    assert settings.mode == "FULL_DISTRIBUTED"


async def test_database_explorer_reports_real_rows_and_shards(app_and_platform) -> None:
    app, _settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "dbx_alice")
        post = (
            await client.post(
                "/api/v1/posts",
                json={"user_id": alice, "content": "explorer row", "visibility": "public"},
            )
        ).json()

        tables = (await client.get("/api/v1/database/tables")).json()
        by_name = {t["table"]: t for t in tables["tables"]}
        assert by_name["users"]["rows"] >= 1
        assert by_name["users"]["shard_key"] == "user_id"
        assert by_name["posts"]["rows"] >= 1
        assert by_name["follows"]["primary_key"] == ["follower_id", "following_id"]

        rows = (await client.get("/api/v1/database/tables/posts/rows?limit=10")).json()
        assert rows["table"] == "posts"
        assert any(r["post_id"] == post["post_id"] for r in rows["rows"])
        # scatter-gather tags every row with the shard it came from
        assert all(r["shard"] is not None for r in rows["rows"])

        unknown = await client.get("/api/v1/database/tables/nope/rows")
        assert unknown.status_code == 404


async def test_change_stream_records_every_write(app_and_platform) -> None:
    app, _settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "chg_alice")
        bob = await _make_user(client, "chg_bob")
        post = (
            await client.post(
                "/api/v1/posts",
                json={"user_id": alice, "content": "tracked", "visibility": "public"},
            )
        ).json()
        await client.post(f"/api/v1/posts/{post['post_id']}/like", json={"user_id": bob})
        await client.post(f"/api/v1/users/{bob}/follow", json={"following_id": alice})

        changes = (await client.get("/api/v1/database/changes?limit=50")).json()
        assert changes["tracking"] == "enabled"
        records = changes["changes"]

        def find(table: str, operation: str) -> dict:
            matches = [c for c in records if c["table"] == table and c["operation"] == operation]
            assert matches, f"no {operation} on {table} in {records}"
            return matches[0]

        inserted_users = [
            c
            for c in records
            if c["table"] == "users"
            and c["operation"] == "INSERT"
            and c["primary_key"]["user_id"] in (alice, bob)
        ]
        assert len(inserted_users) == 2, records
        user_insert = next(c for c in inserted_users if c["primary_key"]["user_id"] == alice)
        assert user_insert["shard"] is not None  # routed to a real shard
        assert user_insert["after"]["username"] == "chg_alice"
        assert "password" not in user_insert["after"]
        assert "password_hash" not in user_insert["after"]  # secrets never recorded
        assert user_insert["latency_ms"] >= 0
        assert user_insert["request_id"]
        assert user_insert["service"] == "api"

        like = find("likes", "INSERT")
        assert like["after"]["post_id"] == post["post_id"]
        assert like["after"]["user_id"] == bob

        follow = find("follows", "INSERT")
        assert follow["primary_key"] == {"follower_id": bob, "following_id": alice}
        assert follow["shard"] is not None  # composite key still resolves a shard

        # filters work
        only_users = (await client.get("/api/v1/database/changes?table=users")).json()
        assert {c["table"] for c in only_users["changes"]} == {"users"}

        stats = (await client.get("/api/v1/database/changes/stats")).json()
        assert stats["by_table"]["users"] >= 2
        assert stats["scope"] == "in-process ring buffer (cleared on restart)"

        await client.delete("/api/v1/database/changes")
        cleared = (await client.get("/api/v1/database/changes")).json()
        assert cleared["changes"] == []


async def test_event_bus_introspection(app_and_platform) -> None:
    app, _settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "evt_alice")
        await client.post(
            "/api/v1/posts",
            json={"user_id": alice, "content": "eventful", "visibility": "public"},
        )

        overview = (await client.get("/api/v1/events")).json()
        assert overview["transport"] == "in_memory"
        assert overview["stats"]["published"] >= 2
        assert overview["subscribers"], "derived systems must subscribe to the bus"

        recent = (await client.get("/api/v1/events/recent?limit=10")).json()
        assert recent["retained"] is True
        types = {e["event_type"] for e in recent["events"]}
        assert "USER_CREATED" in types
        assert "POST_CREATED" in types
        for event in recent["events"]:
            assert {"event_id", "event_type", "entity_type", "entity_id", "timestamp"} <= set(event)

        types_body = (await client.get("/api/v1/events/types")).json()
        assert {t["event_type"] for t in types_body["types"]} >= {
            "POST_CREATED",
            "COMMENT_CREATED",
            "LIKE_CREATED",
            "FOLLOW_CREATED",
            "MEDIA_CREATED",
        }


async def test_observability_status_matches_health(app_and_platform) -> None:
    app, _settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/v1/users?limit=1")
        status = (await client.get("/api/v1/observability/status")).json()
    assert status["status"] in {"healthy", "degraded", "unavailable"}
    assert status["components"]["postgresql"]["status"] == "healthy"
    assert status["measured"]["requests"] >= 1
    assert "request_latency_ms" in status["measured"]


async def test_benchmarks_are_measured_not_hardcoded(app_and_platform) -> None:
    app, _settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "bench_alice")
        created = []
        for i in range(5):
            created.append(
                (
                    await client.post(
                        "/api/v1/posts",
                        json={
                            "user_id": alice,
                            "content": f"benchmark post {i}",
                            "visibility": "public",
                        },
                    )
                ).json()["post_id"]
            )

        operations = (await client.get("/api/v1/benchmarks/operations")).json()
        assert {o["name"] for o in operations["operations"]} >= {"read_post", "cached_read"}

        run = (await client.post("/api/v1/benchmarks/run?operation=read_post&iterations=5")).json()
        assert run["count"] == 5
        assert run["p50_ms"] >= 0
        assert run["max_ms"] >= run["p50_ms"] >= run["min_ms"]

        # cold (miss) then warm (hit): the cache counters come from the real provider
        cached = (
            await client.post("/api/v1/benchmarks/run?operation=cached_read&iterations=5")
        ).json()
        assert cached["cold"]["count"] == 5
        assert cached["warm"]["count"] == 5
        assert cached["cache_misses"] >= 1  # cold pass really missed
        assert cached["cache_hits"] >= 1  # warm pass really hit

        compare = (await client.post("/api/v1/benchmarks/compare?iterations=3")).json()
        assert compare["results"], "at least one operation must be measurable"
        for row in compare["results"]:
            if row.get("available", True):
                assert row["count"] == 3

        catalogue = (await client.get("/api/v1/benchmarks/architectures")).json()
        assert [a["id"] for a in catalogue["architectures"]] == list("ABCDEF")


async def test_resource_aliases_for_comments_likes_follows(app_and_platform) -> None:
    """comments / likes / follows are addressable as their own resources."""
    app, _settings, _platform = app_and_platform
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "res_alice")
        bob = await _make_user(client, "res_bob")
        post = (
            await client.post(
                "/api/v1/posts",
                json={"user_id": alice, "content": "resource aliases", "visibility": "public"},
            )
        ).json()
        post_id = post["post_id"]

        await client.post(
            f"/api/v1/posts/{post_id}/comments", json={"user_id": bob, "content": "hi"}
        )
        await client.post(f"/api/v1/posts/{post_id}/like", json={"user_id": bob})
        await client.post(f"/api/v1/users/{bob}/follow", json={"following_id": alice})

        comments = (await client.get(f"/api/v1/comments?post_id={post_id}")).json()
        assert [c["content"] for c in comments] == ["hi"]
        counts = (await client.get(f"/api/v1/comments/counts?post_id={post_id}")).json()
        assert counts["comment_count"] == 1

        likes = (await client.get(f"/api/v1/likes?post_id={post_id}")).json()
        assert likes["count"] == 1
        assert likes["items"][0]["user_id"] == bob
        assert (await client.get(f"/api/v1/likes/counts?post_id={post_id}")).json()[
            "like_count"
        ] == 1

        following = (await client.get(f"/api/v1/follows?user_id={bob}&direction=following")).json()
        assert [f["following_id"] for f in following] == [alice]
        followers = (
            await client.get(f"/api/v1/follows?user_id={alice}&direction=followers")
        ).json()
        assert [f["follower_id"] for f in followers] == [bob]
        assert (await client.get(f"/api/v1/follows/counts?user_id={alice}")).json() == {
            "user_id": alice,
            "followers": 1,
            "following": 0,
        }
        check = (
            await client.get(f"/api/v1/follows/check?follower_id={bob}&following_id={alice}")
        ).json()
        assert check["is_following"] is True

        # deleting through the resource alias publishes the same event
        await client.delete(f"/api/v1/likes/{post_id}/{bob}")
        assert (await client.get(f"/api/v1/likes/counts?post_id={post_id}")).json()[
            "like_count"
        ] == 0
        await client.delete(f"/api/v1/follows/{bob}/{alice}")
        assert (await client.get(f"/api/v1/follows/counts?user_id={alice}")).json()[
            "followers"
        ] == 0
        recent = (await client.get("/api/v1/events/recent?limit=20")).json()
        types = {e["event_type"] for e in recent["events"]}
        assert {"LIKE_DELETED", "FOLLOW_DELETED"} <= types
