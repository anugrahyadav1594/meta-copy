from __future__ import annotations

from models import Follow, Post
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from db.session import session_scope


class PullFeedRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    # -------------------------
    # PULL MODEL
    # -------------------------
    async def get_feed(self, user_id: int, limit: int = 20) -> list[Post]:
        async with session_scope(self.engine, readonly=True) as session:
            followed_users = (
                select(Follow.following_id)
                .where(Follow.follower_id == user_id)
            )

            result = await session.execute(
                select(Post)
                .where(Post.author_id.in_(followed_users))
                .order_by(desc(Post.created_at), desc(Post.id))
                .limit(limit)
            )

            return list(result.scalars().all())

    # -------------------------
    # PUSH MODEL
    # -------------------------
    async def ensure_push_table(self) -> None:
        async with session_scope(self.engine) as session:
            await session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS user_feed (
                        user_id BIGINT NOT NULL,
                        post_id BIGINT NOT NULL,
                        author_id BIGINT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (user_id, post_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_user_feed_user_created
                    ON user_feed (user_id, created_at DESC);
                    """
                )
            )

    async def fanout_post(self, post: Post) -> None:
        await self.ensure_push_table()

        async with session_scope(self.engine) as session:
            followers = await session.execute(
                select(Follow.follower_id)
                .where(Follow.following_id == post.author_id)
            )

            follower_ids = followers.scalars().all()

            for follower_id in follower_ids:
                await session.execute(
                    text(
                        """
                        INSERT INTO user_feed
                            (user_id, post_id, author_id, content, created_at)
                        VALUES
                            (:user_id, :post_id, :author_id, :content, :created_at)
                        ON CONFLICT (user_id, post_id) DO NOTHING
                        """
                    ),
                    {
                        "user_id": follower_id,
                        "post_id": post.id,
                        "author_id": post.author_id,
                        "content": post.content,
                        "created_at": post.created_at,
                    },
                )

    async def get_push_feed(
        self,
        user_id: int,
        limit: int = 20,
    ) -> list[dict]:
        await self.ensure_push_table()

        async with session_scope(self.engine, readonly=True) as session:
            result = await session.execute(
                text(
                    """
                    SELECT post_id, author_id, content, created_at
                    FROM user_feed
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC, post_id DESC
                    LIMIT :limit
                    """
                ),
                {
                    "user_id": user_id,
                    "limit": limit,
                },
            )

            return [dict(row) for row in result.mappings().all()]