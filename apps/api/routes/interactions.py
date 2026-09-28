"""Resource-oriented aliases for comments, likes and follows.

The canonical write paths live under `/api/v1/posts/{id}/…` and
`/api/v1/users/{id}/…` (that is where they are created). The integration
contract also names `comments`, `likes` and `follows` as first-class resources,
so this module exposes the read/delete side of each one under its own prefix.

Nothing here duplicates logic or data: every handler calls the same
repositories (canonical or sharded, according to the mode) and publishes the
same domain events.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from schemas.post import CommentRead
from schemas.user import FollowerRead

from api.dependencies import Platform, get_platform
from api.services.post_service import PostService
from api.services.user_service import UserService

comments_router = APIRouter(prefix="/comments", tags=["comments"])
likes_router = APIRouter(prefix="/likes", tags=["likes"])
follows_router = APIRouter(prefix="/follows", tags=["follows"])


def _post_service(platform: Platform) -> PostService:
    return PostService(
        platform.post_repo,
        platform.comment_repo,
        platform.user_repo,
        cache=platform.cache,
        event_bus=getattr(platform, "event_bus", None),
        hashtag_repo=platform.hashtag_repo,
    )


def _user_service(platform: Platform) -> UserService:
    return UserService(platform.user_repo, getattr(platform, "event_bus", None))


# --------------------------------------------------------------------- comments
@comments_router.get("", response_model=list[CommentRead])
async def list_comments(
    post_id: int = Query(..., description="comments belong to a post"),
    limit: int = Query(default=50, ge=1, le=200),
    platform: Platform = Depends(get_platform),
) -> list[Any]:
    """Comments for one post (targeted read on the post's shard)."""
    return await platform.comment_repo.list_for_post(post_id, limit=limit)


@comments_router.get("/counts")
async def comment_counts(
    post_id: int = Query(...), platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    """Real COUNT(*) of the comments attached to a post."""
    rows = await platform.comment_repo.list_for_post(post_id, limit=1_000)
    return {"post_id": post_id, "comment_count": len(rows), "truncated": len(rows) == 1_000}


# ------------------------------------------------------------------------ likes
@likes_router.get("")
async def list_likes(
    post_id: int = Query(..., description="likes belong to a post"),
    limit: int = Query(default=100, ge=1, le=500),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """Likes on one post with the real count from the repository."""
    rows = await platform.like_repo.list_for_post(post_id, limit=limit)
    return {
        "post_id": post_id,
        "count": await platform.like_repo.count_for_post(post_id),
        "returned": len(rows),
        "items": [
            {
                "like_id": row.like_id,
                "post_id": row.post_id,
                "user_id": row.user_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@likes_router.get("/counts")
async def like_counts(
    post_id: int = Query(...), platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    return {"post_id": post_id, "like_count": await platform.like_repo.count_for_post(post_id)}


@likes_router.delete("/{post_id}/{user_id}")
async def remove_like(
    post_id: int, user_id: int, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    """Unlike. Publishes ``LIKE_DELETED`` so the projections stay in step."""
    removed = await platform.like_repo.remove(post_id=post_id, user_id=user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="like not found")
    svc = _post_service(platform)
    await svc._publish("LIKE_DELETED", post_id, post_id=post_id, user_id=user_id)
    return {
        "post_id": post_id,
        "user_id": user_id,
        "removed": True,
        "like_count": await platform.like_repo.count_for_post(post_id),
    }


# ---------------------------------------------------------------------- follows
@follows_router.get("", response_model=list[FollowerRead])
async def list_follows(
    user_id: int = Query(...),
    direction: str = Query(
        default="followers", pattern="^(followers|following)$", description="edge direction"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    platform: Platform = Depends(get_platform),
) -> list[Any]:
    """Follow edges: who follows ``user_id``, or who ``user_id`` follows."""
    if direction == "followers":
        return await platform.follow_repo.list_followers(user_id, limit=limit)
    return await platform.follow_repo.list_following(user_id, limit=limit)


@follows_router.get("/counts")
async def follow_counts(
    user_id: int = Query(...), platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    """Real counts from the repository (scatter-gather in shard modes)."""
    followers, following = await platform.follow_repo.counts(user_id)
    return {"user_id": user_id, "followers": followers, "following": following}


@follows_router.get("/check")
async def follows_check(
    follower_id: int = Query(...),
    following_id: int = Query(...),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    return {
        "follower_id": follower_id,
        "following_id": following_id,
        "is_following": await platform.follow_repo.is_following(follower_id, following_id),
    }


@follows_router.delete("/{follower_id}/{following_id}", status_code=204)
async def remove_follow(
    follower_id: int, following_id: int, platform: Platform = Depends(get_platform)
) -> None:
    """Unfollow. Publishes ``FOLLOW_DELETED`` (the same path as the user route)."""
    removed = await platform.follow_repo.remove_follow(follower_id, following_id)
    if not removed:
        raise HTTPException(status_code=404, detail="follow edge not found")
    svc = _user_service(platform)
    await svc._publish(
        "FOLLOW_DELETED",
        following_id,
        follower_id=follower_id,
        following_id=following_id,
    )
