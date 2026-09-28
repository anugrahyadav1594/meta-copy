"""Liveness/readiness endpoints (unversioned by convention)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from api.dependencies import Platform, get_platform

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness: process is up. No dependency checks."""
    return {"status": "healthy", "service": "metascale-api"}


@router.get("/ready")
async def ready(platform: Platform = Depends(get_platform)) -> dict[str, object]:
    """Readiness: canonical DB (and shards when enabled) are reachable."""
    checks: dict[str, object] = {}
    ready = True

    # canonical PostgreSQL
    try:
        async with platform.canonical_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["canonical_postgres"] = "ready"
    except Exception as exc:  # noqa: BLE001
        ready = False
        checks["canonical_postgres"] = f"unavailable: {type(exc).__name__}"

    if platform.sharding_available:
        shards: dict[str, str] = {}
        probes = await platform.health_checker.check_all()
        for probe in probes:
            shards[probe.shard_id] = "ready" if probe.reachable else "unavailable"
            if not probe.reachable:
                ready = False
        checks["shards"] = shards

    return {
        "status": "ready" if ready else "degraded",
        "mode": platform.settings.mode.value,
        "checks": checks,
    }
