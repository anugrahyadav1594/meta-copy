"""Metrics collection for the shard router.

Tracks, per shard and globally:

* request counts (read/write), errors
* latency samples -> P50 / P95 / P99
* routing latency, routing errors
* scatter-gather / cross-shard query counts
* rows-per-shard gauges, active connections
* migration counters (rows migrated, duration, failures)

``snapshot()`` returns a plain dict that is also the contract for the future
observability member's :class:`events.contracts.MetricsSink`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ShardMetrics:
    shard_id: str
    requests: int = 0
    reads: int = 0
    writes: int = 0
    errors: int = 0
    active_connections: int = 0
    row_count: int = 0
    _samples: deque[tuple[float, float]] = field(default_factory=lambda: deque())

    def percentile(self, q: float, window_s: float = 300.0) -> float:
        cutoff = time.monotonic() - window_s
        values = sorted(lat for ts, lat in self._samples if ts >= cutoff)
        if not values:
            return 0.0
        idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
        return round(values[idx], 3)

    def rate(self, window_s: float = 60.0) -> float:
        cutoff = time.monotonic() - window_s
        return float(sum(1 for ts, _ in self._samples if ts >= cutoff)) / window_s


class MetricsCollector:
    """Thread-safe-ish in-memory collector (single asyncio loop expected)."""

    MAX_SAMPLES_PER_SHARD = 20_000

    def __init__(self, shard_ids: list[str] | None = None) -> None:
        self._lock = threading.Lock()
        self.shards: dict[str, ShardMetrics] = {}
        for sid in shard_ids or []:
            self.shards[sid] = ShardMetrics(sid)
        # Global counters
        self.routing_errors = 0
        self.routing_latencies: deque[float] = deque(maxlen=self.MAX_SAMPLES_PER_SHARD)
        self.scatter_gather_queries = 0
        self.cross_shard_queries = 0
        self.rows_migrated = 0
        self.migration_failures = 0
        self.migration_durations_ms: list[float] = []

    def register_shard(self, shard_id: str) -> None:
        with self._lock:
            self.shards.setdefault(shard_id, ShardMetrics(shard_id))

    def remove_shard(self, shard_id: str) -> None:
        with self._lock:
            self.shards.pop(shard_id, None)

    # ------------------------------------------------------------- recording
    def record_routing(self, latency_ms: float, *, error: bool = False) -> None:
        self.routing_latencies.append(latency_ms)
        if error:
            self.routing_errors += 1

    def record_query(
        self,
        shard_id: str,
        *,
        kind: str = "read",
        latency_ms: float = 0.0,
        error: bool = False,
    ) -> None:
        with self._lock:
            m = self.shards.setdefault(shard_id, ShardMetrics(shard_id))
            m.requests += 1
            if kind == "write":
                m.writes += 1
            else:
                m.reads += 1
            if error:
                m.errors += 1
            m._samples.append((time.monotonic(), latency_ms))
            if len(m._samples) > self.MAX_SAMPLES_PER_SHARD:
                m._samples.popleft()

    def record_scatter_gather(self, n: int = 1) -> None:
        self.scatter_gather_queries += n

    def record_cross_shard(self, n: int = 1) -> None:
        self.cross_shard_queries += n

    def set_row_counts(self, counts: dict[str, int]) -> None:
        for sid, n in counts.items():
            self.shards.setdefault(sid, ShardMetrics(sid)).row_count = n

    def set_active_connections(self, shard_id: str, n: int) -> None:
        self.shards.setdefault(shard_id, ShardMetrics(shard_id)).active_connections = n

    def record_migration(self, rows: int, duration_ms: float, *, failed: bool = False) -> None:
        if failed:
            self.migration_failures += 1
        else:
            self.rows_migrated += rows
        self.migration_durations_ms.append(duration_ms)

    # --------------------------------------------------------------- export
    def _percentile(self, values: list[float], q: float) -> float:
        if not values:
            return 0.0
        values = sorted(values)
        idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
        return round(values[idx], 3)

    def per_shard(self) -> dict[str, ShardMetrics]:
        return dict(self.shards)

    def snapshot(self, registry: Any | None = None) -> dict[str, Any]:
        with self._lock:
            total_requests = sum(m.requests for m in self.shards.values())
            statuses: dict[str, str] = {}
            if registry is not None:
                for shard in registry.list_sync():
                    statuses[shard.id] = shard.status.value
            healthy = sum(1 for sid in self.shards if statuses.get(sid, "HEALTHY") == "HEALTHY")
            unavailable = sum(1 for sid in self.shards if statuses.get(sid) == "UNAVAILABLE")
            per_shard: dict[str, Any] = {}
            for sid, m in self.shards.items():
                per_shard[sid] = {
                    "requests": m.requests,
                    "status": statuses.get(sid, "HEALTHY"),
                    "requests_per_sec": round(m.rate(), 3),
                    "reads": m.reads,
                    "writes": m.writes,
                    "errors": m.errors,
                    "avg_latency_ms": (
                        round(
                            sum(lat for _, lat in list(m._samples)[-1000:])
                            / max(1, min(len(m._samples), 1000)),
                            3,
                        )
                    ),
                    "p50_latency_ms": m.percentile(0.50),
                    "p95_latency_ms": m.percentile(0.95),
                    "p99_latency_ms": m.percentile(0.99),
                    "active_connections": m.active_connections,
                    "rows": m.row_count,
                    "load_fraction": (
                        round(m.requests / total_requests, 4) if total_requests else 0.0
                    ),
                }
            routing = list(self.routing_latencies)
            return {
                "generated_at": time.time(),
                "shards": per_shard,
                "shard_count": len(self.shards),
                "total_requests": total_requests,
                "healthy_shards": healthy,
                "failed_shards": unavailable,
                "routing_latency_avg_ms": self._percentile(routing, 0.50),
                "routing_latency_p95_ms": self._percentile(routing, 0.95),
                "routing_errors": self.routing_errors,
                "scatter_gather_queries": self.scatter_gather_queries,
                "cross_shard_queries": self.cross_shard_queries,
                "rows_migrated": self.rows_migrated,
                "migration_duration_ms": self._percentile(self.migration_durations_ms, 0.50),
                "migration_failures": self.migration_failures,
            }

    def reset(self) -> None:
        """Test/benchmark helper: zero every counter."""
        with self._lock:
            for sid in list(self.shards):
                self.shards[sid] = ShardMetrics(sid)
            self.routing_errors = 0
            self.routing_latencies.clear()
            self.scatter_gather_queries = 0
            self.cross_shard_queries = 0
            self.rows_migrated = 0
            self.migration_failures = 0
            self.migration_durations_ms.clear()

    # convenience for detectors that want raw counts
    def request_counts(self) -> dict[str, int]:
        return {sid: m.requests for sid, m in self.shards.items()}
