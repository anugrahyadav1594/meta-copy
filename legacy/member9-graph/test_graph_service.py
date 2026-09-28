"""Member 9 graph tests — preserved for reference, NOT part of the active suite.

These were removed from tests/unit/test_projections.py when the graph module
was excluded from the final integrated iteration. They are kept here so the
work is not lost. They are not run by `make test` because the module they
import is no longer part of the system.
"""

# ---------------------------------------------------------------- Member 9
@dataclass
class FakeFollow:
    follower_id: int
    following_id: int


class FakeFollowRepo:
    """Minimal stand-in for the canonical `follows` table."""

    def __init__(self, edges: list[tuple[int, int]]) -> None:
        self.edges = [FakeFollow(a, b) for a, b in edges]
        self.reads = 0

    async def follower_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        self.reads += 1
        return [e.follower_id for e in self.edges if e.following_id == user_id][:limit]

    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        self.reads += 1
        return [e.following_id for e in self.edges if e.follower_id == user_id][:limit]

    async def list_followers(self, user_id: int, limit: int = 100) -> list[Any]:
        return [e for e in self.edges if e.following_id == user_id][:limit]

    async def list_following(self, user_id: int, limit: int = 100) -> list[Any]:
        return [e for e in self.edges if e.follower_id == user_id][:limit]

    async def counts(self, user_id: int) -> tuple[int, int]:
        return (
            len(await self.follower_ids(user_id)),
            len(await self.following_ids(user_id)),
        )

    async def is_following(self, follower_id: int, following_id: int) -> bool:
        return any(
            e.follower_id == follower_id and e.following_id == following_id for e in self.edges
        )


@pytest.mark.asyncio
async def test_graph_is_a_rebuildable_projection() -> None:
    repo = FakeFollowRepo([(1, 2), (1, 3), (2, 3), (4, 2)])
    graph = SocialGraph(repo, cache_ttl_seconds=30)

    assert sorted(await graph.following(1)) == [2, 3]
    assert sorted(await graph.followers(2)) == [1, 4]
    assert await graph.mutuals(1, 2) == [3]  # both follow 3
    assert await graph.is_following(1, 3) is True
    assert await graph.is_following(3, 1) is False

    # TAO-style degrees
    assert await graph.degrees(1) == {"followers": 0, "following": 2}
    assert await graph.edge_count(1, "following") == 2

    # cached: a second read must not hit PostgreSQL again
    before = repo.reads
    await graph.following(1)
    assert repo.reads == before

    stats = graph.stats()
    assert stats["derived_projection"] is True
    assert stats["source_of_truth"] == "postgresql:follows"

    # rebuild recomputes everything from the source of truth
    result = await graph.rebuild()
    assert result["derived"] is True
    assert result["source_of_truth"] == "postgresql:follows"
    assert result["rebuilds"] == 1
    assert graph.stats()["cached_nodes"] == 0  # cache dropped: rebuilt on demand
    assert sorted(await graph.following(1)) == [2, 3]


@pytest.mark.asyncio
async def test_graph_cache_expires_and_can_be_invalidated() -> None:
    repo = FakeFollowRepo([(1, 2)])
    graph = SocialGraph(repo, cache_ttl_seconds=1)

    assert await graph.following(1) == [2]
    # simulate a canonical write the graph does not know about
    repo.edges.append(FakeFollow(1, 5))
    assert await graph.following(1) == [2]  # still cached

    graph.invalidate(1)
    assert sorted(await graph.following(1)) == [2, 5]  # re-read from PostgreSQL


@pytest.mark.asyncio
async def test_graph_suggestions_come_from_second_degree_edges() -> None:
    repo = FakeFollowRepo([(1, 2), (2, 3), (2, 4)])
    graph = SocialGraph(repo, cache_ttl_seconds=30)
    assert sorted(await graph.suggestions(1, limit=10)) == [3, 4]


