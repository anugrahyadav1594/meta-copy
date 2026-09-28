"""User endpoints (mode-aware: canonical or sharded repository)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from schemas.user import (
    FollowCreate,
    FollowerRead,
    UserCreate,
    UserRead,
    UserUpdate,
)

from api.dependencies import Platform, get_platform
from api.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def service(platform: Platform = Depends(get_platform)) -> UserService:
    return UserService(platform.user_repo, getattr(platform, "event_bus", None))


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, svc: UserService = Depends(service)) -> object:
    return await svc.create(payload)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: int, svc: UserService = Depends(service)) -> object:
    return await svc.get(user_id)


@router.get("", response_model=list[UserRead])
async def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: UserService = Depends(service),
) -> list[object]:
    return await svc.list(limit=limit, offset=offset)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int, payload: UserUpdate, svc: UserService = Depends(service)
) -> object:
    return await svc.update(user_id, payload)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, svc: UserService = Depends(service)) -> None:
    await svc.delete(user_id)


@router.get("/{user_id}/followers", response_model=list[FollowerRead])
async def followers(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    svc: UserService = Depends(service),
) -> list[object]:
    return await svc.followers(user_id, limit=limit)


@router.post("/{user_id}/follow", status_code=status.HTTP_201_CREATED)
async def follow_user(
    user_id: int, payload: FollowCreate, svc: UserService = Depends(service)
) -> dict[str, object]:
    """Create a follow edge (canonical PostgreSQL, routed by Member 4)."""
    created = await svc.follow(user_id, payload.following_id)
    return {
        "follower_id": user_id,
        "following_id": payload.following_id,
        "created": created,
    }


@router.delete("/{user_id}/follow/{following_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unfollow_user(
    user_id: int, following_id: int, svc: UserService = Depends(service)
) -> None:
    await svc.unfollow(user_id, following_id)


@router.get("/{user_id}/following")
async def following(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    svc: UserService = Depends(service),
) -> list[object]:
    return await svc.following(user_id, limit=limit)


@router.get("/{user_id}/notifications")
async def notifications(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    unread_only: bool = Query(default=False),
    platform: Platform = Depends(get_platform),
) -> dict[str, object]:
    rows = await platform.notification_repo.list_for_user(
        user_id, limit=limit, unread_only=unread_only
    )
    return {
        "user_id": user_id,
        "count": len(rows),
        "items": [
            {
                "notification_id": n.notification_id,
                "user_id": n.user_id,
                "actor_user_id": n.actor_user_id,
                "type": n.type,
                "post_id": n.post_id,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
    }
