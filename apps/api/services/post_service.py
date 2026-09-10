"""Post/comment business logic (mode-agnostic)."""

from __future__ import annotations

from typing import Any

from common.exceptions import ConflictError, NotFoundError, ValidationError
from schemas.post import CommentCreate, PostCreate, PostUpdate
from sqlalchemy.exc import IntegrityError


class PostService:
    def __init__(self, post_repo: Any, comment_repo: Any, user_repo: Any) -> None:
        self.posts = post_repo
        self.comments = comment_repo
        self.users = user_repo

    async def get(self, post_id: int) -> Any:
        post = await self.posts.get_by_id(post_id)
        if post is None:
            raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})
        return post

    async def create(self, payload: PostCreate) -> Any:
        # Foreign-key hygiene: author must exist (checked at app level so the
        # error is identical in NORMALIZED and SHARDED modes).
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
        except IntegrityError as exc:  # FK violation on canonical DB etc.
            raise ConflictError("could not create post", {"detail": str(exc.orig)[:160]}) from exc

    async def update(self, post_id: int, payload: PostUpdate) -> Any:
        data = payload.model_dump(exclude_unset=True)
        if "visibility" in data and data["visibility"] is not None:
            data["visibility"] = data["visibility"].value
        try:
            return await self.posts.update(post_id, **data)
        except LookupError as exc:
            raise NotFoundError(str(exc), {"post_id": post_id}) from exc

    async def delete(self, post_id: int) -> bool:
        deleted = await self.posts.delete(post_id)
        if not deleted:
            raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})
        return True

    async def recent(self, limit: int = 50) -> Any:
        return await self.posts.list_recent(limit=limit)

    async def comments_for(self, post_id: int, limit: int = 50) -> list[Any]:
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
