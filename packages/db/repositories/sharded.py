"""Repository implementations that transparently route through shards.

Writes are routed by the entity's configured shard key (users -> id,
posts -> author_id, comments/likes -> post_id, ...). Reads by primary key are
targeted to the single owning shard; key-list queries (global recent posts,
followers of a user whose edges are sharded by follower) use scatter-gather.

``*_traced`` methods additionally return the executor outcome (shard
contacted + latencies) for the sharding demo/API.
"""

from __future__ import annotations

from typing import Any

from common.enums import EntityName, RoutingStrategy
from metrics.collector import MetricsCollector
from models import Comment, Follow, Post, Profile, User
from queries.scatter_gather import ScatterGatherExecutor, ScatterOutcome
from queries.targeted import TargetedExecutor, TargetedOutcome
from router.shard_router import ShardRouter
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import SnowflakeIdGenerator
from db.repositories.base import CommentRepository, PostRepository, UserRepository
from db.shard_engine import ShardEngineManager


def _post_sort_key(row: Post) -> tuple[Any, ...]:
    return (row.created_at, row.id)


class ShardedUserRepository(UserRepository):
    ENTITY = EntityName.USERS.value

    def __init__(
        self,
        router: ShardRouter,
        manager: ShardEngineManager,
        metrics: MetricsCollector,
        id_generator: SnowflakeIdGenerator | None = None,
        strategy: RoutingStrategy | None = None,
    ) -> None:
        self.router = router
        self.manager = manager
        self.metrics = metrics
        self.targeted = TargetedExecutor(router, manager, metrics)
        self.scatter = ScatterGatherExecutor(router, manager, metrics)
        self.ids = id_generator or SnowflakeIdGenerator(slot=0)
        self.strategy = strategy

    # ------------------------------------------------------------ CRUD
    async def get_by_id_traced(self, user_id: int) -> tuple[User | None, TargetedOutcome]:
        async def work(session: AsyncSession) -> User | None:
            return await session.get(User, user_id)

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="read", strategy=self.strategy
        )
        return outcome.result, outcome

    async def get_by_id(self, user_id: int) -> User | None:
        user, _ = await self.get_by_id_traced(user_id)
        return user

    async def create_traced(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        bio: str | None = None,
        user_id: int | None = None,
    ) -> tuple[User, TargetedOutcome]:
        uid = user_id or self.ids.next_id()
        shard = self.router.shard_for(uid, strategy=self.strategy)
        profile_id = self.ids.next_id()

        async def work(session: AsyncSession) -> User:
            user = User(id=uid, username=username, email=email, password_hash=password_hash)
            session.add(user)
            await session.flush()
            session.add(
                Profile(
                    id=profile_id,
                    user_id=uid,
                    display_name=display_name or username,
                    bio=bio,
                )
            )
            await session.flush()
            await session.refresh(user)
            return user

        outcome = await self.targeted.execute(
            self.ENTITY,
            uid,
            work,
            kind="write",
            strategy=self.strategy,
            shard_id=shard,
        )
        return outcome.result, outcome

    async def create(
        self,
        *,
        user_id: int,
        username: str,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        bio: str | None = None,
    ) -> User:
        user, _ = await self.create_traced(
            username=username,
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            bio=bio,
            user_id=user_id,
        )
        return user

    async def update(self, user_id: int, **fields: Any) -> User:
        allowed = {"username", "email", "password_hash"}

        async def work(session: AsyncSession) -> User:
            user = await session.get(User, user_id)
            if user is None:
                raise LookupError(f"user {user_id} not found")
            for k, v in fields.items():
                if k in allowed and v is not None:
                    setattr(user, k, v)
            await session.flush()
            await session.refresh(user)
            return user

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="write", strategy=self.strategy
        )
        return outcome.result

    async def delete(self, user_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            result = await session.execute(delete(User).where(User.id == user_id))
            return (result.rowcount or 0) > 0

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        outcome = await self._scatter_users(limit=limit + offset)
        return outcome.rows[offset : offset + limit]

    async def _scatter_users(self, limit: int = 50) -> ScatterOutcome:
        async def work(session: AsyncSession) -> list[User]:
            res = await session.execute(select(User).order_by(User.id).limit(limit))
            return list(res.scalars().all())

        return await self.scatter.execute(
            work,
            merge=lambda rows: sorted(rows, key=lambda u: u.id),
            limit=limit,
        )

    # ---------------------------------------------------------- follows
    async def add_follow(self, follower_id: int, following_id: int) -> None:
        if follower_id == following_id:
            raise ValueError("a user cannot follow themselves")
        shard = self.router.shard_for(follower_id, strategy=self.strategy)

        async def work(session: AsyncSession) -> None:
            existing = await session.execute(
                select(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            if existing.first() is None:
                session.add(Follow(follower_id=follower_id, following_id=following_id))

        await self.targeted.execute(
            EntityName.FOLLOWS.value,
            follower_id,
            work,
            kind="write",
            strategy=self.strategy,
            shard_id=shard,
        )

    async def list_followers_traced(
        self, user_id: int, limit: int = 50
    ) -> tuple[list[Follow], ScatterOutcome]:
        # Edges are sharded by follower_id; finding followers OF a user has no
        # single routing key -> scatter-gather on the secondary index column.
        async def work(session: AsyncSession) -> list[Follow]:
            res = await session.execute(
                select(Follow)
                .where(Follow.following_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.scatter.execute(
            work,
            merge=lambda rows: sorted(rows, key=lambda f: f.created_at, reverse=True),
            limit=limit,
        )
        return outcome.rows, outcome

    async def list_followers(self, user_id: int, limit: int = 50) -> list[Follow]:
        rows, _ = await self.list_followers_traced(user_id, limit)
        return rows


class ShardedPostRepository(PostRepository):
    ENTITY = EntityName.POSTS.value

    def __init__(
        self,
        router: ShardRouter,
        manager: ShardEngineManager,
        metrics: MetricsCollector,
        id_generator: SnowflakeIdGenerator | None = None,
        strategy: RoutingStrategy | None = None,
    ) -> None:
        self.router = router
        self.manager = manager
        self.metrics = metrics
        self.targeted = TargetedExecutor(router, manager, metrics)
        self.scatter = ScatterGatherExecutor(router, manager, metrics)
        self.ids = id_generator or SnowflakeIdGenerator(slot=1)
        self.strategy = strategy

    async def get_by_id_traced(self, post_id: int) -> tuple[Post | None, TargetedOutcome]:
        async def work(session: AsyncSession) -> Post | None:
            return await session.get(Post, post_id)

        # Post ids are key-aligned: route(post.id) == author's owning shard.
        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        return outcome.result, outcome

    async def get_by_id(self, post_id: int) -> Post | None:
        post, _ = await self.get_by_id_traced(post_id)
        return post

    async def create_traced(
        self, *, author_id: int, content: str, visibility: str, post_id: int | None = None
    ) -> tuple[Post, TargetedOutcome]:
        author_shard = self.router.shard_for(author_id, strategy=self.strategy)
        # Align the new post id so the post stays findable BY ITS OWN id on the
        # same shard it was written to (hash(post_id) == hash(author_id)).
        pid = post_id if post_id and post_id > 0 else None
        if pid is None:
            pid = self.router.generate_key_routed_to(
                author_shard, base=self.ids.next_id(), strategy=self.strategy
            )

        async def work(session: AsyncSession) -> Post:
            vis = visibility.value if hasattr(visibility, "value") else visibility
            post = Post(id=pid, author_id=author_id, content=content, visibility=vis)
            session.add(post)
            await session.flush()
            await session.refresh(post)
            return post

        outcome = await self.targeted.execute(
            self.ENTITY,
            author_id,
            work,
            kind="write",
            strategy=self.strategy,
            shard_id=author_shard,
        )
        return outcome.result, outcome

    async def create(self, *, post_id: int, author_id: int, content: str, visibility: str) -> Post:
        post, _ = await self.create_traced(
            author_id=author_id, content=content, visibility=visibility, post_id=post_id
        )
        return post

    async def update(self, post_id: int, **fields: Any) -> Post:
        allowed = {"content", "visibility"}

        async def work(session: AsyncSession) -> Post:
            post = await session.get(Post, post_id)
            if post is None:
                raise LookupError(f"post {post_id} not found")
            for k, v in fields.items():
                if k in allowed and v is not None:
                    setattr(post, k, v.value if hasattr(v, "value") else v)
            await session.flush()
            await session.refresh(post)
            return post

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="write", strategy=self.strategy
        )
        return outcome.result

    async def delete(self, post_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            result = await session.execute(delete(Post).where(Post.id == post_id))
            return (result.rowcount or 0) > 0

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)

    async def list_recent_traced(self, limit: int = 50) -> tuple[list[Post], ScatterOutcome]:
        # Per-shard top-N, then global merge/sort/limit — classic scatter-gather.
        per_shard_limit = limit

        async def work(session: AsyncSession) -> list[Post]:
            res = await session.execute(
                select(Post).order_by(desc(Post.created_at), desc(Post.id)).limit(per_shard_limit)
            )
            return list(res.scalars().all())

        outcome = await self.scatter.execute(
            work,
            merge=lambda rows: sorted(rows, key=_post_sort_key, reverse=True),
            limit=limit,
        )
        return outcome.rows, outcome

    async def list_recent(self, limit: int = 50) -> list[Post]:
        rows, _ = await self.list_recent_traced(limit)
        return rows

    async def list_by_author(self, author_id: int, limit: int = 50) -> list[Post]:
        async def work(session: AsyncSession) -> list[Post]:
            res = await session.execute(
                select(Post)
                .where(Post.author_id == author_id)
                .order_by(desc(Post.created_at), desc(Post.id))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, author_id, work, kind="read", strategy=self.strategy
        )
        return outcome.result


class ShardedCommentRepository(CommentRepository):
    ENTITY = EntityName.COMMENTS.value

    def __init__(
        self,
        router: ShardRouter,
        manager: ShardEngineManager,
        metrics: MetricsCollector,
        id_generator: SnowflakeIdGenerator | None = None,
        strategy: RoutingStrategy | None = None,
    ) -> None:
        self.router = router
        self.manager = manager
        self.metrics = metrics
        self.targeted = TargetedExecutor(router, manager, metrics)
        self.ids = id_generator or SnowflakeIdGenerator(slot=2)
        self.strategy = strategy

    async def list_for_post_traced(
        self, post_id: int, limit: int = 50
    ) -> tuple[list[Comment], TargetedOutcome]:
        async def work(session: AsyncSession) -> list[Comment]:
            res = await session.execute(
                select(Comment)
                .where(Comment.post_id == post_id)
                .order_by(Comment.created_at, Comment.id)
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        return outcome.result, outcome

    async def list_for_post(self, post_id: int, limit: int = 50) -> list[Comment]:
        rows, _ = await self.list_for_post_traced(post_id, limit)
        return rows

    async def create_traced(
        self, *, post_id: int, user_id: int, content: str
    ) -> tuple[Comment, TargetedOutcome]:
        cid = self.ids.next_id()

        async def work(session: AsyncSession) -> Comment:
            comment = Comment(id=cid, post_id=post_id, user_id=user_id, content=content)
            session.add(comment)
            await session.flush()
            await session.refresh(comment)
            return comment

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="write", strategy=self.strategy
        )
        return outcome.result, outcome

    async def create(self, *, post_id: int, user_id: int, content: str) -> Comment:
        comment, _ = await self.create_traced(post_id=post_id, user_id=user_id, content=content)
        return comment
