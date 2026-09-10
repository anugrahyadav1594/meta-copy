"""End-to-end cache behaviour through the FastAPI routers (no database).

Repositories are fakes and the cache is the ``memory://`` fakeredis backend,
so these tests exercise routing, the ``X-Cache`` header, response_model
validation on a cache HIT, invalidation and the cache observability endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api.dependencies import get_platform
from api.routes.cache import router as cache_router
from api.routes.posts import router as posts_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from schemas.post import PostUpdate

from services.cache.cache import CacheProvider
from services.cache.tests.test_cache_aside_service import (
    FakeCommentRepo,
    FakePost,
    FakePostRepo,
    FakeUserRepo,
)

pytestmark = pytest.mark.asyncio


def build_app(cache):
    app = FastAPI()
    repo = FakePostRepo({1: FakePost(1)})
    platform = SimpleNamespace(
        post_repo=repo,
        comment_repo=FakeCommentRepo(),
        user_repo=FakeUserRepo(),
        cache=cache,
    )
    app.dependency_overrides[get_platform] = lambda: platform
    app.include_router(posts_router, prefix="/api/v1")
    app.include_router(cache_router, prefix="/api/v1")
    return app, repo


@pytest.fixture
async def cache():
    provider = await CacheProvider.create("memory://")
    assert provider is not None
    yield provider
    await provider.aclose()


async def test_get_post_miss_then_hit_header_and_shape(cache) -> None:
    app, _ = build_app(cache)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r1 = await c.get("/api/v1/posts/1")
        r2 = await c.get("/api/v1/posts/1")

    assert r1.status_code == r2.status_code == 200
    assert r1.headers["X-Cache"] == "MISS"
    assert r2.headers["X-Cache"] == "HIT"

    body = r2.json()
    # Cache hits must satisfy the full PostRead contract (regression test for
    # the original 4-field cached dict that failed response validation).
    assert set(body) == {
        "id",
        "author_id",
        "content",
        "visibility",
        "created_at",
        "updated_at",
    }


async def test_update_invalidates_and_next_read_is_miss(cache) -> None:
    app, _ = build_app(cache)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.get("/api/v1/posts/1")
        assert (await c.get("/api/v1/posts/1")).headers["X-Cache"] == "HIT"

        patched = await c.patch(
            "/api/v1/posts/1",
            json=PostUpdate(content="changed").model_dump(exclude_unset=True),
        )
        assert patched.status_code == 200
        assert patched.json()["content"] == "changed"

        after = await c.get("/api/v1/posts/1")
        assert after.headers["X-Cache"] == "MISS"
        assert after.json()["content"] == "changed"


async def test_cache_observability_endpoints(cache) -> None:
    app, _ = build_app(cache)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.get("/api/v1/posts/1")
        await c.get("/api/v1/posts/1")

        metrics = (await c.get("/api/v1/cache/metrics")).json()
        assert metrics["cache_enabled"] is True
        assert metrics["cache_hits"] == 1
        assert metrics["cache_misses"] == 1
        assert metrics["hit_ratio"] == 50.0
        assert metrics["db_queries_avoided"] == 1

        health = (await c.get("/api/v1/cache/health")).json()
        assert health == {"cache_enabled": True, "status": "healthy", "backend": "memory"}

        keys = (await c.get("/api/v1/cache/keys")).json()
        assert keys["count"] == 1 and keys["keys"] == ["post:1"]


async def test_manual_invalidation_endpoint(cache) -> None:
    app, _ = build_app(cache)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.get("/api/v1/posts/1")
        r = await c.delete("/api/v1/cache/posts/1")
        assert r.status_code == 200 and r.json()["invalidated"] is True
        assert (await c.get("/api/v1/posts/1")).headers["X-Cache"] == "MISS"


async def test_cache_disabled_endpoints_and_bypass() -> None:
    app, _ = build_app(None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/posts/1")
        assert r.status_code == 200
        assert r.headers["X-Cache"] == "BYPASS"

        metrics = (await c.get("/api/v1/cache/metrics")).json()
        assert metrics == {
            "cache_enabled": False,
            "message": "Redis cache is disabled or failed to start (fail-open).",
        }
        health = (await c.get("/api/v1/cache/health")).json()
        assert health == {"cache_enabled": False, "status": "disabled"}
        keys = (await c.get("/api/v1/cache/keys")).json()
        assert keys == {"cache_enabled": False, "keys": []}
