"""Repository implementations against the single canonical PostgreSQL.

NORMALIZED mode: straightforward ORM access, local foreign keys and JOINs all
work because every row lives in one database.
"""

from __future__ import annotations

from typing import Any

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
from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

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
from db.session import session_scope


class CanonicalUserRepository(UserRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_by_id(self, user_id: int) -> User | None:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.get(User, user_id)
            return res

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
        async with session_scope(self._engine) as s:
            user = User(
                user_id=user_id if user_id and user_id > 0 else None,
                username=username,
                email=email,
                password_hash=password_hash,
            )
            s.add(user)
            await s.flush()  # populate server-generated user.user_id
            profile = Profile(
                user_id=user.user_id,
                display_name=display_name or username,
                bio=bio,
            )
            s.add(profile)
            await s.flush()
            await s.refresh(user)
            return user

    async def update(self, user_id: int, **fields: Any) -> User:
        allowed = {"username", "email", "password_hash"}
        async with session_scope(self._engine) as s:
            user = await s.get(User, user_id)
            if user is None:
                raise LookupError(f"user {user_id} not found")
            for k, v in fields.items():
                if k in allowed and v is not None:
                    setattr(user, k, v)
            await s.flush()
            await s.refresh(user)
            return user

    async def delete(self, user_id: int) -> bool:
        async with session_scope(self._engine) as s:
            result = await s.execute(delete(User).where(User.user_id == user_id))
            return (result.rowcount or 0) > 0

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(select(User).order_by(User.user_id).limit(limit).offset(offset))
            return list(res.scalars().all())

    async def add_follow(self, follower_id: int, following_id: int) -> bool:
        if follower_id == following_id:
            raise ValueError("a user cannot follow themselves")
        async with session_scope(self._engine) as s:
            stmt = (
                pg_insert(Follow)
                .values(follower_id=follower_id, following_id=following_id)
                .on_conflict_do_nothing(constraint="pk_follows")
            )
            res = await s.execute(stmt)
            return (res.rowcount or 0) > 0

    async def remove_follow(self, follower_id: int, following_id: int) -> bool:
        async with session_scope(self._engine) as s:
            res = await s.execute(
                delete(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            return (res.rowcount or 0) > 0

    async def list_followers(self, user_id: int, limit: int = 50) -> list[Follow]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow)
                .where(Follow.following_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

    async def list_following(self, user_id: int, limit: int = 50) -> list[Follow]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow)
                .where(Follow.follower_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())


class CanonicalPostRepository(PostRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_by_id(self, post_id: int) -> Post | None:
        async with session_scope(self._engine, readonly=True) as s:
            return await s.get(Post, post_id)

    async def create(self, *, post_id: int, user_id: int, content: str, visibility: str) -> Post:
        async with session_scope(self._engine) as s:
            post = Post(
                post_id=post_id if post_id and post_id > 0 else None,
                user_id=user_id,
                content=content,
                visibility=visibility,
            )
            s.add(post)
            await s.flush()
            await s.refresh(post)
            return post

    async def update(self, post_id: int, **fields: Any) -> Post:
        allowed = {"content", "visibility"}
        async with session_scope(self._engine) as s:
            post = await s.get(Post, post_id)
            if post is None:
                raise LookupError(f"post {post_id} not found")
            for k, v in fields.items():
                if k in allowed and v is not None:
                    setattr(post, k, v.value if hasattr(v, "value") else v)
            await s.flush()
            await s.refresh(post)
            return post

    async def delete(self, post_id: int) -> bool:
        async with session_scope(self._engine) as s:
            result = await s.execute(delete(Post).where(Post.post_id == post_id))
            return (result.rowcount or 0) > 0

    async def list_recent(self, limit: int = 50) -> list[Post]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Post).order_by(desc(Post.created_at), desc(Post.post_id)).limit(limit)
            )
            return list(res.scalars().all())

    async def list_by_author(self, user_id: int, limit: int = 50) -> list[Post]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Post)
                .where(Post.user_id == user_id)
                .order_by(desc(Post.created_at), desc(Post.post_id))
                .limit(limit)
            )
            return list(res.scalars().all())


class CanonicalCommentRepository(CommentRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_for_post(self, post_id: int, limit: int = 50) -> list[Comment]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Comment)
                .where(Comment.post_id == post_id)
                .order_by(Comment.created_at, Comment.comment_id)
                .limit(limit)
            )
            return list(res.scalars().all())

    async def create(self, *, post_id: int, user_id: int, content: str) -> Comment:
        async with session_scope(self._engine) as s:
            comment = Comment(post_id=post_id, user_id=user_id, content=content)
            s.add(comment)
            await s.flush()
            await s.refresh(comment)
            return comment


class CanonicalLikeRepository(LikeRepository):
    """likes in one PostgreSQL instance (unique constraint enforces idempotency)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def add(self, *, post_id: int, user_id: int) -> Like:
        async with session_scope(self._engine) as s:
            stmt = (
                pg_insert(Like)
                .values(post_id=post_id, user_id=user_id)
                .on_conflict_do_nothing(constraint="uq_likes_post_user")
                .returning(Like)
            )
            res = await s.execute(stmt)
            row = res.scalars().first()
            if row is None:  # already liked — return the existing edge
                res = await s.execute(
                    select(Like).where(Like.post_id == post_id, Like.user_id == user_id)
                )
                row = res.scalars().first()
            return row

    async def remove(self, *, post_id: int, user_id: int) -> bool:
        async with session_scope(self._engine) as s:
            res = await s.execute(
                delete(Like).where(Like.post_id == post_id, Like.user_id == user_id)
            )
            return (res.rowcount or 0) > 0

    async def count_for_post(self, post_id: int) -> int:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(func.count()).select_from(Like).where(Like.post_id == post_id)
            )
            return int(res.scalar() or 0)

    async def list_for_post(self, post_id: int, limit: int = 100) -> list[Like]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Like)
                .where(Like.post_id == post_id)
                .order_by(desc(Like.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

    async def has_liked(self, *, post_id: int, user_id: int) -> bool:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Like.like_id).where(Like.post_id == post_id, Like.user_id == user_id)
            )
            return res.first() is not None


class CanonicalFollowRepository(FollowRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def add_follow(self, follower_id: int, following_id: int) -> bool:
        if follower_id == following_id:
            raise ValueError("a user cannot follow themselves")
        async with session_scope(self._engine) as s:
            stmt = (
                pg_insert(Follow)
                .values(follower_id=follower_id, following_id=following_id)
                .on_conflict_do_nothing(constraint="pk_follows")
            )
            res = await s.execute(stmt)
            return (res.rowcount or 0) > 0

    async def remove_follow(self, follower_id: int, following_id: int) -> bool:
        async with session_scope(self._engine) as s:
            res = await s.execute(
                delete(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            return (res.rowcount or 0) > 0

    async def list_followers(self, user_id: int, limit: int = 100) -> list[Follow]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow)
                .where(Follow.following_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

    async def list_following(self, user_id: int, limit: int = 100) -> list[Follow]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow)
                .where(Follow.follower_id == user_id)
                .order_by(desc(Follow.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

    async def follower_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow.follower_id).where(Follow.following_id == user_id).limit(limit)
            )
            return list(res.scalars().all())

    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow.following_id).where(Follow.follower_id == user_id).limit(limit)
            )
            return list(res.scalars().all())

    async def counts(self, user_id: int) -> tuple[int, int]:
        async with session_scope(self._engine, readonly=True) as s:
            followers = await s.execute(
                select(func.count()).select_from(Follow).where(Follow.following_id == user_id)
            )
            following = await s.execute(
                select(func.count()).select_from(Follow).where(Follow.follower_id == user_id)
            )
            return int(followers.scalar() or 0), int(following.scalar() or 0)

    async def is_following(self, follower_id: int, following_id: int) -> bool:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow.following_id).where(
                    Follow.follower_id == follower_id,
                    Follow.following_id == following_id,
                )
            )
            return res.first() is not None


class CanonicalMediaRepository(MediaRepository):
    """Media *metadata*; the bytes live in a MediaBlobStore (Member 8)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

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
        async with session_scope(self._engine) as s:
            media = Media(
                media_id=media_id if media_id else None,
                owner_id=owner_id,
                post_id=post_id,
                storage_key=storage_key,
                checksum_sha256=checksum_sha256,
                media_type=media_type,
                mime_type=mime_type,
                size_bytes=size_bytes,
            )
            s.add(media)
            await s.flush()
            await s.refresh(media)
            return media

    async def get_by_id(self, media_id: int) -> Media | None:
        async with session_scope(self._engine, readonly=True) as s:
            return await s.get(Media, media_id)

    async def find_by_checksum(self, checksum_sha256: str) -> Media | None:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Media)
                .where(Media.checksum_sha256 == checksum_sha256)
                .order_by(Media.media_id)
                .limit(1)
            )
            return res.scalars().first()

    async def list_by_owner(self, owner_id: int, limit: int = 50) -> list[Media]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Media)
                .where(Media.owner_id == owner_id)
                .order_by(desc(Media.created_at))
                .limit(limit)
            )
            return list(res.scalars().all())

    async def delete(self, media_id: int) -> bool:
        async with session_scope(self._engine) as s:
            res = await s.execute(delete(Media).where(Media.media_id == media_id))
            return (res.rowcount or 0) > 0


class CanonicalNotificationRepository(NotificationRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self,
        *,
        user_id: int,
        type: str,
        actor_user_id: int | None = None,
        post_id: int | None = None,
    ) -> Notification:
        async with session_scope(self._engine) as s:
            note = Notification(
                user_id=user_id,
                actor_user_id=actor_user_id,
                type=type,
                post_id=post_id,
            )
            s.add(note)
            await s.flush()
            await s.refresh(note)
            return note

    async def list_for_user(
        self, user_id: int, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]:
        async with session_scope(self._engine, readonly=True) as s:
            stmt = select(Notification).where(Notification.user_id == user_id)
            if unread_only:
                stmt = stmt.where(Notification.is_read.is_(False))
            res = await s.execute(stmt.order_by(desc(Notification.created_at)).limit(limit))
            return list(res.scalars().all())

    async def mark_read(self, notification_id: int, user_id: int) -> bool:
        async with session_scope(self._engine) as s:
            note = await s.get(Notification, notification_id)
            if note is None or note.user_id != user_id:
                return False
            note.is_read = True
            await s.flush()
            return True


class CanonicalHashtagRepository(HashtagRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @staticmethod
    def extract_tags(content: str) -> list[str]:
        import re

        return sorted(
            {m.group(1).lower() for m in re.finditer(r"#([A-Za-z0-9_]{2,50})", content or "")}
        )

    async def tag_post(self, *, post_id: int, tags: list[str]) -> list[str]:
        if not tags:
            return []
        async with session_scope(self._engine) as s:
            # upsert the vocabulary (3NF: the tag string lives in one row)
            stmt = (
                pg_insert(Hashtag)
                .values([{"tag": tag} for tag in tags])
                .on_conflict_do_nothing(index_elements=["tag"])
                .returning(Hashtag.hashtag_id, Hashtag.tag)
            )
            rows = (await s.execute(stmt)).all()
            known = {tag: hid for hid, tag in rows}
            if len(known) < len(tags):
                res = await s.execute(
                    select(Hashtag.hashtag_id, Hashtag.tag).where(Hashtag.tag.in_(tags))
                )
                known = {tag: hid for hid, tag in res.all()}
            await s.execute(
                pg_insert(PostHashtag)
                .values(
                    [{"post_id": post_id, "hashtag_id": known[tag]} for tag in tags if tag in known]
                )
                .on_conflict_do_nothing(constraint="pk_post_hashtags")
            )
            return [tag for tag in tags if tag in known]

    async def tags_for_post(self, post_id: int) -> list[str]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Hashtag.tag)
                .join(PostHashtag, PostHashtag.hashtag_id == Hashtag.hashtag_id)
                .where(PostHashtag.post_id == post_id)
                .order_by(Hashtag.tag)
            )
            return list(res.scalars().all())

    async def posts_for_tag(self, tag: str, limit: int = 50) -> list[int]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(PostHashtag.post_id)
                .join(Hashtag, Hashtag.hashtag_id == PostHashtag.hashtag_id)
                .where(Hashtag.tag == tag.lower())
                .order_by(desc(PostHashtag.post_id))
                .limit(limit)
            )
            return list(res.scalars().all())


# Imported here so the module also acts as a convenient canonical aggregate.
__all__ = [
    "CanonicalUserRepository",
    "CanonicalPostRepository",
    "CanonicalCommentRepository",
    "CanonicalLikeRepository",
    "CanonicalFollowRepository",
    "CanonicalMediaRepository",
    "CanonicalNotificationRepository",
    "CanonicalHashtagRepository",
    "Like",
    "Media",
    "Notification",
    "func",
]
