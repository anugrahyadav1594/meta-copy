"""Shared helpers for the sharding benchmarks (real measurements only)."""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT, _ROOT / "packages", _ROOT / "services" / "shard-router", _ROOT / "apps"):
    sys.path.insert(0, str(p))

RESULTS_DIR = _ROOT / "benchmarks" / "results" / "raw"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
    return round(values[idx], 4)


def latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    total = sum(latencies_ms)
    return {
        "samples": len(latencies_ms),
        "avg_latency_ms": round(total / max(1, len(latencies_ms)), 4),
        "p50_latency_ms": percentile(latencies_ms, 0.50),
        "p95_latency_ms": percentile(latencies_ms, 0.95),
        "p99_latency_ms": percentile(latencies_ms, 0.99),
        "max_latency_ms": round(max(latencies_ms, default=0.0), 4),
    }


def distribution(router, keys, strategy=None) -> dict[str, float]:
    counts = Counter(router.shard_for(k, strategy=strategy) for k in keys)
    total = max(1, sum(counts.values()))
    return {sid: round(100.0 * counts.get(sid, 0) / total, 2) for sid in router.shard_ids}


def write_outputs(name: str, payload: dict, rows: list[dict]) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"{name}.json"
    csv_path = RESULTS_DIR / f"{name}.csv"
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    if rows:
        # union of keys across rows, preserving first-seen order
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with csv_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return json_path, csv_path


def banner(title: str) -> None:
    print("=" * 58)
    print(title)
    print("=" * 58)


class CpuSampler:
    """Best-effort CPU% sampler (this process + spawned postgres children)."""

    def __init__(self) -> None:
        self._procs = []
        self._t0 = 0.0
        try:
            import psutil

            self._psutil = psutil
            self._procs = [psutil.Process()]
            for p in psutil.process_iter(["name"]):
                if p.info.get("name") == "postgres":
                    try:
                        self._procs.append(p)
                    except Exception:
                        pass
        except Exception:
            self._psutil = None

    def __enter__(self) -> CpuSampler:
        if self._psutil:
            for p in self._procs:
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
            self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = time.perf_counter() - self._t0 if self._t0 else 0.0

    def cpu_percent(self) -> float:
        if not self._psutil or not self.elapsed:
            return 0.0
        total = 0.0
        for p in self._procs:
            try:
                total += p.cpu_percent(None)
            except Exception:
                pass
        # cpu_percent since last call (which spans the workload); one sample.
        return round(total, 1)
