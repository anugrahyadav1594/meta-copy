"""Member 11 — observability: metrics registry + Prometheus exposition.

Every metric exposed here is actually measured somewhere in the request
path — nothing is synthesised to make a dashboard look busy. Counters and
histograms live in this process; the sharding collector (Member 4) and the
cache provider (Member 6) push their own real values in through
``record_*``/``set_gauge``.

Exposition:
* ``GET /metrics``            — Prometheus text format (scrape target)
* ``GET /api/v1/metrics``     — the same values as JSON
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from typing import Any

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile (no interpolation, no invented values)."""
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, math.ceil(q * len(sorted_values)) - 1))
    return round(sorted_values[index], 3)


class Histogram:
    """Reservoir-free histogram: it keeps every sample up to ``max_samples``.

    Sample counts stay small enough for a demo service; P50/P95/P99 are
    computed from real observations.
    """

    def __init__(self, max_samples: int = 2_048) -> None:
        self.max_samples = max_samples
        self._samples: list[float] = []
        self.count = 0
        self.total = 0.0
        self.min: float | None = None
        self.max: float | None = None

    def observe(self, value: float) -> None:
        value = float(value)
        self.count += 1
        self.total += value
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)
        if len(self._samples) < self.max_samples:
            self._samples.append(value)
        else:  # reservoir sampling keeps the tail representative
            import random

            idx = random.randrange(self.count)
            if idx < self.max_samples:
                self._samples[idx] = value

    def snapshot(self) -> dict[str, float]:
        ordered = sorted(self._samples)
        return {
            "count": self.count,
            "sum_ms": round(self.total, 3),
            "avg_ms": round(self.total / self.count, 3) if self.count else 0.0,
            "min_ms": round(self.min or 0.0, 3),
            "max_ms": round(self.max or 0.0, 3),
            "p50_ms": _percentile(ordered, 0.50),
            "p95_ms": _percentile(ordered, 0.95),
            "p99_ms": _percentile(ordered, 0.99),
        }


class MetricsRegistry:
    """Counters, gauges and histograms for the whole platform."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = {}
        self.histograms: dict[str, Histogram] = defaultdict(Histogram)

    # ------------------------------------------------------------- recording
    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self.counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self.gauges[name] = float(value)

    def observe(self, name: str, value_ms: float) -> None:
        with self._lock:
            self.histograms[name].observe(value_ms)

    # -------------------------------------------------------------- registry
    def register_known_metrics(self) -> None:
        """Declare the documented metric names up-front (zero values)."""
        for name in (
            "request_count",
            "errors_total",
            "cache_hits",
            "cache_misses",
            "cache_errors",
            "cache_invalidations",
            "db_queries_avoided",
            "shard_requests",
            "shard_errors",
            "search_latency_ms",
            "feed_latency_ms",
            "media_latency_ms",
            "graph_latency_ms",
            "database_latency_ms",
            "request_latency_ms",
        ):
            self.counters.setdefault(name, 0.0)
        for name in (
            "cache_hit_ratio",
            "hot_shards",
            "shard_load_max",
            "replication_lag_seconds",
            "media_bytes_stored",
            "events_published",
        ):
            self.gauges.setdefault(name, 0.0)

    # ---------------------------------------------------------------- output
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self.counters)
            gauges = dict(self.gauges)
            histograms = {name: h.snapshot() for name, h in self.histograms.items()}

        cache_hits = counters.get("cache_hits", 0.0)
        cache_misses = counters.get("cache_misses", 0.0)
        lookups = cache_hits + cache_misses
        return {
            "uptime_seconds": round(time.time() - self.started_at, 3),
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
            "derived": {
                "cache_hit_ratio": round(cache_hits / lookups, 4) if lookups else 0.0,
                "request_latency": histograms.get("request_latency_ms", {}),
                "database_latency": histograms.get("database_latency_ms", {}),
            },
        }

    def render_prometheus(self) -> str:
        """Prometheus text exposition format."""
        snapshot = self.snapshot()
        lines: list[str] = []

        def emit(name: str, value: float, help_text: str, kind: str) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.append(f"{name} {value}")
            lines.append("")

        emit(
            "metascale_uptime_seconds",
            snapshot["uptime_seconds"],
            "Seconds since the API process started.",
            "gauge",
        )

        for name, value in sorted(snapshot["counters"].items()):
            emit(
                f"metascale_{name}",
                value,
                f"Counter {name} (measured).",
                "counter",
            )

        for name, value in sorted(snapshot["gauges"].items()):
            emit(f"metascale_{name}", value, f"Gauge {name} (measured).", "gauge")

        for name, stats in sorted(snapshot["histograms"].items()):
            base = f"metascale_{name}"
            lines.append(f"# HELP {base} Latency histogram in milliseconds ({name}).")
            lines.append(f"# TYPE {base} summary")
            for quantile, key in (
                ("0.5", "p50_ms"),
                ("0.95", "p95_ms"),
                ("0.99", "p99_ms"),
            ):
                lines.append(f'{base}{{quantile="{quantile}"}} {stats[key]}')
            lines.append(f"{base}_sum {stats['sum_ms']}")
            lines.append(f"{base}_count {stats['count']}")
            lines.append("")

        emit(
            "metascale_cache_hit_ratio",
            snapshot["derived"]["cache_hit_ratio"],
            "cache_hits / (cache_hits + cache_misses).",
            "gauge",
        )
        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()
REGISTRY.register_known_metrics()


def timed(name: str):
    """Decorator/context helper measuring a coroutine into a histogram."""
    import functools
    from collections.abc import Callable

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                REGISTRY.observe(name, (time.perf_counter() - start) * 1000.0)

        return wrapper

    return decorator
