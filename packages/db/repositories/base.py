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
    async def add_follow(self, follower_id: int, following_id: int) -> None: ...

    @abc.abstractmethod
    async def list_followers(self, user_id: int, limit: int = 50) -> list[Any]: ...


class CommentRepository(abc.ABC):
    @abc.abstractmethod
    async def list_for_post(self, post_id: int, limit: int = 50) -> list[Any]: ...

    @abc.abstractmethod
    async def create(self, *, post_id: int, user_id: int, content: str) -> Any: ...


class PostRepository(abc.ABC):
    @abc.abstractmethod
    async def get_by_id(self, post_id: int) -> Any: ...

    @abc.abstractmethod
    async def create(
        self, *, post_id: int, author_id: int, content: str, visibility: str
    ) -> Any: ...

    @abc.abstractmethod
    async def update(self, post_id: int, **fields: Any) -> Any: ...

    @abc.abstractmethod
    async def delete(self, post_id: int) -> bool: ...

    @abc.abstractmethod
    async def list_recent(self, limit: int = 50) -> list[Any]: ...

    @abc.abstractmethod
    async def list_by_author(self, author_id: int, limit: int = 50) -> list[Any]: ...
