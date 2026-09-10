from fastapi import APIRouter, Depends, Query

from api.dependencies import Platform, get_platform
from api.services.feed_service import FeedService

router = APIRouter(prefix="/feed", tags=["feed"])


def service(platform: Platform = Depends(get_platform)) -> FeedService:
    return FeedService(platform.feed_repo)


@router.get("/pull/{user_id}")
async def pull_feed(
    user_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    svc: FeedService = Depends(service),
):
    return await svc.pull_feed(user_id, limit=limit)


@router.get("/push/{user_id}")
async def push_feed(
    user_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    platform: Platform = Depends(get_platform),
):
    await platform.feed_repo.ensure_push_table()
    return await platform.feed_repo.get_push_feed(user_id, limit=limit)