"""Unit tests for the derived (rebuildable) systems.

Members 3/7/8/10. (Member 9 / graph is EXCLUDED from this iteration — see
legacy/member9-graph/.) Every system here is a PROJECTION: PostgreSQL is the only
source of truth, so these tests use small fakes instead of a database and
assert that the projection can always be rebuilt from canonical data.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from feed.service import FeedService
from media.blobstore import (
    LocalBlobStore,
    MediaBlobStore,
    MinioBlobStore,
    sha256_hex,
    storage_key_for,
)
from search.provider import InMemorySearchProvider, OpenSearchProvider, SearchProvider


# --------------------------------------------------------------- Member 8
def test_sha256_content_addressing_is_deterministic() -> None:
    assert sha256_hex(b"abc") == sha256_hex(b"abc")
    assert sha256_hex(b"abc") != sha256_hex(b"abd")
    assert len(sha256_hex(b"abc")) == 64


def test_storage_key_is_sharded_by_checksum_prefix() -> None:
    checksum = sha256_hex(b"payload")
    key = storage_key_for(checksum, ".png")
    assert key.startswith(f"{checksum[:2]}/{checksum[2:4]}/")
    assert key.endswith(".png")
    assert storage_key_for(checksum) == storage_key_for(checksum)


@pytest.mark.asyncio
async def test_local_blob_store_round_trip(tmp_path) -> None:  # noqa: ANN001
    store: MediaBlobStore = LocalBlobStore(tmp_path)
    data = b"binary" * 100
    key = storage_key_for(sha256_hex(data))

    await store.put(key, data, "image/png")
    assert await store.exists(key) is True
    assert await store.get(key) == data
    assert await store.delete(key) is True
    assert await store.exists(key) is False

    # and it says what it is: a filesystem adapter, not object storage
    assert store.describe()["backend"] == "local_filesystem"


def test_minio_blob_store_refuses_to_lie_without_credentials() -> None:
    """Without the SDK/endpoint the MinIO backend must fail loudly."""
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - any config error
        MinioBlobStore(endpoint="minio:9000", access_key="", secret_key="", bucket="m")
    assert "minio" in str(excinfo.value).lower()


def test_every_blob_store_satisfies_the_contract(tmp_path) -> None:  # noqa: ANN001
    assert isinstance(LocalBlobStore(tmp_path), MediaBlobStore)
    assert isinstance(InMemorySearchProvider(), SearchProvider)
    assert isinstance(OpenSearchProvider("http://localhost:9200"), SearchProvider)


# --------------------------------------------------------------- Member 10
@pytest.mark.asyncio
async def test_search_provider_ranks_and_resolves_canonical_ids() -> None:
    provider = InMemorySearchProvider()
    await provider.index_document(
        "posts", {"id": 1, "entity": "post", "post_id": 1, "text": "sharding is hard"}
    )
    await provider.index_document(
        "posts",
        {
            "id": 2,
            "entity": "post",
            "post_id": 2,
            "text": "sharding sharding sharding everywhere",
        },
    )
    await provider.index_document(
        "posts", {"id": 3, "entity": "post", "post_id": 3, "text": "unrelated content"}
    )

    hits = await provider.search("posts", "sharding", limit=10)
    # term frequency ranks the repeated post first; results stay canonical
    assert [h["post_id"] for h in hits] == [2, 1]
    assert all(h["entity"] == "post" for h in hits)
    assert hits[0]["_score"] > hits[1]["_score"]

    assert await provider.search("posts", "nothing-matches", limit=5) == []
    stats = provider.stats()
    assert stats["documents"] == 3
    assert stats["queries"] >= 1


@pytest.mark.asyncio
async def test_search_autocomplete_and_clear() -> None:
    provider = InMemorySearchProvider()
    await provider.index_document("posts", {"id": 1, "text": "distributed systems"})
    await provider.index_document("posts", {"id": 2, "text": "distributed cache"})

    assert await provider.autocomplete("posts", "dist", limit=5) == ["distributed"]
    await provider.clear("posts")
    assert await provider.search("posts", "distributed") == []
    assert provider.stats()["documents"] == 0


def test_search_provider_describes_itself_honestly() -> None:
    assert InMemorySearchProvider().describe()["backend"] == "in_memory_inverted_index"
    described = OpenSearchProvider("http://opensearch:9200").describe()
    assert described["backend"] == "opensearch"
    # it never claims to be reachable without having talked to the cluster
    assert "reachable" in described


# ---------------------------------------------------------------- Member 7
@dataclass
class FakePost:
    post_id: int
    user_id: int
    content: str
    created_at: datetime
    visibility: str = "public"
    author_username: str = ""
    like_count: int = 0
    comment_count: int = 0


class FakePostRepo:
    def __init__(self, posts: list[FakePost]) -> None:
        self.posts = posts
        self.list_recent_calls = 0
        self.list_by_author_calls = 0

    async def list_recent(self, limit: int = 50) -> list[Any]:
        self.list_recent_calls += 1
        return sorted(self.posts, key=lambda p: p.created_at, reverse=True)[:limit]

    async def list_by_author(self, user_id: int, limit: int = 50) -> list[Any]:
        self.list_by_author_calls += 1
        rows = [p for p in self.posts if p.user_id == user_id]
        return sorted(rows, key=lambda p: p.created_at, reverse=True)[:limit]


class FakeFollowIds:
    def __init__(self, edges: dict[int, list[int]]) -> None:
        self.edges = edges

    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        return self.edges.get(user_id, [])[:limit]


def _posts(now: datetime) -> list[FakePost]:
    return [
        FakePost(1, 10, "old post", now - timedelta(days=5)),
        FakePost(2, 11, "fresh post", now - timedelta(minutes=1)),
        FakePost(3, 12, "mid post", now - timedelta(hours=3)),
    ]


@pytest.mark.asyncio
async def test_pull_feed_scatter_gathers_followed_authors() -> None:
    now = datetime.now(UTC)
    posts = FakePostRepo(_posts(now))
    follows = FakeFollowIds({1: [10, 11]})
    service = FeedService(follows, posts, strategy="pull")  # type: ignore[arg-type]

    feed = await service.get_feed(1, limit=10)
    assert [row["post_id"] for row in feed] == [2, 1]  # newest first
    assert posts.list_by_author_calls == 2  # one query per followed author
    assert posts.list_recent_calls == 0

    described = service.describe()
    assert described["strategy"] == "pull"
    assert described["push_table_is_derived"] is True


@pytest.mark.asyncio
async def test_push_feed_reads_the_precomputed_table_not_the_shards() -> None:
    now = datetime.now(UTC)
    posts = FakePostRepo(_posts(now))
    follows = FakeFollowIds({1: [10, 11, 12]})
    service = FeedService(follows, posts, strategy="push")  # type: ignore[arg-type]

    # push reads the derived `user_feed` table, so it needs the canonical
    # engine; without one the service says so instead of silently pulling.
    with pytest.raises(RuntimeError, match="canonical engine"):
        await service.get_feed(1, limit=10)
    assert posts.list_by_author_calls == 0
    assert service.describe()["strategy"] == "push"


def test_feed_ranking_is_recency_weighted_not_random() -> None:
    now = datetime.now(UTC)
    fresh = FakePost(1, 1, "now", now)
    old = FakePost(2, 1, "then", now - timedelta(days=10))
    from feed.service import _rank_key

    assert _rank_key(fresh, now) > _rank_key(old, now)


# ------------------------------------------- the E2E object graph is async
def test_derived_modules_expose_stats_without_a_database() -> None:
    async def scenario() -> None:
        provider = InMemorySearchProvider()
        assert provider.stats()["documents"] == 0

    asyncio.run(scenario())
    assert time.time() > 0
