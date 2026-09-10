"""Repository implementations against the single canonical PostgreSQL.

NORMALIZED mode: straightforward ORM access, local foreign keys and JOINs all
work because every row lives in one database.
"""

from __future__ import annotations

from typing import Any

from models import Comment, Follow, Like, Media, Notification, Post, Profile, User
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from db.repositories.base import CommentRepository, PostRepository, UserRepository
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
                id=user_id if user_id and user_id > 0 else None,
                username=username,
                email=email,
                password_hash=password_hash,
            )
            s.add(user)
            await s.flush()  # populate server-generated user.id
            profile = Profile(
                user_id=user.id,
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
            result = await s.execute(delete(User).where(User.id == user_id))
            return (result.rowcount or 0) > 0

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(select(User).order_by(User.id).limit(limit).offset(offset))
            return list(res.scalars().all())

    async def add_follow(self, follower_id: int, following_id: int) -> None:
        async with session_scope(self._engine) as s:
            exists = await s.execute(
                select(Follow).where(
                    Follow.follower_id == follower_id, Follow.following_id == following_id
                )
            )
            if exists.first() is None:
                s.add(Follow(follower_id=follower_id, following_id=following_id))

    async def list_followers(self, user_id: int, limit: int = 50) -> list[Follow]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Follow)
                .where(Follow.following_id == user_id)
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

    async def create(self, *, post_id: int, author_id: int, content: str, visibility: str) -> Post:
        async with session_scope(self._engine) as s:
            post = Post(
                id=post_id if post_id and post_id > 0 else None,
                author_id=author_id,
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
            result = await s.execute(delete(Post).where(Post.id == post_id))
            return (result.rowcount or 0) > 0

    async def list_recent(self, limit: int = 50) -> list[Post]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Post).order_by(desc(Post.created_at), desc(Post.id)).limit(limit)
            )
            return list(res.scalars().all())

    async def list_by_author(self, author_id: int, limit: int = 50) -> list[Post]:
        async with session_scope(self._engine, readonly=True) as s:
            res = await s.execute(
                select(Post)
                .where(Post.author_id == author_id)
                .order_by(desc(Post.created_at), desc(Post.id))
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
                .order_by(Comment.created_at, Comment.id)
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


# Imported here so the module also acts as a convenient canonical aggregate.
__all__ = [
    "CanonicalUserRepository",
    "CanonicalPostRepository",
    "CanonicalCommentRepository",
    "Like",
    "Media",
    "Notification",
    "func",
]
