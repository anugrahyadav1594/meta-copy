"""Hot-shard detection and metrics collection."""

from __future__ import annotations

from hotspots.detector import HotShardDetector
from metrics.collector import MetricsCollector

SHARDS = [f"shard-{i}" for i in range(4)]


def test_balanced_traffic_not_flagged():
    m = MetricsCollector(SHARDS)
    for _ in range(400):
        for sid in SHARDS:
            m.record_query(sid, latency_ms=1.0)
    detector = HotShardDetector(m, min_requests=50)
    assert detector.detect() == []


def test_skewed_traffic_is_flagged():
    m = MetricsCollector(SHARDS)
    # shard-2 gets the celebrity workload (~70%), others share the rest
    for _ in range(700):
        m.record_query("shard-2", latency_ms=2.0)
    for sid in ("shard-0", "shard-1", "shard-3"):
        for _ in range(70):
            m.record_query(sid, latency_ms=1.0)
    detector = HotShardDetector(m, min_requests=50, load_ratio_threshold=0.4)
    hot = detector.detect()
    assert len(hot) == 1
    assert hot[0].shard_id == "shard-2"
    assert hot[0].load_fraction > 0.5


def test_low_volume_never_alarms():
    m = MetricsCollector(SHARDS)
    for _ in range(5):
        m.record_query("shard-2", latency_ms=1.0)
    detector = HotShardDetector(m, min_requests=50)
    assert detector.hottest() is None


def test_percentiles_and_snapshot():
    m = MetricsCollector(SHARDS)
    for ms in range(1, 101):
        m.record_query("shard-0", latency_ms=float(ms))
    snap = m.snapshot()
    s0 = snap["shards"]["shard-0"]
    assert s0["requests"] == 100
    assert s0["p50_latency_ms"] <= s0["p95_latency_ms"] <= s0["p99_latency_ms"]
    assert snap["total_requests"] == 100
    assert set(snap["shards"]) == set(SHARDS)


def test_migration_counters():
    m = MetricsCollector(SHARDS)
    m.record_migration(1234, 56.7)
    m.record_migration(0, 9.0, failed=True)
    snap = m.snapshot()
    assert snap["rows_migrated"] == 1234
    assert snap["migration_failures"] == 1


def test_routing_errors_counted():
    m = MetricsCollector(SHARDS)
    m.record_routing(0.01, error=True)
    assert m.snapshot()["routing_errors"] == 1
