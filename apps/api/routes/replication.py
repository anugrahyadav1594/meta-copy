"""Replication API (Member 5) — status, routing counters and failover.

IMPORTANT distinction kept explicit in every response:

* ``promote_replica`` performs a REAL PostgreSQL promotion on a physical
  standby (``pg_promote()``) and re-points the shard's primary endpoint; it
  refuses to run when the configured URL is not a standby.
* ``/demo/simulate-failover`` is the SIMULATION inherited from the ``Satyam``
  branch. It contacts nothing, changes no routing and is flagged
  ``simulated: true``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import Platform, get_platform

router = APIRouter(prefix="/replication", tags=["replication"])


def _provider(platform: Platform = Depends(get_platform)) -> Any:
    if platform.replication is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "replication provider is only active in SHARDED mode with shard "
                "URLs configured (see /api/v1/shards)"
            ),
        )
    return platform.replication


@router.get("/status")
async def status(provider: Any = Depends(_provider)) -> dict[str, Any]:
    """Per-shard primary/replica health and REAL measured lag (or null)."""
    return await provider.status()


@router.get("/describe")
async def describe(provider: Any = Depends(_provider)) -> dict[str, Any]:
    return provider.describe()


@router.post("/promote/{shard_id}")
async def promote(shard_id: str, provider: Any = Depends(_provider)) -> dict[str, Any]:
    """REAL failover: promotes a physical standby (refuses if it is not one)."""
    try:
        return await provider.promote_replica(shard_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/demo/simulate-failover/{shard_id}")
async def simulate_failover(shard_id: str, provider: Any = Depends(_provider)) -> dict[str, Any]:
    """SIMULATION ONLY — teaching demo, touches nothing."""
    return await provider.simulate_failover(shard_id)
