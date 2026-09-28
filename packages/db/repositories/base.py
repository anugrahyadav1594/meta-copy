"""Abstract repository interfaces (the seam every module plugs into).

The API and services call these methods only. They do not know whether the
implementation talks to one PostgreSQL instance, a set of shards, (future)
replicas, or sits behind a (future) Redis cache-aside wrapper.
"""

from __future__ import annotations

import abc
from typing import Any


class UserRepository(abc.ABC):
    @abc.abstractmethod
    async def get_by_id(self, user_id: int) -> Any: ...

    @abc.abstractmethod
    async def create(
        self,
        *,
        user_id: int,
        username: str,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        bio: str | None = None,
    ) -> Any: ...

    @abc.abstractmethod
    async def update(self, user_id: int, **fields: Any) -> Any: ...

    @abc.abstractmethod
    async def delete(self, user_id: int) -> bool: ...

    @abc.abstractmethod
    async def list_users(self, limit: int = 50, offset: int = 0) -> list[Any]: ...

    # follows --------------------------------------------------------------
    @abc.abstractmethod
    async def add_follow(self, follower_id: int, following_id: int) -> bool: ...

    @abc.abstractmethod
    async def remove_follow(self, follower_id: int, following_id: int) -> bool: ...

    @abc.abstractmethod
    async def list_followers(self, user_id: int, limit: int = 50) -> list[Any]: ...

    @abc.abstractmethod
    async def list_following(self, user_id: int, limit: int = 50) -> list[Any]: ...


class CommentRepository(abc.ABC):
    @abc.abstractmethod
    async def list_for_post(self, post_id: int, limit: int = 50) -> list[Any]: ...

    @abc.abstractmethod
    async def create(self, *, post_id: int, user_id: int, content: str) -> Any: ...


class PostRepository(abc.ABC):
    @abc.abstractmethod
    async def get_by_id(self, post_id: int) -> Any: ...

    @abc.abstractmethod
    async def create(self, *, post_id: int, user_id: int, content: str, visibility: str) -> Any: ...

    @abc.abstractmethod
    async def update(self, post_id: int, **fields: Any) -> Any: ...

    @abc.abstractmethod
    async def delete(self, post_id: int) -> bool: ...

    @abc.abstractmethod
    async def list_recent(self, limit: int = 50) -> list[Any]: ...

    @abc.abstractmethod
    async def list_by_author(self, user_id: int, limit: int = 50) -> list[Any]: ...


class LikeRepository(abc.ABC):
    """likes — idempotent (user_id, post_id) edges."""

    @abc.abstractmethod
    async def add(self, *, post_id: int, user_id: int) -> Any:
        """Create the like if absent; return the row (never duplicates)."""

    @abc.abstractmethod
    async def remove(self, *, post_id: int, user_id: int) -> bool: ...

    @abc.abstractmethod
    async def count_for_post(self, post_id: int) -> int: ...

    @abc.abstractmethod
    async def list_for_post(self, post_id: int, limit: int = 100) -> list[Any]: ...

    @abc.abstractmethod
    async def has_liked(self, *, post_id: int, user_id: int) -> bool: ...


class FollowRepository(abc.ABC):
    """follows — the social graph edges (canonical, PostgreSQL)."""

    @abc.abstractmethod
    async def add_follow(self, follower_id: int, following_id: int) -> bool:
        """Return True when a new edge was created, False when it existed."""

    @abc.abstractmethod
    async def remove_follow(self, follower_id: int, following_id: int) -> bool: ...

    @abc.abstractmethod
    async def list_followers(self, user_id: int, limit: int = 100) -> list[Any]: ...

    @abc.abstractmethod
    async def list_following(self, user_id: int, limit: int = 100) -> list[Any]: ...

    @abc.abstractmethod
    async def follower_ids(self, user_id: int, limit: int = 5_000) -> list[int]: ...

    @abc.abstractmethod
    async def following_ids(self, user_id: int, limit: int = 5_000) -> list[int]: ...

    @abc.abstractmethod
    async def counts(self, user_id: int) -> tuple[int, int]:
        """``(followers, following)``."""

    @abc.abstractmethod
    async def is_following(self, follower_id: int, following_id: int) -> bool: ...


class MediaRepository(abc.ABC):
    """media — metadata only; blobs live in object storage (Member 8)."""

    @abc.abstractmethod
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
    ) -> Any: ...

    @abc.abstractmethod
    async def get_by_id(self, media_id: int) -> Any: ...

    @abc.abstractmethod
    async def find_by_checksum(self, checksum_sha256: str) -> Any:
        """Content-addressable de-duplication lookup."""

    @abc.abstractmethod
    async def list_by_owner(self, owner_id: int, limit: int = 50) -> list[Any]: ...

    @abc.abstractmethod
    async def delete(self, media_id: int) -> bool: ...


class NotificationRepository(abc.ABC):
    @abc.abstractmethod
    async def create(
        self,
        *,
        user_id: int,
        type: str,
        actor_user_id: int | None = None,
        post_id: int | None = None,
    ) -> Any: ...

    @abc.abstractmethod
    async def list_for_user(
        self, user_id: int, limit: int = 50, unread_only: bool = False
    ) -> list[Any]: ...

    @abc.abstractmethod
    async def mark_read(self, notification_id: int, user_id: int) -> bool: ...


class HashtagRepository(abc.ABC):
    """hashtags / post_hashtags (3NF tag vocabulary)."""

    @abc.abstractmethod
    async def tag_post(self, *, post_id: int, tags: list[str]) -> list[str]:
        """Attach ``tags`` to ``post_id``; returns the tags now on the post."""

    @abc.abstractmethod
    async def tags_for_post(self, post_id: int) -> list[str]: ...

    @abc.abstractmethod
    async def posts_for_tag(self, tag: str, limit: int = 50) -> list[int]:
        """Return post ids carrying ``tag`` (used by search + feed)."""
