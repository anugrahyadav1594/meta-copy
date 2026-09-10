"""User endpoints (mode-aware: canonical or sharded repository)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from schemas.user import FollowerRead, UserCreate, UserRead, UserUpdate

from api.dependencies import Platform, get_platform
from api.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def service(platform: Platform = Depends(get_platform)) -> UserService:
    return UserService(platform.user_repo)


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
