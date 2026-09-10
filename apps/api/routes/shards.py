"""Shard admin API and sharded-data API (Member 4).

Admin endpoints (/shards/...) report topology and operate the router; the
sharded-data endpoints (/sharded/...) perform CRUD THROUGH the router — the
caller never specifies a shard id.
"""

from __future__ import annotations

from typing import Any

from common.enums import RoutingStrategy
from fastapi import APIRouter, Depends, Query, status
from schemas.post import CommentRead, PostCreate, PostRead, PostUpdate
from schemas.shard import DistributionResponse, RebalanceRequest, ShardListResponse
from schemas.user import UserCreate, UserRead, UserUpdate

from api.dependencies import Platform, get_platform
from api.services.security import hash_password
from api.services.shard_service import ShardService

router = APIRouter(tags=["sharding"])


def shards(platform: Platform = Depends(get_platform)) -> ShardService:
    platform.require_sharding()
    return ShardService(platform)


# =========================================================================
# Shard admin
# =========================================================================


@router.get("/shards", response_model=ShardListResponse)
async def list_shards(svc: ShardService = Depends(shards)) -> Any:
    return await svc.list_shards()


@router.get("/shards/stats")
async def shard_stats(svc: ShardService = Depends(shards)) -> dict[str, Any]:
    return await svc.stats()


@router.get("/shards/distribution", response_model=DistributionResponse)
async def shard_distribution(
    sample_size: int = Query(default=10_000, ge=100, le=1_000_000),
    strategy: RoutingStrategy | None = None,
    svc: ShardService = Depends(shards),
) -> Any:
    return await svc.distribution(sample_size=sample_size, strategy=strategy)


@router.get("/shards/route/user/{user_id}")
async def route_user(
    user_id: int,
    strategy: RoutingStrategy | None = None,
    svc: ShardService = Depends(shards),
) -> dict[str, Any]:
    return await svc.route_key(user_id, strategy=strategy, entity="users")


@router.get("/shards/route/{key}")
async def route_key(
    key: str,
    strategy: RoutingStrategy | None = None,
    svc: ShardService = Depends(shards),
) -> dict[str, Any]:
    return await svc.route_key(key, strategy=strategy)


@router.post("/shards/rebalance")
async def rebalance(
    payload: RebalanceRequest, svc: ShardService = Depends(shards)
) -> dict[str, Any]:
    """DEVELOPER/DEMO: add a shard and run the verified migration workflow."""
    return await svc.rebalance(payload)


@router.post("/shards/{shard_id}/health-check")
async def health_check(shard_id: str, svc: ShardService = Depends(shards)) -> dict[str, Any]:
    return await svc.health_check(shard_id)


@router.post("/shards/{shard_id}/simulate-down")
async def simulate_down(shard_id: str, svc: ShardService = Depends(shards)) -> dict[str, Any]:
    """DEVELOPER/DEMO (SIMULATION): force a shard into UNAVAILABLE state."""
    return await svc.simulate_down(shard_id)


@router.get("/shards/{shard_id}")
async def get_shard(shard_id: str, svc: ShardService = Depends(shards)) -> dict[str, Any]:
    return await svc.get_shard(shard_id)


# Demo-only workload generator (celebrity / hot-user problem)
demo_router = APIRouter(tags=["demo"])


@demo_router.post("/shards/demo/hot-user/{user_id}")
async def hot_user_demo(
    user_id: int,
    requests: int = Query(default=400, ge=10, le=5000),
    skew: float = Query(default=0.8, ge=0.0, le=1.0),
    concurrency: int = Query(default=32, ge=1, le=128),
    svc: ShardService = Depends(shards),
) -> dict[str, Any]:
    """DEVELOPER/DEMO: generate REAL skewed traffic to show a hot shard."""
    return await svc.hot_user_workload(
        user_id, requests=requests, skew=skew, concurrency=concurrency
    )


# =========================================================================
# Sharded data CRUD (the caller never chooses a shard)
# =========================================================================

sharded = APIRouter(prefix="/sharded", tags=["sharding"])


@sharded.post("/users", status_code=status.HTTP_201_CREATED)
async def sharded_create_user(
    payload: UserCreate, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    repo = platform.sharded_user_repo
    user, outcome = await repo.create_traced(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    return {
        "data": UserRead.model_validate(user).model_dump(mode="json"),
        "routing": _targeted(outcome),
    }


@sharded.get("/users/{user_id}")
async def sharded_get_user(
    user_id: int, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    user, outcome = await platform.sharded_user_repo.get_by_id_traced(user_id)
    from common.exceptions import NotFoundError

    if user is None:
        raise NotFoundError(f"user {user_id} not found", {"user_id": user_id})
    return {
        "data": UserRead.model_validate(user).model_dump(mode="json"),
        "routing": _targeted(outcome),
    }


@sharded.patch("/users/{user_id}")
async def sharded_update_user(
    user_id: int, payload: UserUpdate, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    data = payload.model_dump(exclude_unset=True)
    user = await platform.sharded_user_repo.update(user_id, **data)
    return {"data": UserRead.model_validate(user).model_dump(mode="json")}


@sharded.delete("/users/{user_id}")
async def sharded_delete_user(
    user_id: int, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    deleted = await platform.sharded_user_repo.delete(user_id)
    return {"deleted": deleted, "user_id": user_id}


@sharded.post("/posts", status_code=status.HTTP_201_CREATED)
async def sharded_create_post(
    payload: PostCreate, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    # validate author first (raises a clean 422 if missing)
    if await platform.sharded_user_repo.get_by_id(payload.author_id) is None:
        from common.exceptions import ValidationError

        raise ValidationError(
            f"author {payload.author_id} does not exist", {"author_id": payload.author_id}
        )
    post, outcome = await platform.sharded_post_repo.create_traced(
        author_id=payload.author_id,
        content=payload.content,
        visibility=payload.visibility.value,
    )
    return {
        "data": PostRead.model_validate(post).model_dump(mode="json"),
        "routing": _targeted(outcome),
    }


@sharded.get("/posts")
async def sharded_posts_scatter(
    limit: int = Query(default=50, ge=1, le=200), platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    """Scatter-gather: fan out to ALL shards, merge/sort/limit."""
    platform.require_sharding()
    rows, outcome = await platform.sharded_post_repo.list_recent_traced(limit=limit)
    return {
        "data": [PostRead.model_validate(p).model_dump(mode="json") for p in rows],
        "timing": {
            "shards_contacted": outcome.shards_contacted,
            "shard_results": [
                {
                    "shard_id": r.shard_id,
                    "rows": r.rows,
                    "latency_ms": r.latency_ms,
                    "status": r.status,
                    "error": r.error,
                }
                for r in outcome.shard_results
            ],
            "total_rows": outcome.total_rows_before_limit,
            "execution_time_ms": outcome.execution_time_ms,
            "merge_time_ms": outcome.merge_time_ms,
            "total_latency_ms": outcome.total_latency_ms,
            "unavailable_shards": outcome.unavailable_shards,
            "query_type": outcome.query_type.value,
        },
    }


@sharded.get("/posts/{post_id}")
async def sharded_get_post(
    post_id: int, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    post, outcome = await platform.sharded_post_repo.get_by_id_traced(post_id)
    from common.exceptions import NotFoundError

    if post is None:
        raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})
    return {
        "data": PostRead.model_validate(post).model_dump(mode="json"),
        "routing": _targeted(outcome),
    }


@sharded.patch("/posts/{post_id}")
async def sharded_update_post(
    post_id: int, payload: PostUpdate, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    data = payload.model_dump(exclude_unset=True)
    if data.get("visibility") is not None:
        data["visibility"] = data["visibility"].value
    post = await platform.sharded_post_repo.update(post_id, **data)
    return {"data": PostRead.model_validate(post).model_dump(mode="json")}


@sharded.delete("/posts/{post_id}")
async def sharded_delete_post(
    post_id: int, platform: Platform = Depends(get_platform)
) -> dict[str, Any]:
    platform.require_sharding()
    deleted = await platform.sharded_post_repo.delete(post_id)
    return {"deleted": deleted, "post_id": post_id}


@sharded.get("/posts/{post_id}/comments")
async def sharded_post_comments(
    post_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    platform.require_sharding()
    post = await platform.sharded_post_repo.get_by_id(post_id)
    from common.exceptions import NotFoundError

    if post is None:
        raise NotFoundError(f"post {post_id} not found", {"post_id": post_id})
    comments, outcome = await platform.sharded_comment_repo.list_for_post_traced(post_id, limit)
    return {
        "data": [CommentRead.model_validate(c).model_dump(mode="json") for c in comments],
        "routing": _targeted(outcome),
    }


@sharded.get("/posts/{post_id}/cross-shard")
async def sharded_cross_shard(post_id: int, svc: ShardService = Depends(shards)) -> dict[str, Any]:
    """Educational cross-shard JOIN demo (post + comments + authors)."""
    return await svc.post_cross_shard(post_id)


# Merge the demo and sharded-data sub-routers into the module router.
router.include_router(demo_router)
router.include_router(sharded)


def _targeted(outcome: Any) -> dict[str, Any]:
    return {
        "shard_contacted": outcome.shard_contacted,
        "strategy": outcome.decision.strategy.value,
        "entity": outcome.decision.entity,
        "shard_key": outcome.decision.shard_key,
        "key": outcome.decision.key,
        "virtual_node": outcome.decision.virtual_node,
        "routing_latency_ms": outcome.routing_latency_ms,
        "database_latency_ms": outcome.database_latency_ms,
        "total_latency_ms": outcome.total_latency_ms,
        "query_type": outcome.query_type.value,
    }
