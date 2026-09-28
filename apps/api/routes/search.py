"""Search API (Member 10) — search + autocomplete over a derived index."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import Platform, get_platform

router = APIRouter(prefix="/search", tags=["search"])


def _indexer(platform: Platform = Depends(get_platform)) -> Any:
    if platform.search_indexer is None:
        raise HTTPException(status_code=503, detail="search indexer is not initialised")
    return platform.search_indexer


@router.get("")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    indexer: Any = Depends(_indexer),
) -> dict[str, Any]:
    """BM25 search over posts and users.

    Results always carry the canonical ``post_id`` / ``user_id``, never an id
    invented by the index.
    """
    result = await indexer.search(q, limit=limit)
    result["provider"] = indexer.provider.backend
    result["derived"] = True
    return result


@router.get("/autocomplete")
async def autocomplete(
    prefix: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    indexer: Any = Depends(_indexer),
) -> dict[str, Any]:
    return await indexer.autocomplete(prefix, limit=limit)


@router.post("/reindex")
async def reindex(indexer: Any = Depends(_indexer)) -> dict[str, Any]:
    """Rebuild the index from canonical PostgreSQL (proves it is derived)."""
    count = await indexer.rebuild()
    return {"reindexed": True, "documents": count, "derived": True}


@router.get("/stats")
async def search_stats(indexer: Any = Depends(_indexer)) -> dict[str, Any]:
    return indexer.stats()
