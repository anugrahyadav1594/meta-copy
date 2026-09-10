"""Cache observability/admin endpoints (Member 6).

These are always registered but report ``cache_enabled: false`` when caching
is off, so the API works identically without Redis. Invalidation is marked as
a developer/demo operation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import Platform, get_platform
from api.services.post_service import PostService

router = APIRouter(prefix="/cache", tags=["cache"])


def service(platform: Platform = Depends(get_platform)) -> PostService:
    return PostService(
        platform.post_repo,
        platform.comment_repo,
        platform.user_repo,
        platform.cache,
    )


@router.get("/metrics")
async def cache_metrics(platform: Platform = Depends(get_platform)) -> dict:
    """Redis cache performance: hits/misses, ratio, latency, hot keys."""
    if platform.cache is None:
        return {
            "cache_enabled": False,
            "message": "Redis cache is disabled or failed to start (fail-open).",
        }
    return {"cache_enabled": True, **platform.cache.get_metrics()}


@router.get("/health")
async def cache_health(platform: Platform = Depends(get_platform)) -> dict:
    if platform.cache is None:
        return {"cache_enabled": False, "status": "disabled"}
    reachable = await platform.cache.ping()
    return {
        "cache_enabled": True,
        "status": "healthy" if reachable else "unavailable",
        "backend": platform.cache.backend,
    }


@router.get("/keys")
async def cache_keys(platform: Platform = Depends(get_platform)) -> dict:
    if platform.cache is None:
        return {"cache_enabled": False, "keys": []}
    keys = await platform.cache.keys("post:*")
    return {"cache_enabled": True, "count": len(keys), "keys": keys}


@router.delete("/posts/{post_id}")
async def invalidate_post(post_id: int, svc: PostService = Depends(service)) -> dict:
    """DEVELOPER/DEMO: explicitly invalidate ``post:{id}``."""
    await svc.invalidate_post(post_id)
    return {"invalidated": True, "key": f"post:{post_id}"}
