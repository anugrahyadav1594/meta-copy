"""Post/comment endpoints (mode-aware: canonical or sharded repository).

The single-post read transparently uses Redis cache-aside when enabled and
reports whether the response was a cache ``HIT``, ``MISS`` (filled from the
database), or ``BYPASS`` (caching disabled) via the ``X-Cache`` header.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from schemas.post import CommentCreate, CommentRead, PostCreate, PostRead, PostUpdate

from api.dependencies import Platform, get_platform
from api.services.post_service import PostService

router = APIRouter(prefix="/posts", tags=["posts"])


def service(platform: Platform = Depends(get_platform)) -> PostService:
    return PostService(
        platform.post_repo,
        platform.comment_repo,
        platform.user_repo,
        platform.cache,
    )


@router.post("", response_model=PostRead, status_code=status.HTTP_201_CREATED)
async def create_post(payload: PostCreate, svc: PostService = Depends(service)) -> object:
    return await svc.create(payload)


@router.get("", response_model=list[PostRead])
async def recent_posts(
    limit: int = Query(default=50, ge=1, le=200), svc: PostService = Depends(service)
) -> list[object]:
    return await svc.recent(limit=limit)


@router.get("/{post_id}", response_model=PostRead)
async def get_post(
    post_id: int,
    response: Response,
    svc: PostService = Depends(service),
) -> object:
    post, source = await svc.get(post_id)
    response.headers["X-Cache"] = source.upper()
    return post


@router.patch("/{post_id}", response_model=PostRead)
async def update_post(
    post_id: int, payload: PostUpdate, svc: PostService = Depends(service)
) -> object:
    return await svc.update(post_id, payload)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: int, svc: PostService = Depends(service)) -> None:
    await svc.delete(post_id)


@router.get("/{post_id}/comments", response_model=list[CommentRead])
async def comments(
    post_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    svc: PostService = Depends(service),
) -> list[object]:
    return await svc.comments_for(post_id, limit=limit)


@router.post("/{post_id}/comments", response_model=CommentRead, status_code=status.HTTP_201_CREATED)
async def add_comment(
    post_id: int, payload: CommentCreate, svc: PostService = Depends(service)
) -> object:
    return await svc.comment(post_id, payload)
