"""Member 7 — feed generation (integrated).

Ported from the ``member-7-feed`` branch and adapted so it works through the
**repository interfaces** instead of a single hard-wired SQLAlchemy engine.
That was the branch's main integration defect: the feed only worked in
NORMALIZED mode and called ``CREATE TABLE`` on every GET request.

Two strategies, both real:

``pull`` (default)
    follow graph -> candidate posts -> rank -> feed.
    Uses ``FollowRepository.following_ids`` + ``PostRepository.list_recent``,
    so it is correct in NORMALIZED *and* SHARDED mode (the post listing
    becomes a scatter-gather under the hood).

``push`` (fan-out-on-write)
    A derived ``user_feed`` table maintained by :class:`FeedProjector` from
    domain events. It is marked derived and rebuildable — never a source of
    truth. The DDL is created once at start-up, never per request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from common.logging import get_logger

logger = get_logger("feed")

# Derived fan-out table (only used when strategy == "push").
PUSH_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS user_feed (
    user_id    BIGINT NOT NULL,
    post_id    BIGINT NOT NULL,
    user_id_author BIGINT NOT NULL,
    content    TEXT NOT NULL,
    visibility VARCHAR(20) NOT NULL DEFAULT 'public',
    created_at TIMESTAMPTZ NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, post_id)
);

CREATE INDEX IF NOT EXISTS idx_user_feed_user_created
    ON user_feed (user_id, created_at DESC);
"""


class FollowRepo(Protocol):
    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]: ...


class PostRepo(Protocol):
    async def list_recent(self, limit: int = 50) -> list[Any]: ...

    async def list_by_author(self, user_id: int, limit: int = 50) -> list[Any]: ...


def _created_at(row: Any) -> datetime:
    value = getattr(row, "created_at", None)
    if isinstance(value, datetime):
        return value
    return datetime.min


def _rank_key(row: Any, now: datetime) -> tuple[float, datetime]:
    """Recency-weighted ranking.

    Simple, honest ranking: newer posts rank higher. (No engagement counts are
    available on the post row itself; the denormalized read model carries
    like/comment counters and is used when present.)
    """
    created = _created_at(row)
    age_hours = max((now - created).total_seconds(), 0.0) / 3600.0
    return (1.0 / (1.0 + age_hours), created)


class FeedService:
    """Feed assembly above the repositories (topology-agnostic)."""

    def __init__(
        self,
        follow_repo: FollowRepo,
        post_repo: PostRepo,
        *,
        strategy: str = "pull",
        candidate_limit: int = 500,
        engine: Any = None,
    ) -> None:
        self.follows = follow_repo
        self.posts = post_repo
        self.strategy = (strategy or "pull").lower()
        self.candidate_limit = candidate_limit
        self.engine = engine  # only needed for the push strategy

    # ------------------------------------------------------------------ pull
    async def pull_feed(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        following = await self.follows.following_ids(user_id)
        if not following:
            return []

        # Candidate generation: recent posts (scatter-gather in SHARDED mode)
        # filtered to the follow set. When the follow set is small it is
        # cheaper to ask each author's shard directly; both paths are real.
        if len(following) <= 8:
            rows: list[Any] = []
            for author_id in following:
                rows.extend(await self.posts.list_by_author(author_id, limit=limit))
        else:
            candidates = await self.posts.list_recent(limit=self.candidate_limit)
            rows = [p for p in candidates if getattr(p, "user_id", None) in set(following)]

        now = datetime.now(_created_at(rows[0]).tzinfo) if rows else datetime.now()
        rows.sort(key=lambda r: _rank_key(r, now), reverse=True)
        return [self._serialize(r) for r in rows[:limit]]

    # ------------------------------------------------------------------ push
    async def ensure_push_table(self) -> None:
        """Create the derived fan-out table once (never inside a GET)."""
        if self.engine is None:
            return
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            for statement in filter(None, (s.strip() for s in PUSH_TABLE_DDL.split(";"))):
                await conn.execute(text(statement))

    async def push_feed(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Read the pre-computed fan-out table (derived, may be empty)."""
        if self.engine is None:
            raise RuntimeError("push strategy requires the canonical engine")
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            res = await conn.execute(
                text(
                    "SELECT post_id, user_id_author, content, visibility, created_at "
                    "FROM user_feed WHERE user_id = :uid "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"uid": user_id, "limit": limit},
            )
            rows = res.mappings().all()
        return [
            {
                "post_id": r["post_id"],
                "user_id": r["user_id_author"],
                "content": r["content"],
                "visibility": r["visibility"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "source": "push_projection",
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ API
    async def get_feed(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        if self.strategy == "push":
            return await self.push_feed(user_id, limit=limit)
        return await self.pull_feed(user_id, limit=limit)

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        created = _created_at(row)
        return {
            "post_id": getattr(row, "post_id", None),
            "user_id": getattr(row, "user_id", None),
            "content": getattr(row, "content", ""),
            "visibility": getattr(row, "visibility", "public"),
            "created_at": created.isoformat() if isinstance(created, datetime) else None,
            "source": "canonical",
        }

    def describe(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "candidate_limit": self.candidate_limit,
            "ranking": "recency_weighted",
            "push_table_is_derived": True,
        }


class FeedProjector:
    """Fan-out-on-write: POST_CREATED -> every follower's ``user_feed`` row.

    Subscribes to the event bus. The table is fully rebuildable from the
    canonical ``follows`` + ``posts`` tables.
    """

    def __init__(
        self,
        engine: Any,
        follow_repo: FollowRepo,
        event_bus: Any,
        shard_manager: Any = None,
    ) -> None:
        self.engine = engine
        self.follows = follow_repo
        self.event_bus = event_bus
        # In SHARDED mode the canonical database is NOT the data location: the
        # projection must be rebuilt by scattering over the shards.
        self.shard_manager = shard_manager
        self.rows_written = 0
        self.errors = 0

    async def start(self) -> None:
        await self.ensure_schema()
        self.event_bus.subscribe("POST_CREATED", self.on_post_created)

    async def ensure_schema(self) -> None:
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            for statement in filter(None, (s.strip() for s in PUSH_TABLE_DDL.split(";"))):
                await conn.execute(text(statement))

    async def on_post_created(self, event: Any) -> None:
        from sqlalchemy import text

        payload = dict(getattr(event, "payload", {}) or {})
        post_id = int(getattr(event, "aggregate_id", 0) or payload.get("post_id") or 0)
        author_id = int(payload.get("user_id", 0))
        if not post_id or not author_id:
            return
        try:
            followers = await self.follows.follower_ids(author_id)
            async with self.engine.begin() as conn:
                for follower_id in followers:
                    await conn.execute(
                        text(
                            "INSERT INTO user_feed (user_id, post_id, user_id_author, "
                            "content, visibility, created_at, projected_at) "
                            "VALUES (:uid, :pid, :author, :content, :visibility, "
                            "COALESCE(CAST(:created_at AS timestamptz), now()), now()) "
                            "ON CONFLICT (user_id, post_id) DO NOTHING"
                        ),
                        {
                            "uid": follower_id,
                            "pid": post_id,
                            "author": author_id,
                            "content": payload.get("content", ""),
                            "visibility": payload.get("visibility", "public"),
                            "created_at": payload.get("created_at"),
                        },
                    )
            self.rows_written += len(followers)
        except Exception as exc:
            self.errors += 1
            logger.error(
                "feed_fanout_failed",
                post_id=post_id,
                error=type(exc).__name__,
                detail=str(getattr(exc, "orig", exc))[:300],
            )

    async def rebuild(self) -> int:
        """Rebuild the whole fan-out table from canonical follows + posts.

        The join is performed in the API process, NOT inside a shard:
        ``follows`` is placed by ``follower_id`` while ``posts`` is placed by
        ``user_id``, so a per-shard ``posts JOIN follows`` would silently
        miss every edge whose follower lives on another shard. See
        ``db.shard_scan`` for the full explanation.
        """
        from db.shard_scan import scan_follow_edges, scan_posts
        from sqlalchemy import text

        await self.ensure_schema()
        async with self.engine.begin() as conn:
            await conn.execute(text("TRUNCATE user_feed"))

        rows: list[dict[str, Any]] = []

        # 1. canonical (NORMALIZED mode, or canonical-only data)
        async with self.engine.begin() as conn:
            canonical = (
                await conn.execute(
                    text(
                        "SELECT f.follower_id, p.post_id, p.user_id, p.content, "
                        "p.visibility, p.created_at "
                        "FROM posts p JOIN follows f ON f.following_id = p.user_id"
                    )
                )
            ).all()
        for r in canonical:
            rows.append(
                {
                    "uid": int(r[0]),
                    "pid": int(r[1]),
                    "author": int(r[2]),
                    "content": r[3],
                    "vis": r[4] or "public",
                    "created": r[5],
                }
            )

        # 2. shards (SHARDED mode): scatter the base tables, join here
        if self.shard_manager is not None:
            edges = await scan_follow_edges(self.shard_manager)
            posts = await scan_posts(self.shard_manager)
            by_author: dict[int, list[dict[str, Any]]] = {}
            for post in posts:
                by_author.setdefault(post["user_id"], []).append(post)
            for follower_id, following_id in edges:
                for post in by_author.get(following_id, []):
                    rows.append(
                        {
                            "uid": follower_id,
                            "pid": post["post_id"],
                            "author": post["user_id"],
                            "content": post["content"],
                            "vis": post["visibility"],
                            "created": post["created_at"],
                        }
                    )

        if rows:
            # de-duplicate on the projection's primary key
            seen: set[tuple[int, int]] = set()
            unique = []
            for row in rows:
                marker = (row["uid"], row["pid"])
                if marker in seen:
                    continue
                seen.add(marker)
                unique.append(row)
            async with self.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO user_feed (user_id, post_id, user_id_author, "
                        "content, visibility, created_at, projected_at) "
                        "VALUES (:uid, :pid, :author, :content, :vis, "
                        "COALESCE(CAST(:created AS timestamptz), now()), now()) "
                        "ON CONFLICT (user_id, post_id) DO NOTHING"
                    ),
                    unique,
                )

        async with self.engine.begin() as conn:
            res = await conn.execute(text("SELECT count(*) FROM user_feed"))
            return int(res.scalar() or 0)

    def stats(self) -> dict[str, Any]:
        return {
            "table": "user_feed",
            "derived": True,
            "rows_written": self.rows_written,
            "errors": self.errors,
        }
