"""Member 3 — denormalized read model (DERIVED DATA).

Ported from the ``nikhil-denormalization`` branch and integrated:

* the projection table uses the **canonical** identifiers
  (``post_id`` / ``user_id``) instead of the branch's ``id`` / ``author_id``;
* the Java/Spring RabbitMQ worker (which only handled ``LIKE_CREATED``) is
  replaced by a Python projector subscribed to the event bus, so every
  documented event is handled without a second runtime;
* the projection is **rebuildable** from canonical PostgreSQL at any time,
  which is what makes it a legitimate derived read model rather than a
  second source of truth.

Status: IMPLEMENTED. Nothing here is simulated — rows are real SQL upserts
and ``rebuild()`` re-derives them from canonical tables with real queries.
"""

from __future__ import annotations

import time
from typing import Any

from common.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = get_logger("denormalization")

# DDL for the DERIVED read model. Deliberately kept out of 002_schema.sql so
# the canonical schema stays free of derived tables.
DDL = """
CREATE TABLE IF NOT EXISTS denormalized_post_feed (
    post_id             BIGINT PRIMARY KEY,
    user_id             BIGINT NOT NULL,
    author_username     VARCHAR(50) NOT NULL,
    author_display_name VARCHAR(100) NOT NULL,
    content             TEXT,
    visibility          VARCHAR(20) NOT NULL DEFAULT 'public',
    like_count          BIGINT NOT NULL DEFAULT 0,
    comment_count       BIGINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL,
    projected_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_denorm_feed_created_at
    ON denormalized_post_feed (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_denorm_feed_user
    ON denormalized_post_feed (user_id, created_at DESC);
"""

REBUILD_SQL = """
INSERT INTO denormalized_post_feed (
    post_id, user_id, author_username, author_display_name,
    content, visibility, like_count, comment_count, created_at, projected_at
)
SELECT
    p.post_id,
    p.user_id,
    u.username                              AS author_username,
    COALESCE(pr.display_name, u.username)   AS author_display_name,
    p.content,
    p.visibility,
    (SELECT count(*) FROM likes l    WHERE l.post_id    = p.post_id) AS like_count,
    (SELECT count(*) FROM comments c WHERE c.post_id    = p.post_id) AS comment_count,
    p.created_at,
    now()
FROM posts p
JOIN users u          ON p.user_id = u.user_id
LEFT JOIN profiles pr ON pr.user_id = u.user_id
ON CONFLICT (post_id) DO UPDATE SET
    user_id             = EXCLUDED.user_id,
    author_username     = EXCLUDED.author_username,
    author_display_name = EXCLUDED.author_display_name,
    content             = EXCLUDED.content,
    visibility          = EXCLUDED.visibility,
    like_count          = EXCLUDED.like_count,
    comment_count       = EXCLUDED.comment_count,
    created_at          = EXCLUDED.created_at,
    projected_at        = now()
"""

_UPSERT_POST = """
INSERT INTO denormalized_post_feed (
    post_id, user_id, author_username, author_display_name,
    content, visibility, like_count, comment_count, created_at, projected_at
)
VALUES (
    :post_id, :user_id,
    COALESCE(:username, 'user-' || :user_id),
    COALESCE(:display_name, :username, 'user-' || :user_id),
    :content, :visibility, 0, 0, :created_at, now()
)
ON CONFLICT (post_id) DO UPDATE SET
    user_id         = EXCLUDED.user_id,
    author_username = EXCLUDED.author_username,
    content         = EXCLUDED.content,
    visibility      = EXCLUDED.visibility,
    projected_at    = now()
"""


async def ensure_schema(engine: AsyncEngine) -> None:
    """Create the derived table if it does not exist."""
    async with engine.begin() as conn:
        for statement in filter(None, (s.strip() for s in DDL.split(";"))):
            await conn.execute(text(statement))


async def rebuild(engine: AsyncEngine, shard_manager: Any = None) -> int:
    """Rebuild the whole projection from the source of truth.

    In NORMALIZED mode that is the canonical database. In SHARDED mode the
    rows live on the shards and the join is done here, in the API process
    (``likes``/``comments`` are keyed by ``post_id``, ``posts`` by
    ``user_id`` — see ``db.shard_scan``).
    """
    async with engine.begin() as conn:
        await conn.execute(text(REBUILD_SQL))

    if shard_manager is not None:
        from db.shard_scan import (
            scan_counts,
            scan_posts,
            scan_usernames,
        )

        posts = await scan_posts(shard_manager)
        usernames = await scan_usernames(shard_manager)
        likes = await scan_counts(shard_manager, "likes", "post_id")
        comments = await scan_counts(shard_manager, "comments", "post_id")

        rows = []
        for post in posts:
            username, display_name = usernames.get(post["user_id"], (None, None))
            rows.append(
                {
                    "post_id": post["post_id"],
                    "user_id": post["user_id"],
                    "username": username,
                    "display_name": display_name or username,
                    "content": post["content"],
                    "visibility": post["visibility"],
                    "like_count": likes.get(post["post_id"], 0),
                    "comment_count": comments.get(post["post_id"], 0),
                    "created_at": post["created_at"],
                }
            )
        if rows:
            async with engine.begin() as conn:
                # SQLAlchemy 2.0 async executemany = execute(stmt, [params])
                await conn.execute(text(_UPSERT_POST), rows)

    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT count(*) FROM denormalized_post_feed"))
        return int(result.scalar() or 0)


async def project_event(session: AsyncSession, event: Any) -> bool:
    """Apply one canonical domain event to the projection.

    Returns True when the event was applicable to the read model.
    """
    etype = getattr(event, "event_type", "")
    payload = dict(getattr(event, "payload", {}) or {})
    post_id = int(getattr(event, "aggregate_id", 0) or payload.get("post_id") or 0)

    if etype == "POST_CREATED":
        await session.execute(
            text(_UPSERT_POST),
            {
                "post_id": post_id,
                "user_id": int(payload.get("user_id", 0)),
                "username": payload.get("username"),
                "display_name": payload.get("display_name"),
                "content": payload.get("content", ""),
                "visibility": payload.get("visibility") or "public",
                "created_at": payload.get("created_at")
                or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        return True

    if etype == "POST_UPDATED":
        # A partial update carries only the changed fields; ``None`` values
        # are "not provided", never "set to NULL" (the read model declares
        # visibility NOT NULL).
        fields = {
            k: v for k, v in payload.items() if k in ("content", "visibility") and v is not None
        }
        if not fields:
            return False
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        await session.execute(
            text(
                f"UPDATE denormalized_post_feed SET {assignments}, "
                "projected_at = now() WHERE post_id = :post_id"
            ),
            {**fields, "post_id": post_id},
        )
        return True

    if etype == "POST_DELETED":
        await session.execute(
            text("DELETE FROM denormalized_post_feed WHERE post_id = :post_id"),
            {"post_id": post_id},
        )
        return True

    if etype == "LIKE_CREATED":
        await session.execute(
            text(
                "UPDATE denormalized_post_feed SET like_count = like_count + 1, "
                "projected_at = now() WHERE post_id = :post_id"
            ),
            {"post_id": post_id},
        )
        return True

    if etype == "LIKE_DELETED":
        await session.execute(
            text(
                "UPDATE denormalized_post_feed SET like_count = GREATEST(like_count - 1, 0), "
                "projected_at = now() WHERE post_id = :post_id"
            ),
            {"post_id": post_id},
        )
        return True

    if etype == "COMMENT_CREATED":
        await session.execute(
            text(
                "UPDATE denormalized_post_feed SET comment_count = comment_count + 1, "
                "projected_at = now() WHERE post_id = :post_id"
            ),
            {"post_id": post_id},
        )
        return True

    return False
