"""Member 11 — observability surface for the Architecture Control Center.

Everything here is measured in the request path: request counters, latency
histograms (P50/P95/P99 computed from real samples), cache counters pushed in
by Member 6, shard counters pushed in by Member 4, event counters from the
bus. There are no placeholder or decorative numbers.

``/api/v1/observability/status`` additionally probes every dependency the
deployment declares and classifies it as **healthy**, **degraded** or
**unavailable** — the same vocabulary as ``/api/v1/health``.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from api.dependencies import Platform, get_platform
from api.routes.health import aggregate_health
from services.observability.metrics import REGISTRY

router = APIRouter(prefix="/observability", tags=["observability"])

_START = time.monotonic()


@router.get("/status")
async def observability_status(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Dependency health + the counters each module actually records."""
    health = await aggregate_health(platform)
    snapshot = REGISTRY.snapshot()
    return {
        "status": health["status"],
        "components": health["components"],
        "required": health["required"],
        "mode": platform.settings.mode.value,
        "uptime_seconds": round(time.monotonic() - _START, 3),
        "measured": {
            "requests": snapshot.get("counters", {}).get("request_count", 0),
            "errors": snapshot.get("counters", {}).get("errors_total", 0),
            "request_latency_ms": snapshot.get("histograms", {}).get("request_latency_ms", {}),
        },
        "derived_systems": {
            "denormalized_read_model": platform.denormalizer is not None,
            "feed_strategy": platform.settings.feed_strategy,
            "search_provider": platform.settings.search_provider,
            "media_backend": platform.settings.media_backend,
            "event_bus": platform.settings.event_bus,
        },
    }


@router.get("/metrics")
async def observability_metrics(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """JSON snapshot of every measured metric (same values as /metrics)."""
    from api.routes.metrics import metrics

    return await metrics(platform)


@router.get("/latency")
async def latency_summary() -> dict[str, Any]:
    """Latency histograms only — P50/P95/P99 from real observations."""
    snapshot = REGISTRY.snapshot()
    return {
        "histograms": snapshot.get("histograms", {}),
        "note": (
            "percentiles are computed from the samples actually recorded since "
            "process start; an empty histogram means the path was never exercised"
        ),
    }
