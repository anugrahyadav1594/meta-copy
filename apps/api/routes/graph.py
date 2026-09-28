"""TAO-inspired graph API (Member 9).

All reads come from the canonical ``follows`` table through repositories; the
in-memory adjacency sets are a derived, TTL-bounded projection that can be
rebuilt from PostgreSQL at any time (SQLite is NOT used anywhere).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import Platform, get_platform

router = APIRouter(prefix="/graph", tags=["graph"])


def _graph(platform: Platform = Depends(get_platform)) -> Any:
    if platform.graph is None:
        raise HTTPException(status_code=503, detail="graph service is not initialised")
    return platform.graph


@router.get("/users/{user_id}/followers")
async def followers(
    user_id: int,
    limit: int = Query(default=100, ge=1, le=1_000),
    graph: Any = Depends(_graph),
) -> dict[str, Any]:
    ids = await graph.followers(user_id, limit=limit)
    return {"user_id": user_id, "edge": "followers", "count": len(ids), "user_ids": ids}


@router.get("/users/{user_id}/following")
async def following(
    user_id: int,
    limit: int = Query(default=100, ge=1, le=1_000),
    graph: Any = Depends(_graph),
) -> dict[str, Any]:
    ids = await graph.following(user_id, limit=limit)
    return {"user_id": user_id, "edge": "following", "count": len(ids), "user_ids": ids}


@router.get("/users/{user_id}/mutuals/{other_user_id}")
async def mutuals(
    user_id: int,
    other_user_id: int,
    limit: int = Query(default=100, ge=1, le=1_000),
    graph: Any = Depends(_graph),
) -> dict[str, Any]:
    ids = await graph.mutuals(user_id, other_user_id, limit=limit)
    return {
        "user_id": user_id,
        "other_user_id": other_user_id,
        "edge": "mutuals",
        "count": len(ids),
        "user_ids": ids,
    }


@router.get("/users/{user_id}/degrees")
async def degrees(user_id: int, graph: Any = Depends(_graph)) -> dict[str, Any]:
    return {"user_id": user_id, **await graph.degrees(user_id)}


@router.get("/users/{user_id}/suggestions")
async def suggestions(
    user_id: int,
    limit: int = Query(default=10, ge=1, le=100),
    graph: Any = Depends(_graph),
) -> dict[str, Any]:
    """2-hop friends-of-friends suggestions (bounded fan-out)."""
    ids = await graph.suggestions(user_id, limit=limit)
    return {"user_id": user_id, "count": len(ids), "user_ids": ids}


@router.post("/rebuild")
async def rebuild_projection(graph: Any = Depends(_graph)) -> dict[str, Any]:
    """Drop and rebuild the derived adjacency projection from PostgreSQL."""
    return await graph.rebuild()


@router.get("/stats")
async def graph_stats(graph: Any = Depends(_graph)) -> dict[str, Any]:
    return graph.stats()
