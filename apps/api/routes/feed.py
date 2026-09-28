"""Feed API (Member 7) — GET /api/v1/users/{user_id}/feed + admin endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import Platform, get_platform

router = APIRouter(tags=["feed"])


def _service(platform: Platform = Depends(get_platform)) -> Any:
    if platform.feed_service is None:
        raise HTTPException(status_code=503, detail="feed service is not initialised")
    return platform.feed_service


@router.get("/users/{user_id}/feed")
async def get_feed(
    user_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    svc: Any = Depends(_service),
) -> dict[str, Any]:
    """Chronological, recency-ranked feed of the users ``user_id`` follows.

    ``strategy`` (config ``FEED_STRATEGY``):
      * ``pull`` (default) — follow graph -> candidate posts -> rank. Works in
        NORMALIZED *and* SHARDED mode because it goes through repositories.
      * ``push`` — reads the derived ``user_feed`` fan-out table maintained by
        the feed projector from POST_CREATED events.
    """
    rows = await svc.get_feed(user_id, limit=limit)
    return {
        "user_id": user_id,
        "strategy": svc.strategy,
        "count": len(rows),
        "items": rows,
    }


@router.get("/feed/strategies")
async def describe(svc: Any = Depends(_service)) -> dict[str, Any]:
    """Which strategy is active and what it means (IMPLEMENTED vs derived)."""
    return svc.describe()


@router.post("/feed/rebuild")
async def rebuild_feed(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Rebuild the derived fan-out table from canonical follows + posts."""
    if platform.feed_projector is None:
        raise HTTPException(
            status_code=400,
            detail="fan-out projector is only active when FEED_STRATEGY=push",
        )
    rows = await platform.feed_projector.rebuild()
    return {"rebuilt": True, "rows": rows, "derived": True}


@router.get("/feed/stats")
async def feed_stats(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    return {
        "service": platform.feed_service.describe() if platform.feed_service else None,
        "projector": platform.feed_projector.stats() if platform.feed_projector else None,
    }
