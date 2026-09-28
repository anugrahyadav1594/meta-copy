"""Denormalized read model API (Member 3).

``denormalized_post_feed`` is DERIVED data: it is written by the projector
that subscribes to the domain event bus, and it can always be rebuilt from
PostgreSQL. Nothing here is a source of truth.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import Platform, get_platform

router = APIRouter(tags=["denormalization"])


@router.get("/read-model/stats")
async def stats(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Projection health: events applied, skipped, errors — all measured."""
    if platform.denormalizer is None:
        raise HTTPException(
            status_code=503,
            detail="denormalized read model is disabled (DENORMALIZED_ENABLED=false)",
        )
    return platform.denormalizer.stats()


@router.post("/read-model/rebuild")
async def rebuild(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Rebuild the whole projection from the source of truth.

    In SHARDED mode the posts live on the shards, so the rebuild scatters
    over every shard and joins in the API process (see ``db.shard_scan``).
    """
    if platform.denormalizer is None:
        raise HTTPException(
            status_code=503,
            detail="denormalized read model is disabled (DENORMALIZED_ENABLED=false)",
        )
    rows = await platform.denormalizer.rebuild()
    return {
        "rebuilt": True,
        "rows": rows,
        "derived": True,
        "source_of_truth": "postgresql (canonical + shards)",
        **platform.denormalizer.stats(),
    }


@router.get("/read-model/posts")
async def list_posts(
    limit: int = Query(default=20, ge=1, le=200),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """Read the projection directly (single table, no joins) for the demo."""
    if platform.denormalizer is None or platform.canonical_engine is None:
        raise HTTPException(status_code=503, detail="read model is disabled")
    from sqlalchemy import text

    async with platform.canonical_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT post_id, user_id, author_username, content, visibility, "
                        "like_count, comment_count, created_at "
                        "FROM denormalized_post_feed "
                        "ORDER BY created_at DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
    return {
        "derived": True,
        "count": len(rows),
        "items": [
            {
                "post_id": r["post_id"],
                "user_id": r["user_id"],
                "author_username": r["author_username"],
                "content": r["content"],
                "visibility": r["visibility"],
                "like_count": r["like_count"],
                "comment_count": r["comment_count"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }
