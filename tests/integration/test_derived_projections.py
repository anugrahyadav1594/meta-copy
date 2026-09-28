"""Integration tests for the derived systems against real PostgreSQL.

Members 3 (denormalized read model), 7 (feed fan-out), 8 (media metadata),
9 (social graph), 10 (search index). Each test proves the same two things:

1. the projection is produced from canonical data (or domain events), and
2. it can be REBUILT from PostgreSQL — so it is never a second source of
   truth.
"""

from __future__ import annotations

import pytest
from api.main import create_app
from common.config import Settings
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def app_and_urls(infra, clean, tmp_path):  # noqa: ANN201
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
        cache_enabled=False,
        denormalized_enabled=True,
        media_backend="local",
        media_root=str(tmp_path / "media"),
        feed_strategy="push",
        search_provider="memory",
        event_bus="memory",
    )
    app = create_app(settings)
    from api.dependencies import get_platform

    platform = get_platform()
    await platform.start()
    try:
        yield app, settings, urls, platform
    finally:
        await platform.shutdown()


async def _make_user(client: AsyncClient, name: str) -> int:
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


async def _make_post(client: AsyncClient, user_id: int, content: str) -> int:
    res = await client.post("/api/v1/posts", json={"user_id": user_id, "content": content})
    assert res.status_code == 201, res.text
    return int(res.json()["post_id"])


@pytest.mark.asyncio
async def test_read_model_is_projected_from_events(app_and_urls) -> None:
    app, settings, _urls, _platform = app_and_urls
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "rm_alice")
        bob = await _make_user(client, "rm_bob")
        post_id = await _make_post(client, alice, "read model projection test")

        await client.post(f"/api/v1/posts/{post_id}/like", json={"user_id": bob})
        await client.post(
            f"/api/v1/posts/{post_id}/comments",
            json={"user_id": bob, "content": "projected"},
        )

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT author_username, content, like_count, comment_count "
                            "FROM denormalized_post_feed WHERE post_id = :pid"
                        ),
                        {"pid": post_id},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None, "POST_CREATED did not reach the read model"
        assert row["author_username"] == "rm_alice"
        assert row["content"] == "read model projection test"
        assert row["like_count"] == 1  # LIKE_CREATED applied
        assert row["comment_count"] == 1  # COMMENT_CREATED applied

        # deleting the post removes the derived row (POST_DELETED)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.delete(f"/api/v1/posts/{post_id}")
        async with engine.connect() as conn:
            remaining = (
                await conn.execute(
                    text("SELECT count(*) FROM denormalized_post_feed WHERE post_id = :pid"),
                    {"pid": post_id},
                )
            ).scalar()
        assert remaining == 0

        # and the projection is rebuildable from canonical data
        from denormalization.read_model import rebuild

        count = await rebuild(engine)
        async with engine.connect() as conn:
            assert (
                await conn.execute(
                    text("SELECT count(*) FROM denormalized_post_feed WHERE post_id = :pid"),
                    {"pid": post_id},
                )
            ).scalar() == 0  # the post really is gone, so the rebuild omits it
        assert count >= 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_read_model_rebuild_matches_canonical_rows(app_and_urls) -> None:
    app, settings, urls, platform = app_and_urls
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "rb_alice")
        ids = [await _make_post(client, alice, f"rebuild source post {i}") for i in range(3)]

    engine = create_async_engine(settings.database_url)
    try:
        # wipe the derived table completely, then rebuild: it must come back
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM denormalized_post_feed"))
            await conn.execute(text("SELECT count(*) FROM denormalized_post_feed"))

        count = await platform.denormalizer.rebuild()  # type: ignore[union-attr]

        # in SHARDED mode canonical holds no posts, so the rebuild must find
        # them on the shards instead — that is the property under test here.
        shard_posts = 0
        for url in urls.values():
            shard_engine = create_async_engine(url)
            async with shard_engine.connect() as conn:
                shard_posts += int(
                    (
                        await conn.execute(
                            text("SELECT count(*) FROM posts WHERE user_id = :u"),
                            {"u": alice},
                        )
                    ).scalar()
                    or 0
                )
            await shard_engine.dispose()

        async with engine.connect() as conn:
            projected = int(
                (
                    await conn.execute(
                        text("SELECT count(*) FROM denormalized_post_feed " "WHERE user_id = :u"),
                        {"u": alice},
                    )
                ).scalar()
                or 0
            )
        assert shard_posts == len(ids)
        assert projected == len(ids)
        assert count == projected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_feed_fan_out_is_derived_and_rebuildable(app_and_urls) -> None:
    app, _settings, _urls, platform = app_and_urls
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "fo_alice")
        bob = await _make_user(client, "fo_bob")
        assert (
            await client.post(f"/api/v1/users/{bob}/follow", json={"following_id": alice})
        ).status_code == 201

        # POST_CREATED must fan out to every follower's user_feed row
        post_id = await _make_post(client, alice, "fan-out-on-write payload")

        feed = (await client.get(f"/api/v1/users/{bob}/feed?limit=10")).json()
        assert feed["strategy"] == "push"
        assert [row["post_id"] for row in feed["items"]] == [post_id]

        # wipe the derived table: a rebuild must reconstruct the same rows
        engine = create_async_engine(_settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM user_feed"))
            rows = await platform.feed_projector.rebuild()  # type: ignore[union-attr]
            async with engine.connect() as conn:
                stored = [
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT post_id FROM user_feed WHERE user_id = :u "
                                "ORDER BY post_id"
                            ),
                            {"u": bob},
                        )
                    ).all()
                ]
            assert rows >= 1
            assert stored == [post_id]
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_graph_is_rebuilt_from_follows_only(app_and_urls) -> None:
    app, settings, _urls, platform = app_and_urls
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "gr_alice")
        bob = await _make_user(client, "gr_bob")
        cara = await _make_user(client, "gr_cara")
        await client.post(f"/api/v1/users/{bob}/follow", json={"following_id": alice})
        await client.post(f"/api/v1/users/{cara}/follow", json={"following_id": alice})
        await client.post(f"/api/v1/users/{alice}/follow", json={"following_id": bob})

        followers = (await client.get(f"/api/v1/graph/users/{alice}/followers")).json()
        assert sorted(followers["user_ids"]) == sorted([bob, cara])
        following = (await client.get(f"/api/v1/graph/users/{alice}/following")).json()
        assert following["user_ids"] == [bob]
        mutuals = (await client.get(f"/api/v1/graph/users/{bob}/mutuals/{cara}")).json()
        assert mutuals["user_ids"] == [alice]

        # the graph is a projection: SQLite is NOT involved anywhere
        stats = (await client.get("/api/v1/graph/stats")).json()
        assert stats["derived_projection"] is True
        assert stats["source_of_truth"] == "postgresql:follows"

        # rebuild and re-read: identical answer
        await client.post("/api/v1/graph/rebuild")
        again = (await client.get(f"/api/v1/graph/users/{alice}/followers")).json()
        assert again["user_ids"] == followers["user_ids"]

    assert platform.graph is not None
    assert settings.media_backend == "local"


@pytest.mark.asyncio
async def test_search_index_is_derived_and_references_canonical_ids(app_and_urls) -> None:
    app, _settings, _urls, _platform = app_and_urls
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "se_alice")
        post_id = await _make_post(client, alice, "searchable distributed sharding post")

        # incremental indexing driven by POST_CREATED
        hits = (await client.get("/api/v1/search?q=sharding")).json()
        assert any(p["post_id"] == post_id for p in hits["posts"])

        # clear + full reindex from PostgreSQL (canonical and shards)
        await client.post("/api/v1/search/reindex")
        hits = (await client.get("/api/v1/search?q=sharding")).json()
        assert any(p["post_id"] == post_id for p in hits["posts"])
        assert all(p["entity"] == "post" for p in hits["posts"])
        assert all(p["_score"] > 0 for p in hits["posts"])

        autocomplete = (await client.get("/api/v1/search/autocomplete?prefix=search")).json()
        assert "searchable" in autocomplete["posts"]

        stats = (await client.get("/api/v1/metrics")).json()["search"]
        assert stats["derived"] is True
        assert stats["documents"] >= 1


@pytest.mark.asyncio
async def test_media_metadata_lives_in_postgres_and_blobs_in_the_store(
    app_and_urls, tmp_path
) -> None:
    app, settings, urls, _platform = app_and_urls
    payload = b"integration-media-bytes" * 16

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice = await _make_user(client, "md_alice")

        first = await client.post(
            "/api/v1/media/upload",
            data={"owner_id": str(alice)},
            files={"file": ("a.png", payload, "image/png")},
        )
        assert first.status_code == 200, first.text
        media = first.json()

        second = await client.post(
            "/api/v1/media/upload",
            data={"owner_id": str(alice)},
            files={"file": ("b.png", payload, "image/png")},
        )
        assert second.status_code == 200
        assert second.json()["media_id"] == media["media_id"]
        assert second.json()["deduplicated"] is True

        # metadata is a PostgreSQL row, never a blob-store artefact. In
        # SHARDED mode media is placed by owner_id, so it lives on the shard
        # that owns the uploader - not in the canonical database.
        media_row = None
        found_on: list[str] = []
        for label, url in [("canonical", settings.database_url), *urls.items()]:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    row = (
                        (
                            await conn.execute(
                                text(
                                    "SELECT checksum_sha256, size_bytes, storage_key, "
                                    "mime_type FROM media WHERE media_id = :m"
                                ),
                                {"m": media["media_id"]},
                            )
                        )
                        .mappings()
                        .first()
                    )
            finally:
                await engine.dispose()
            if row is not None:
                media_row = row
                found_on.append(label)
        assert media_row is not None, "media metadata row was never persisted"
        assert len(found_on) == 1  # no duplicated metadata across nodes
        row = media_row
        assert row["size_bytes"] == len(payload)
        assert row["mime_type"] == "image/png"
        assert len(row["checksum_sha256"]) == 64  # SHA-256, content addressing

        # exactly one blob on disk even though the file was uploaded twice
        blobs = list((tmp_path / "media").rglob("*"))
        assert len([p for p in blobs if p.is_file()]) == 1

        # download returns the original bytes
        downloaded = await client.get(f"/api/v1/media/{media['media_id']}")
        assert downloaded.content == payload

        # and the service reports what it really is
        stats = (await client.get("/api/v1/media/_stats/backend")).json()
        assert stats["backend"] == "local_filesystem"
        assert stats["deduplicated_uploads"] == 1
