"""Observability API (Member 11).

``/metrics`` is the Prometheus scrape target (text exposition format);
``/api/v1/metrics`` returns the same measured values as JSON. Only metrics
that are actually recorded in the request path appear here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from api.dependencies import Platform, get_platform
from services.observability.metrics import PROMETHEUS_CONTENT_TYPE, REGISTRY

# Unversioned scrape target for Prometheus: GET /metrics
prometheus_router = APIRouter(tags=["observability"])

# JSON view mounted under /api/v1/metrics
router = APIRouter(tags=["observability"])


@prometheus_router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    return Response(content=REGISTRY.render_prometheus(), media_type=PROMETHEUS_CONTENT_TYPE)


@router.get("/metrics")
async def metrics(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Measured counters, gauges and latency percentiles (P50/P95/P99)."""
    snapshot = REGISTRY.snapshot()

    # Enrich with the real values owned by other modules.
    if platform.cache is not None:
        cache_stats = platform.cache.get_metrics()
        snapshot["cache"] = cache_stats
        REGISTRY.set_gauge("cache_hit_ratio", cache_stats.get("hit_ratio", 0.0))
        REGISTRY.increment("cache_hits", 0)
        REGISTRY.increment("cache_misses", 0)
    if platform.metrics is not None:
        shard_snapshot = platform.metrics.snapshot(platform.registry)
        snapshot["sharding"] = shard_snapshot
        REGISTRY.set_gauge("shard_requests", shard_snapshot.get("total_requests", 0))
    if platform.event_bus is not None and hasattr(platform.event_bus, "stats"):
        snapshot["events"] = platform.event_bus.stats()
    if platform.search_indexer is not None:
        snapshot["search"] = platform.search_indexer.stats()
    if platform.denormalizer is not None:
        snapshot["read_model"] = platform.denormalizer.stats()
    return snapshot


@router.get("/metrics/summary")
async def metrics_summary() -> dict[str, Any]:
    """Compact view: just the headline numbers a dashboard would show."""
    snap = REGISTRY.snapshot()
    return {
        "uptime_seconds": snap["uptime_seconds"],
        "counters": snap["counters"],
        "gauges": snap["gauges"],
        "latency": snap["histograms"],
    }
