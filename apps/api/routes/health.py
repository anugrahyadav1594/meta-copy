"""Liveness/readiness endpoints (unversioned by convention).

``/health``  — liveness: the process is up. Touches **no** dependency, so a
               database outage can never make Kubernetes restart a healthy
               process.

``/ready``   — readiness: every dependency the deployment actually uses is
               probed and reported. Optional backends that are DISABLED are
               reported as ``disabled`` and never fail the check; optional
               backends that are enabled but unreachable are reported as
               ``degraded`` for the same reason the application itself keeps
               serving: cache, search, messaging, media and replicas are all
               fail-open, and PostgreSQL is the only source of truth.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text

from api.dependencies import Platform, get_platform

router = APIRouter(tags=["system"])

# Versioned alias required by the integrated API contract: GET /api/v1/health.
v1_router = APIRouter(prefix="/api/v1", tags=["system"])

_START = time.monotonic()
PROBE_TIMEOUT = 2.0


@router.get("/health")
async def health(platform: Platform = Depends(get_platform)) -> dict[str, object]:
    """Liveness: process is up. No dependency checks."""
    return {
        "status": "healthy",
        "service": "metascale-api",
        "mode": platform.settings.mode.value,
        "uptime_seconds": round(time.monotonic() - _START, 3),
    }


async def _probe(coro: Any) -> str:
    """Run a probe with a hard timeout; never raise out of /ready."""
    try:
        result = await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT)
    except TimeoutError:
        return "unavailable: timeout"
    except Exception as exc:  # noqa: BLE001 - readiness must never 500
        return f"unavailable: {type(exc).__name__}"
    return "ready" if result else "unavailable: probe returned False"


async def _probe_redis(platform: Platform) -> str:
    if platform.cache is None:
        return "disabled"
    return await _probe(platform.cache.ping())


async def _probe_rabbitmq(platform: Platform) -> str:
    transport = (platform.settings.event_bus or "memory").lower()
    if transport != "rabbitmq":
        return f"disabled (event_bus={transport})"

    async def connect() -> bool:
        import aio_pika  # optional dependency

        connection = await aio_pika.connect_robust(
            platform.settings.rabbitmq_url, timeout=PROBE_TIMEOUT
        )
        await connection.close()
        return True

    return await _probe(connect())


async def _probe_opensearch(platform: Platform) -> str:
    provider = getattr(platform.search_indexer, "provider", None)
    if provider is None:
        return "disabled"
    available = getattr(provider, "available", None)
    if available is None:  # in-memory provider: no external dependency
        return "disabled (search_provider=memory)"
    return await _probe(available())


async def _probe_minio(platform: Platform) -> str:
    store = getattr(platform.media_service, "blobs", None) if platform.media_service else None
    if store is None or getattr(store, "backend", "local") != "minio":
        media_backend = getattr(store, "backend", None) if store is not None else "local"
        return f"disabled (media_backend={media_backend})"

    async def head() -> bool:
        import httpx

        endpoint = getattr(store, "endpoint", "")
        scheme = "https" if getattr(store, "secure", False) else "http"
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            response = await client.get(f"{scheme}://{endpoint}/minio/health/live")
        return response.status_code == 200

    return await _probe(head())


@router.get("/ready")
async def ready(platform: Platform = Depends(get_platform)) -> dict[str, object]:
    """Readiness: every dependency the deployment uses, probed and reported."""
    checks: dict[str, Any] = {}
    required_ok = True

    # ---------------------------------------------- required: canonical PG
    try:
        async with platform.canonical_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["canonical_postgres"] = "ready"
    except Exception as exc:  # noqa: BLE001
        required_ok = False
        checks["canonical_postgres"] = f"unavailable: {type(exc).__name__}"

    # ------------------------------------------- required when sharding on
    if platform.sharding_available:
        shards: dict[str, str] = {}
        probes = await platform.health_checker.check_all()
        for probe in probes:
            shards[probe.shard_id] = (
                "ready" if probe.reachable else f"unavailable: {probe.error or 'down'}"
            )
            if not probe.reachable:
                required_ok = False
        checks["shards"] = shards

    # ------------------------------------- optional: probed, never fatal
    checks["redis"] = await _probe_redis(platform)
    checks["rabbitmq"] = await _probe_rabbitmq(platform)
    checks["opensearch"] = await _probe_opensearch(platform)
    checks["minio"] = await _probe_minio(platform)
    if platform.replication is not None:
        status = await platform.replication.status()
        checks["replication"] = {
            "active": status["replication_active"],
            "per_shard": {
                s["shard_id"]: {
                    "replica_configured": s["replica_configured"],
                    "read_routing": s["read_routing"],
                }
                for s in status["shards"]
            },
        }

    degraded = [
        name
        for name in ("redis", "rabbitmq", "opensearch", "minio")
        if str(checks[name]).startswith("unavailable")
    ]

    return {
        "status": "ready" if required_ok else "not_ready",
        "degraded_optional_dependencies": degraded,
        "mode": platform.settings.mode.value,
        "derived_systems": {
            "denormalized_read_model": platform.denormalizer is not None,
            "feed_strategy": platform.settings.feed_strategy,
            "search_provider": platform.settings.search_provider,
            "media_backend": platform.settings.media_backend,
            "event_bus": platform.settings.event_bus,
        },
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/health — aggregate status across every declared dependency.
#
# Vocabulary is exactly ``healthy`` | ``degraded`` | ``unavailable``:
#   * required components (API process, canonical PostgreSQL, configured
#     shards) failing makes the whole system ``unavailable``
#   * optional, fail-open components (Redis, RabbitMQ, OpenSearch, MinIO,
#     replicas) that are enabled but unreachable make it ``degraded``
#   * optional components that were never configured are reported
#     ``unavailable`` with ``required: false`` and a ``detail`` explaining it —
#     they are not counted as degradation because the system is designed to run
#     without them.
# ---------------------------------------------------------------------------


async def aggregate_health(platform: Platform) -> dict[str, object]:
    """Probe every dependency and classify each one. Never raises."""
    components: dict[str, Any] = {}
    required: list[str] = ["api", "postgresql"]

    components["api"] = {"status": "healthy", "required": True, "detail": "request served"}

    # ------------------------------------------------- required: PostgreSQL
    try:
        async with platform.canonical_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        components["postgresql"] = {
            "status": "healthy",
            "required": True,
            "detail": "canonical database reachable",
        }
    except Exception as exc:  # noqa: BLE001 - health must never 500
        components["postgresql"] = {
            "status": "unavailable",
            "required": True,
            "detail": f"{type(exc).__name__}",
        }

    # ---------------------------------------------- required when configured
    if platform.sharding_available and platform.health_checker is not None:
        required.append("shards")
        probes = await platform.health_checker.check_all()
        per_shard = {
            p.shard_id: {
                "status": "healthy" if p.reachable else "unavailable",
                "latency_ms": round(p.latency_ms, 3) if p.latency_ms is not None else None,
                "detail": None if p.reachable else (p.error or "unreachable"),
            }
            for p in probes
        }
        down = [s for s in per_shard if per_shard[s]["status"] != "healthy"]
        components["shards"] = {
            "status": "unavailable" if down else "healthy",
            "required": True,
            "count": len(probes),
            "unavailable": down,
            "per_shard": per_shard,
        }

    # ------------------------------------------------- optional, fail-open
    async def classify(name: str, probe: Any) -> None:
        """Run a probe coroutine and map the result onto the vocabulary."""
        try:
            value = await probe()
        except Exception as exc:  # noqa: BLE001
            components[name] = {
                "status": "unavailable",
                "required": False,
                "detail": f"probe error: {type(exc).__name__}",
            }
            return
        if value is None:  # not configured in this deployment
            components[name] = {
                "status": "unavailable",
                "required": False,
                "detail": "not configured",
            }
        elif value is True:
            components[name] = {"status": "healthy", "required": False, "detail": "reachable"}
        else:
            components[name] = {
                "status": "degraded",
                "required": False,
                "detail": str(value),
            }

    async def _redis() -> Any:
        if platform.cache is None:
            return None
        return bool(await asyncio.wait_for(platform.cache.ping(), timeout=PROBE_TIMEOUT))

    async def _rabbitmq() -> Any:
        transport = (platform.settings.event_bus or "memory").lower()
        if transport != "rabbitmq":
            return None
        return (await _probe_rabbitmq(platform)) == "ready"

    async def _opensearch() -> Any:
        provider = getattr(platform.search_indexer, "provider", None)
        available = getattr(provider, "available", None)
        if provider is None or available is None:
            return None
        return bool(await asyncio.wait_for(available(), timeout=PROBE_TIMEOUT))

    async def _minio() -> Any:
        store = getattr(platform.media_service, "blobs", None) if platform.media_service else None
        if store is None or getattr(store, "backend", "local") != "minio":
            return None
        import httpx

        endpoint = getattr(store, "endpoint", "")
        scheme = "https" if getattr(store, "secure", False) else "http"
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            response = await client.get(f"{scheme}://{endpoint}/minio/health/live")
        return response.status_code == 200

    async def _replication() -> Any:
        if platform.replication is None:
            return None
        status = await platform.replication.status()
        return {
            "status": "healthy" if status.get("replication_active") else "unavailable",
            "required": False,
            "detail": (
                "replicas configured"
                if status.get("replication_active")
                else "no replicas configured"
            ),
            "shards": status.get("shards", []),
        }

    await classify("redis", _redis)
    await classify("rabbitmq", _rabbitmq)
    await classify("opensearch", _opensearch)
    await classify("minio", _minio)

    if platform.replication is not None:
        status = await platform.replication.status()
        components["replication"] = {
            "status": "healthy" if status.get("replication_active") else "unavailable",
            "required": False,
            "detail": (
                "replica URLs configured"
                if status.get("replication_active")
                else "no replica URLs configured — primary-only"
            ),
            "per_shard": status.get("shards", []),
        }

    broken_required = [n for n in required if components.get(n, {}).get("status") != "healthy"]
    degraded_optional = [
        n for n, c in components.items() if not c.get("required") and c.get("status") == "degraded"
    ]
    if broken_required:
        overall = "unavailable"
    elif degraded_optional:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "service": "metascale-api",
        "mode": platform.settings.mode.value,
        "required": required,
        "unavailable_required": broken_required,
        "degraded_optional": degraded_optional,
        "components": components,
    }


@v1_router.get("/health")
async def health_v1(platform: Platform = Depends(get_platform)) -> dict[str, object]:
    """Aggregate health of API + PostgreSQL + shards + optional backends."""
    return await aggregate_health(platform)
