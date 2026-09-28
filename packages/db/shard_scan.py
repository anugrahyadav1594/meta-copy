"""Scatter-gather helpers for DERIVED projections (feeds, read models, indexes).

Why this module exists
----------------------
Rows are placed by *access pattern*, not by table:

    users/profiles/posts  -> user_id      (one author's data is co-located)
    comments/likes        -> post_id      (one post's data is co-located)
    follows               -> follower_id  (one reader's edge list is co-located)

That co-location is exactly what makes the hot paths cheap, but it also means
a JOIN written *inside* one shard is wrong for cross-entity queries: an
author's posts live on ``shard(user_id)`` while the edges pointing at that
author live on ``shard(follower_id)``. A per-shard ``posts JOIN follows``
therefore silently returns an under-populated result.

The correct shape for a derived rebuild is:

    scatter the base tables -> gather into the API process -> join in Python
    -> write the projection into the canonical database.

These rebuilds run on operator request (``/feed/rebuild``,
``/search/reindex``, ``/graph/rebuild``) and are bounded by the dataset size;
a production system would stream the same thing through a batch job. That
limitation is documented in RUNBOOK.md.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


async def scatter_rows(
    shard_manager: Any, sql: str, params: dict[str, Any] | None = None
) -> list[tuple[Any, ...]]:
    """Run ``sql`` on every shard and return the union of the rows."""
    rows: list[tuple[Any, ...]] = []
    for shard_id in shard_manager.shard_ids:
        async with shard_manager.session_scope(shard_id, readonly=True) as session:
            result = await session.execute(text(sql), params or {})
            rows.extend(tuple(r) for r in result.all())
    return rows


async def scan_follow_edges(shard_manager: Any) -> list[tuple[int, int]]:
    """Every (follower_id, following_id) edge across all shards."""
    rows = await scatter_rows(shard_manager, "SELECT follower_id, following_id FROM follows")
    return [(int(r[0]), int(r[1])) for r in rows]


async def scan_posts(shard_manager: Any) -> list[dict[str, Any]]:
    """Every post across all shards (metadata needed by projections)."""
    rows = await scatter_rows(
        shard_manager,
        "SELECT post_id, user_id, content, visibility, created_at FROM posts",
    )
    return [
        {
            "post_id": int(r[0]),
            "user_id": int(r[1]),
            "content": r[2],
            "visibility": r[3] or "public",
            "created_at": r[4],
        }
        for r in rows
    ]


async def scan_counts(shard_manager: Any, table: str, key: str) -> dict[int, int]:
    """``{post_id: n}`` counts gathered across all shards.

    ``likes`` and ``comments`` are keyed by ``post_id``, so a single post's
    counters live together — but different posts sit on different shards, so
    the counts must still be gathered shard by shard.
    """
    counts: dict[int, int] = {}
    rows = await scatter_rows(shard_manager, f"SELECT {key}, count(*) FROM {table} GROUP BY {key}")
    for key_value, total in rows:
        counts[int(key_value)] = counts.get(int(key_value), 0) + int(total or 0)
    return counts


async def scan_usernames(shard_manager: Any) -> dict[int, tuple[str, str | None]]:
    """``{user_id: (username, display_name)}`` across all shards."""
    rows = await scatter_rows(
        shard_manager,
        "SELECT u.user_id, u.username, p.display_name "
        "FROM users u LEFT JOIN profiles p ON p.user_id = u.user_id",
    )
    return {int(r[0]): (r[1], r[2]) for r in rows}
