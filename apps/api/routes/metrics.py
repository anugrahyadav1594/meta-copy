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
        # Mirror the provider's counters into the Prometheus registry so the
        # scrape target reports the same numbers as the JSON view. These are
        # gauges because they come from another module's counters.
        REGISTRY.set_gauge("cache_hit_ratio", cache_stats.get("hit_ratio", 0.0))
        REGISTRY.set_gauge("cache_hits", cache_stats.get("cache_hits", 0))
        REGISTRY.set_gauge("cache_misses", cache_stats.get("cache_misses", 0))
        REGISTRY.set_gauge("cache_errors", cache_stats.get("cache_errors", 0))
        REGISTRY.set_gauge("cache_invalidations", cache_stats.get("cache_invalidations", 0))
        REGISTRY.set_gauge("db_queries_avoided", cache_stats.get("db_queries_avoided", 0))
    if platform.metrics is not None:
        shard_snapshot = platform.metrics.snapshot(platform.registry)
        snapshot["sharding"] = shard_snapshot
        REGISTRY.set_gauge("shard_requests", shard_snapshot.get("total_requests", 0))
    if platform.event_bus is not None and hasattr(platform.event_bus, "stats"):
        events = platform.event_bus.stats()
        snapshot["events"] = events
        REGISTRY.set_gauge("events_published", events.get("published", 0))
        REGISTRY.set_gauge("events_delivered", events.get("delivered", 0))
        REGISTRY.set_gauge("events_failures", events.get("failures", 0))
    if platform.search_indexer is not None:
        search = platform.search_indexer.stats()
        snapshot["search"] = search
        REGISTRY.set_gauge("search_documents", search.get("documents", 0))
        REGISTRY.set_gauge("search_queries", search.get("queries", 0))
    if platform.denormalizer is not None:
        read_model = platform.denormalizer.stats()
        snapshot["read_model"] = read_model
        REGISTRY.set_gauge("read_model_events_applied", read_model.get("events_applied", 0))
        REGISTRY.set_gauge("read_model_errors", read_model.get("errors", 0))
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
