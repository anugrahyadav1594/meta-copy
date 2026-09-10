"""Post/comment business logic.

Member 6 added cache-aside to the hottest read path, ``GET /posts/{id}``::

    GET post -> Redis -> (MISS) -> Repository -> (Shard Router) -> PostgreSQL
                    │ HIT -> return (database never contacted)

The cache sits ABOVE the repository; neither the repository nor the shard
router knows it exists. Writes invalidate ``post:{id}`` so stale data is never
served after an update/delete.
"""

from __future__ import annotations

from typing import Any

from common.exceptions import ConflictError, NotFoundError, ValidationError
from schemas.post import CommentCreate, PostCreate, PostUpdate
from sqlalchemy.exc import IntegrityError

CACHE_SOURCE_HIT = "hit"
CACHE_SOURCE_MISS = "miss"
CACHE_SOURCE_BYPASS = "bypass"


class PostService:
    def __init__(
        self,
        post_repo: Any,
        comment_repo: Any,
        user_repo: Any,
        cache: Any = None,
    ) -> None:
        self.posts = post_repo
        self.comments = comment_repo
        self.users = user_repo
        self.cache = cache

    # ---------------------------------------------------------- serialization
    @staticmethod
    def _serialize(post: Any) -> dict[str, Any]:
        """Cache payload — must include every PostRead field.

        Timestamps are ISO-8601 strings so Pydantic can re-validate them on a
        cache hit.
        """
        return {
            "id": post.id,
            "author_id": post.author_id,
            "content": post.content,
            "visibility": post.visibility,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        }

    async def get(self, post_id: int) -> tuple[Any, str]:
        """Return ``(post, cache_source)``; cache_source ∈ hit/miss/bypass."""
        cache_key = f"post:{post_id}"

        if self.cache is not None:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                return cached, CACHE_SOURCE_HIT

        post = await self.posts.get_by_id(post_id)
        if post is None:
            raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})

        if self.cache is not None:
            await self.cache.set(cache_key, self._serialize(post))
            return post, CACHE_SOURCE_MISS
        return post, CACHE_SOURCE_BYPASS

    async def create(self, payload: PostCreate) -> Any:
        # Foreign-key hygiene: author must exist (identical in both modes).
        author = await self.users.get_by_id(payload.author_id)
        if author is None:
            raise ValidationError(
                f"author {payload.author_id} does not exist",
                {"author_id": payload.author_id},
            )
        try:
            return await self.posts.create(
                post_id=0,
                author_id=payload.author_id,
                content=payload.content,
                visibility=payload.visibility.value,
            )
        except IntegrityError as exc:
            raise ConflictError("could not create post", {"detail": str(exc.orig)[:160]}) from exc

    async def update(self, post_id: int, payload: PostUpdate) -> Any:
        data = payload.model_dump(exclude_unset=True)
        if "visibility" in data and data["visibility"] is not None:
            data["visibility"] = data["visibility"].value
        try:
            updated = await self.posts.update(post_id, **data)
            # Write-invalidate: drop any stale cached representation.
            if self.cache is not None:
                await self.cache.delete(f"post:{post_id}")
            return updated
        except LookupError as exc:
            raise NotFoundError(str(exc), {"post_id": post_id}) from exc
        except IntegrityError as exc:  # pragma: no cover - defensive
            raise ConflictError("could not update post") from exc

    async def delete(self, post_id: int) -> bool:
        deleted = await self.posts.delete(post_id)
        if not deleted:
            raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})
        if self.cache is not None:
            await self.cache.delete(f"post:{post_id}")
        return True

    async def recent(self, limit: int = 50) -> Any:
        # Collection endpoints are not cached in this milestone; they may be
        # backed by denormalized feeds by a later member.
        return await self.posts.list_recent(limit=limit)

    async def comments_for(self, post_id: int, limit: int = 50) -> list[Any]:
        # Existence check (may be served from cache); result comes from the
        # repository/shard router, never from Redis.
        await self.get(post_id)
        return await self.comments.list_for_post(post_id, limit=limit)

    async def comment(self, post_id: int, payload: CommentCreate) -> Any:
        await self.get(post_id)
        user = await self.users.get_by_id(payload.user_id)
        if user is None:
            raise ValidationError(
                f"commenter {payload.user_id} does not exist",
                {"user_id": payload.user_id},
            )
        return await self.comments.create(
            post_id=post_id, user_id=payload.user_id, content=payload.content
        )

    async def invalidate_post(self, post_id: int) -> None:
        """Explicit invalidation hook (admin / future event consumers)."""
        if self.cache is not None:
            await self.cache.delete(f"post:{post_id}")

    async def cache_metrics(self) -> dict[str, Any]:
        if self.cache is None:
            return {"cache_enabled": False}
        return {"cache_enabled": True, **self.cache.get_metrics()}
