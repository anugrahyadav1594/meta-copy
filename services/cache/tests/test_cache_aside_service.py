"""Cache-aside behaviour at the PostService layer.

The cache sits ABOVE the repository: repository call counts prove when the
database is actually contacted. Uses the ``memory://`` fakeredis backend.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.services.post_service import (
    CACHE_SOURCE_BYPASS,
    CACHE_SOURCE_HIT,
    CACHE_SOURCE_MISS,
    PostService,
)
from common.exceptions import NotFoundError
from schemas.post import PostCreate, PostRead, PostUpdate

from services.cache.cache import CacheProvider

pytestmark = pytest.mark.asyncio


class FakePost:
    def __init__(self, post_id: int, author_id: int = 1, content: str = "hello") -> None:
        self.id = post_id
        self.author_id = author_id
        self.content = content
        self.visibility = "public"
        self.created_at = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
        self.updated_at = datetime(2026, 9, 11, 9, 5, tzinfo=UTC)


class FakePostRepo:
    def __init__(self, posts: dict[int, FakePost] | None = None) -> None:
        self.posts = posts or {}
        self.get_calls = 0

    async def get_by_id(self, post_id: int):
        self.get_calls += 1
        return self.posts.get(post_id)

    async def create(self, *, post_id, author_id, content, visibility):
        new_id = max(self.posts, default=0) + 1
        post = FakePost(new_id, author_id, content)
        self.posts[new_id] = post
        return post

    async def update(self, post_id: int, **data):
        if post_id not in self.posts:
            raise LookupError(f"post {post_id} not found")
        for k, v in data.items():
            setattr(self.posts[post_id], k, v)
        return self.posts[post_id]

    async def delete(self, post_id: int) -> bool:
        return self.posts.pop(post_id, None) is not None

    async def list_recent(self, limit: int = 50):
        return list(self.posts.values())[:limit]


class FakeUserRepo:
    async def get_by_id(self, user_id: int):
        return object() if user_id == 1 else None


class FakeCommentRepo:
    async def list_for_post(self, post_id, limit=50):
        return []

    async def create(self, *, post_id, user_id, content):
        return None


@pytest.fixture
async def cache() -> CacheProvider:
    provider = await CacheProvider.create("memory://", default_ttl=60)
    assert provider is not None
    yield provider
    await provider.aclose()


def make_service(cache):
    repo = FakePostRepo({1: FakePost(1)})
    svc = PostService(repo, FakeCommentRepo(), FakeUserRepo(), cache)
    return svc, repo


async def test_first_read_misses_second_read_hits(cache) -> None:
    svc, repo = make_service(cache)

    post1, source1 = await svc.get(1)
    post2, source2 = await svc.get(1)

    assert source1 == CACHE_SOURCE_MISS
    assert source2 == CACHE_SOURCE_HIT
    assert repo.get_calls == 1  # hit avoided the database entirely

    # A cache hit still validates against the FULL PostRead contract —
    # the original Member 6 bug cached only 4 fields and broke response_model.
    cached_payload = await cache.get("post:1")
    assert set(cached_payload) == {
        "id",
        "author_id",
        "content",
        "visibility",
        "created_at",
        "updated_at",
    }
    cached = PostRead.model_validate(post2)
    direct = PostRead.model_validate(post1)
    assert cached == direct


async def test_bypass_when_cache_disabled() -> None:
    repo = FakePostRepo({1: FakePost(1)})
    svc = PostService(repo, FakeCommentRepo(), FakeUserRepo(), None)

    post, source = await svc.get(1)
    assert source == CACHE_SOURCE_BYPASS
    assert isinstance(post, FakePost)
    assert await svc.cache_metrics() == {"cache_enabled": False}


async def test_update_invalidates_cached_post(cache) -> None:
    svc, repo = make_service(cache)

    await svc.get(1)  # MISS -> cached
    _, source = await svc.get(1)
    assert source == CACHE_SOURCE_HIT

    updated = await svc.update(1, PostUpdate(content="edited"))
    assert updated.content == "edited"

    _, source = await svc.get(1)
    assert source == CACHE_SOURCE_MISS  # invalidated -> re-read from DB
    assert repo.get_calls == 2
    # The freshly cached value reflects the write.
    assert (await cache.get("post:1"))["content"] == "edited"


async def test_delete_invalidates_cached_post(cache) -> None:
    svc, repo = make_service(cache)
    await svc.get(1)  # populate cache
    assert await cache.get("post:1") is not None

    assert await svc.delete(1) is True
    assert await cache.get("post:1") is None

    with pytest.raises(NotFoundError):
        await svc.get(1)


async def test_missing_post_never_cached(cache) -> None:
    svc, _repo = make_service(cache)
    with pytest.raises(NotFoundError):
        await svc.get(404)
    assert await cache.get("post:404") is None  # direct probe = second miss
    metrics = await svc.cache_metrics()
    assert metrics["cache_enabled"] is True
    assert metrics["cache_misses"] == 2
    assert metrics["cache_sets"] == 0


async def test_create_then_read_flow(cache) -> None:
    repo = FakePostRepo({})
    svc = PostService(repo, FakeCommentRepo(), FakeUserRepo(), cache)
    created = await svc.create(PostCreate(author_id=1, content="fresh", visibility="public"))
    assert PostRead.model_validate(created).content == "fresh"

    _, source = await svc.get(created.id)
    assert source == CACHE_SOURCE_MISS
    _, source = await svc.get(created.id)
    assert source == CACHE_SOURCE_HIT
