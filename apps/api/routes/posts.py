"""Post/comment endpoints (mode-aware: canonical or sharded repository).

The single-post read transparently uses Redis cache-aside when enabled and
reports whether the response was a cache ``HIT``, ``MISS`` (filled from the
database), or ``BYPASS`` (caching disabled) via the ``X-Cache`` header.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from schemas.post import (
    CommentCreate,
    CommentRead,
    LikeCreate,
    PostCreate,
    PostRead,
    PostUpdate,
)

from api.dependencies import Platform, get_platform
from api.services.post_service import PostService

router = APIRouter(prefix="/posts", tags=["posts"])


def service(platform: Platform = Depends(get_platform)) -> PostService:
    return PostService(
        platform.post_repo,
        platform.comment_repo,
        platform.user_repo,
        platform.cache,
        getattr(platform, "event_bus", None),
        getattr(platform, "hashtag_repo", None),
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


@router.post("/{post_id}/like", status_code=status.HTTP_201_CREATED)
async def like_post(
    post_id: int,
    payload: LikeCreate,
    platform: Platform = Depends(get_platform),
) -> dict[str, object]:
    """Idempotent like (unique post_id/user_id) + LIKE_CREATED event."""
    svc = service(platform)
    post, _source = await svc.get(post_id)  # 404 when the post does not exist
    like = await platform.like_repo.add(post_id=post_id, user_id=payload.user_id)
    await svc._publish(
        "LIKE_CREATED",
        post_id,
        post_id=post_id,
        user_id=payload.user_id,
    )
    return {
        "post_id": post_id,
        "user_id": payload.user_id,
        "like_count": await platform.like_repo.count_for_post(post_id),
        "created_at": like.created_at.isoformat() if like.created_at else None,
        # ``get`` returns an ORM row on a miss and a dict on a cache HIT.
        "post_author_id": (
            post.get("user_id") if isinstance(post, dict) else getattr(post, "user_id", None)
        ),
    }


@router.delete("/{post_id}/like/{user_id}")
async def unlike_post(
    post_id: int,
    user_id: int,
    platform: Platform = Depends(get_platform),
) -> dict[str, object]:
    removed = await platform.like_repo.remove(post_id=post_id, user_id=user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="like not found")
    svc = service(platform)
    await svc._publish("LIKE_DELETED", post_id, post_id=post_id, user_id=user_id)
    return {
        "post_id": post_id,
        "user_id": user_id,
        "removed": True,
        "like_count": await platform.like_repo.count_for_post(post_id),
    }


@router.get("/{post_id}/likes")
async def post_likes(
    post_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    platform: Platform = Depends(get_platform),
) -> dict[str, object]:
    rows = await platform.like_repo.list_for_post(post_id, limit=limit)
    return {
        "post_id": post_id,
        "count": len(rows),
        "total": await platform.like_repo.count_for_post(post_id),
        "user_ids": [row.user_id for row in rows],
    }


@router.get("/{post_id}/hashtags")
async def post_hashtags(
    post_id: int, platform: Platform = Depends(get_platform)
) -> dict[str, object]:
    tags = await platform.hashtag_repo.tags_for_post(post_id)
    return {"post_id": post_id, "count": len(tags), "tags": tags}
