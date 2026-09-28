"""Repository implementations that transparently route through shards.

Writes are routed by the entity's configured shard key (users -> user_id,
posts -> user_id, comments/likes -> post_id, ...). Reads by primary key are
targeted to the single owning shard; key-list queries (global recent posts,
followers of a user whose edges are sharded by follower) use scatter-gather.

``*_traced`` methods additionally return the executor outcome (shard
contacted + latencies) for the sharding demo/API.
"""

from __future__ import annotations

from typing import Any

from common.enums import EntityName, RoutingStrategy
from metrics.collector import MetricsCollector
from models import (
    Comment,
    Follow,
    Hashtag,
    Like,
    Media,
    Notification,
    Post,
    PostHashtag,
    Profile,
    User,
)
from queries.scatter_gather import ScatterGatherExecutor, ScatterOutcome
from queries.targeted import TargetedExecutor, TargetedOutcome
from router.shard_router import ShardRouter
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import SnowflakeIdGenerator
from db.repositories.base import (
    CommentRepository,
    FollowRepository,
    HashtagRepository,
    LikeRepository,
    MediaRepository,
    NotificationRepository,
    PostRepository,
    UserRepository,
)
from db.repositories.canonical import CanonicalHashtagRepository
from db.shard_engine import ShardEngineManager


def _post_sort_key(row: Post) -> tuple[Any, ...]:
    return (row.created_at, row.post_id)


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
            user = User(user_id=uid, username=username, email=email, password_hash=password_hash)
            session.add(user)
            await session.flush()
            session.add(
                Profile(
                    profile_id=profile_id,
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
            result = await session.execute(delete(User).where(User.user_id == user_id))
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
            res = await session.execute(select(User).order_by(User.user_id).limit(limit))
            return list(res.scalars().all())

        return await self.scatter.execute(
            work,
            merge=lambda rows: sorted(rows, key=lambda u: u.user_id),
            limit=limit,
        )

    # ---------------------------------------------------------- follows
    async def add_follow(self, follower_id: int, following_id: int) -> bool:
        if follower_id == following_id:
            raise ValueError("a user cannot follow themselves")
        shard = self.router.shard_for(follower_id, strategy=self.strategy)

        async def work(session: AsyncSession) -> bool:
            existing = await session.execute(
                select(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            if existing.first() is not None:
                return False
            session.add(Follow(follower_id=follower_id, following_id=following_id))
            return True

        outcome = await self.targeted.execute(
            EntityName.FOLLOWS.value,
            follower_id,
            work,
            kind="write",
            strategy=self.strategy,
            shard_id=shard,
        )
        return bool(outcome.result)

    async def remove_follow(self, follower_id: int, following_id: int) -> bool:
        shard = self.router.shard_for(follower_id, strategy=self.strategy)

        async def work(session: AsyncSession) -> bool:
            res = await session.execute(
                delete(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            return (res.rowcount or 0) > 0

        outcome = await self.targeted.execute(
            EntityName.FOLLOWS.value,
            follower_id,
            work,
            kind="write",
            strategy=self.strategy,
            shard_id=shard,
        )
        return bool(outcome.result)

    async def list_following(self, user_id: int, limit: int = 50) -> list[Follow]:
        async def work(session: AsyncSession) -> list[Follow]:
            res = await session.execute(
                select(Follow)
                .where(Follow.follower_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            EntityName.FOLLOWS.value,
            user_id,
            work,
            kind="read",
            strategy=self.strategy,
        )
        return list(outcome.result or [])

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

        # Post ids are key-aligned: route(post.post_id) == author's owning shard.
        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        return outcome.result, outcome

    async def get_by_id(self, post_id: int) -> Post | None:
        post, _ = await self.get_by_id_traced(post_id)
        return post

    async def create_traced(
        self, *, user_id: int, content: str, visibility: str, post_id: int | None = None
    ) -> tuple[Post, TargetedOutcome]:
        author_shard = self.router.shard_for(user_id, strategy=self.strategy)
        # Align the new post id so the post stays findable BY ITS OWN id on the
        # same shard it was written to (hash(post_id) == hash(user_id)).
        pid = post_id if post_id and post_id > 0 else None
        if pid is None:
            pid = self.router.generate_key_routed_to(
                author_shard, base=self.ids.next_id(), strategy=self.strategy
            )

        async def work(session: AsyncSession) -> Post:
            vis = visibility.value if hasattr(visibility, "value") else visibility
            post = Post(post_id=pid, user_id=user_id, content=content, visibility=vis)
            session.add(post)
            await session.flush()
            await session.refresh(post)
            return post

        outcome = await self.targeted.execute(
            self.ENTITY,
            user_id,
            work,
            kind="write",
            strategy=self.strategy,
            shard_id=author_shard,
        )
        return outcome.result, outcome

    async def create(self, *, post_id: int, user_id: int, content: str, visibility: str) -> Post:
        post, _ = await self.create_traced(
            user_id=user_id, content=content, visibility=visibility, post_id=post_id
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
            result = await session.execute(delete(Post).where(Post.post_id == post_id))
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
                select(Post)
                .order_by(desc(Post.created_at), desc(Post.post_id))
                .limit(per_shard_limit)
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

    async def list_by_author(self, user_id: int, limit: int = 50) -> list[Post]:
        async def work(session: AsyncSession) -> list[Post]:
            res = await session.execute(
                select(Post)
                .where(Post.user_id == user_id)
                .order_by(desc(Post.created_at), desc(Post.post_id))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="read", strategy=self.strategy
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
                .order_by(Comment.created_at, Comment.comment_id)
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
            comment = Comment(comment_id=cid, post_id=post_id, user_id=user_id, content=content)
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


# ---------------------------------------------------------------------------
# Like / Follow / Media / Notification / Hashtag — sharded implementations
# ---------------------------------------------------------------------------


class ShardedLikeRepository(LikeRepository):
    """likes are sharded by ``post_id`` — co-located with the post."""

    ENTITY = EntityName.LIKES.value

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
        self.ids = id_generator or SnowflakeIdGenerator(slot=3)
        self.strategy = strategy

    async def add(self, *, post_id: int, user_id: int) -> Like:
        like_id = self.ids.next_id()

        async def work(session: AsyncSession) -> Like:
            existing = await session.execute(
                select(Like).where(Like.post_id == post_id, Like.user_id == user_id)
            )
            found = existing.scalars().first()
            if found is not None:
                return found
            like = Like(like_id=like_id, post_id=post_id, user_id=user_id)
            session.add(like)
            await session.flush()
            await session.refresh(like)
            return like

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="write", strategy=self.strategy
        )
        return outcome.result

    async def remove(self, *, post_id: int, user_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            res = await session.execute(
                delete(Like).where(Like.post_id == post_id, Like.user_id == user_id)
            )
            return (res.rowcount or 0) > 0

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)

    async def count_for_post(self, post_id: int) -> int:
        async def work(session: AsyncSession) -> int:
            res = await session.execute(
                select(func.count()).select_from(Like).where(Like.post_id == post_id)
            )
            return int(res.scalar() or 0)

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        return int(outcome.result or 0)

    async def list_for_post(self, post_id: int, limit: int = 100) -> list[Like]:
        async def work(session: AsyncSession) -> list[Like]:
            res = await session.execute(
                select(Like)
                .where(Like.post_id == post_id)
                .order_by(desc(Like.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        return list(outcome.result or [])

    async def has_liked(self, *, post_id: int, user_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            res = await session.execute(
                select(Like.like_id).where(Like.post_id == post_id, Like.user_id == user_id)
            )
            return res.first() is not None

        outcome = await self.targeted.execute(
            self.ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        return bool(outcome.result)


class ShardedFollowRepository(FollowRepository):
    """follows are sharded by ``follower_id``.

    Outgoing edges (``following_ids``) are a targeted read; incoming edges
    (``follower_ids``) have no single routing key and use scatter-gather —
    exactly the asymmetry the sharding module documents.
    """

    ENTITY = EntityName.FOLLOWS.value

    def __init__(
        self,
        router: ShardRouter,
        manager: ShardEngineManager,
        metrics: MetricsCollector,
        strategy: RoutingStrategy | None = None,
    ) -> None:
        self.router = router
        self.manager = manager
        self.metrics = metrics
        self.targeted = TargetedExecutor(router, manager, metrics)
        self.scatter = ScatterGatherExecutor(router, manager, metrics)
        self.strategy = strategy

    async def add_follow(self, follower_id: int, following_id: int) -> bool:
        if follower_id == following_id:
            raise ValueError("a user cannot follow themselves")

        async def work(session: AsyncSession) -> bool:
            existing = await session.execute(
                select(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            if existing.first() is not None:
                return False
            session.add(Follow(follower_id=follower_id, following_id=following_id))
            return True

        outcome = await self.targeted.execute(
            self.ENTITY, follower_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)

    async def remove_follow(self, follower_id: int, following_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            res = await session.execute(
                delete(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            return (res.rowcount or 0) > 0

        outcome = await self.targeted.execute(
            self.ENTITY, follower_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)

    async def list_following(self, user_id: int, limit: int = 100) -> list[Follow]:
        async def work(session: AsyncSession) -> list[Follow]:
            res = await session.execute(
                select(Follow)
                .where(Follow.follower_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="read", strategy=self.strategy
        )
        return list(outcome.result or [])

    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        rows = await self.list_following(user_id, limit=limit)
        return [r.following_id for r in rows]

    async def list_followers(self, user_id: int, limit: int = 100) -> list[Follow]:
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
        return list(outcome.rows)

    async def follower_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        rows = await self.list_followers(user_id, limit=limit)
        return [r.follower_id for r in rows]

    async def counts(self, user_id: int) -> tuple[int, int]:
        following = await self.following_ids(user_id)
        followers = await self.follower_ids(user_id)
        return len(followers), len(following)

    async def is_following(self, follower_id: int, following_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            res = await session.execute(
                select(Follow.following_id).where(
                    Follow.follower_id == follower_id,
                    Follow.following_id == following_id,
                )
            )
            return res.first() is not None

        outcome = await self.targeted.execute(
            self.ENTITY, follower_id, work, kind="read", strategy=self.strategy
        )
        return bool(outcome.result)


class ShardedMediaRepository(MediaRepository):
    """media metadata sharded by ``owner_id`` (co-located with the user)."""

    ENTITY = EntityName.MEDIA.value

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
        self.ids = id_generator or SnowflakeIdGenerator(slot=4)
        self.strategy = strategy

    async def create(
        self,
        *,
        owner_id: int,
        storage_key: str,
        media_type: str,
        mime_type: str,
        size_bytes: int,
        checksum_sha256: str | None = None,
        post_id: int | None = None,
        media_id: int | None = None,
    ) -> Media:
        mid = media_id or self.ids.next_id()

        async def work(session: AsyncSession) -> Media:
            media = Media(
                media_id=mid,
                owner_id=owner_id,
                post_id=post_id,
                storage_key=storage_key,
                checksum_sha256=checksum_sha256,
                media_type=media_type,
                mime_type=mime_type,
                size_bytes=size_bytes,
            )
            session.add(media)
            await session.flush()
            await session.refresh(media)
            return media

        outcome = await self.targeted.execute(
            self.ENTITY, owner_id, work, kind="write", strategy=self.strategy
        )
        return outcome.result

    async def get_by_id(self, media_id: int) -> Media | None:
        """No routing key — scatter-gather (media is addressed by owner_id)."""
        return await self.find_by_checksum_or_id(media_id=media_id)

    async def find_by_checksum(self, checksum_sha256: str) -> Media | None:
        return await self.find_by_checksum_or_id(checksum=checksum_sha256)

    async def find_by_checksum_or_id(
        self, *, media_id: int | None = None, checksum: str | None = None
    ) -> Media | None:
        async def work(session: AsyncSession) -> list[Media]:
            stmt = select(Media)
            if media_id is not None:
                stmt = stmt.where(Media.media_id == media_id)
            elif checksum is not None:
                stmt = stmt.where(Media.checksum_sha256 == checksum)
            res = await session.execute(stmt.limit(1))
            return list(res.scalars().all())

        outcome = await self.scatter.execute(work, limit=1)
        return outcome.rows[0] if outcome.rows else None

    async def list_by_owner(self, owner_id: int, limit: int = 50) -> list[Media]:
        async def work(session: AsyncSession) -> list[Media]:
            res = await session.execute(
                select(Media)
                .where(Media.owner_id == owner_id)
                .order_by(desc(Media.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, owner_id, work, kind="read", strategy=self.strategy
        )
        return list(outcome.result or [])

    async def delete(self, media_id: int) -> bool:
        media = await self.get_by_id(media_id)
        if media is None:
            return False

        async def work(session: AsyncSession) -> bool:
            res = await session.execute(delete(Media).where(Media.media_id == media_id))
            return (res.rowcount or 0) > 0

        outcome = await self.targeted.execute(
            self.ENTITY, media.owner_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)


class ShardedNotificationRepository(NotificationRepository):
    """notifications sharded by ``user_id`` (the recipient's inbox)."""

    ENTITY = EntityName.NOTIFICATIONS.value

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
        self.ids = id_generator or SnowflakeIdGenerator(slot=5)
        self.strategy = strategy

    async def create(
        self,
        *,
        user_id: int,
        type: str,
        actor_user_id: int | None = None,
        post_id: int | None = None,
    ) -> Notification:
        nid = self.ids.next_id()

        async def work(session: AsyncSession) -> Notification:
            note = Notification(
                notification_id=nid,
                user_id=user_id,
                actor_user_id=actor_user_id,
                type=type,
                post_id=post_id,
            )
            session.add(note)
            await session.flush()
            await session.refresh(note)
            return note

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="write", strategy=self.strategy
        )
        return outcome.result

    async def list_for_user(
        self, user_id: int, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]:
        async def work(session: AsyncSession) -> list[Notification]:
            stmt = select(Notification).where(Notification.user_id == user_id)
            if unread_only:
                stmt = stmt.where(Notification.is_read.is_(False))
            res = await session.execute(stmt.order_by(desc(Notification.created_at)).limit(limit))
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="read", strategy=self.strategy
        )
        return list(outcome.result or [])

    async def mark_read(self, notification_id: int, user_id: int) -> bool:
        async def work(session: AsyncSession) -> bool:
            note = await session.get(Notification, notification_id)
            if note is None or note.user_id != user_id:
                return False
            note.is_read = True
            await session.flush()
            return True

        outcome = await self.targeted.execute(
            self.ENTITY, user_id, work, kind="write", strategy=self.strategy
        )
        return bool(outcome.result)


class ShardedHashtagRepository(HashtagRepository):
    """hashtags by ``hashtag_id``; edges (post_hashtags) by ``post_id``.

    Attaching tags to a post is a write on the post's shard (edges are
    co-located with the post); the vocabulary row may live anywhere, so
    ``hashtag_id`` values are minted here and inserted idempotently.
    """

    ENTITY = EntityName.HASHTAGS.value
    EDGE_ENTITY = EntityName.POST_HASHTAGS.value

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
        self.ids = id_generator or SnowflakeIdGenerator(slot=6)
        self.strategy = strategy

    @staticmethod
    def extract_tags(content: str) -> list[str]:
        return CanonicalHashtagRepository.extract_tags(content)

    async def _ensure_tags(self, tags: list[str]) -> dict[str, int]:
        """Insert unknown tags on their hash-routed shard; return tag -> id."""
        known: dict[str, int] = {}
        for tag in tags:

            async def work(session: AsyncSession, tag: str = tag) -> Hashtag:
                res = await session.execute(select(Hashtag).where(Hashtag.tag == tag))
                found = res.scalars().first()
                if found is not None:
                    return found
                row = Hashtag(hashtag_id=self.ids.next_id(), tag=tag)
                session.add(row)
                await session.flush()
                return row

            outcome = await self.targeted.execute(
                self.ENTITY, tag, work, kind="write", strategy=self.strategy
            )
            known[tag] = outcome.result.hashtag_id
        return known

    async def tag_post(self, *, post_id: int, tags: list[str]) -> list[str]:
        if not tags:
            return []
        known = await self._ensure_tags(tags)

        async def work(session: AsyncSession) -> None:
            existing = await session.execute(
                select(PostHashtag.hashtag_id).where(PostHashtag.post_id == post_id)
            )
            already = set(existing.scalars().all())
            for _tag, hashtag_id in known.items():
                if hashtag_id in already:
                    continue
                session.add(PostHashtag(post_id=post_id, hashtag_id=hashtag_id))
            await session.flush()

        await self.targeted.execute(
            self.EDGE_ENTITY, post_id, work, kind="write", strategy=self.strategy
        )
        return list(known)

    async def tags_for_post(self, post_id: int) -> list[str]:
        async def work(session: AsyncSession) -> list[int]:
            res = await session.execute(
                select(PostHashtag.hashtag_id).where(PostHashtag.post_id == post_id)
            )
            return list(res.scalars().all())

        outcome = await self.targeted.execute(
            self.EDGE_ENTITY, post_id, work, kind="read", strategy=self.strategy
        )
        ids = list(outcome.result or [])
        if not ids:
            return []

        # Resolve tag strings: scatter-gather over the vocabulary shards.
        async def resolve(session: AsyncSession) -> list[str]:
            res = await session.execute(select(Hashtag.tag).where(Hashtag.hashtag_id.in_(ids)))
            return list(res.scalars().all())

        resolved = await self.scatter.execute(resolve, limit=len(ids))
        return sorted(set(resolved.rows))

    async def posts_for_tag(self, tag: str, limit: int = 50) -> list[int]:
        async def find_tag(session: AsyncSession) -> list[int]:
            res = await session.execute(
                select(Hashtag.hashtag_id).where(Hashtag.tag == tag.lower()).limit(1)
            )
            return list(res.scalars().all())

        found = await self.scatter.execute(find_tag, limit=1)
        if not found.rows:
            return []
        hashtag_id = found.rows[0]

        async def edges(session: AsyncSession) -> list[int]:
            res = await session.execute(
                select(PostHashtag.post_id)
                .where(PostHashtag.hashtag_id == hashtag_id)
                .order_by(desc(PostHashtag.post_id))
                .limit(limit)
            )
            return list(res.scalars().all())

        outcome = await self.scatter.execute(
            edges, merge=lambda rows: sorted(rows, reverse=True), limit=limit
        )
        return list(outcome.rows)
