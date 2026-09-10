"""Hot-shard detection.

A shard is "hot" when it absorbs a disproportionate share of traffic. Three
configurable signals (any can fire; the finding reports which):

1. ``load_ratio_threshold`` — share of all requests exceeds e.g. 40% with 4+
   shards (fair share would be 25%).
2. ``relative_ratio`` — its request count is >= e.g. 1.8x the mean of the
   others.
3. minimum request volume so a quiet system never cries wolf.

Nothing here is hard-coded: feed it real MetricsCollector snapshots (the
benchmark generates skewed celebrity traffic) and it measures.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.exceptions import RoutingError
from metrics.collector import MetricsCollector


@dataclass(frozen=True)
class HotShard:
    shard_id: str
    load_fraction: float
    request_count: int
    fair_fraction: float
    relative_to_peers: float
    reasons: list[str]

    def describe(self) -> str:
        return (
            f"{self.shard_id}: {self.load_fraction * 100:.1f}% of traffic "
            f"(fair {self.fair_fraction * 100:.1f}%, "
            f"{self.relative_to_peers:.2f}x peers) [{', '.join(self.reasons)}]"
        )


class HotShardDetector:
    def __init__(
        self,
        metrics: MetricsCollector,
        *,
        load_ratio_threshold: float = 0.40,
        min_requests: int = 100,
        relative_ratio: float = 1.8,
    ) -> None:
        self.metrics = metrics
        self.load_ratio_threshold = load_ratio_threshold
        self.min_requests = min_requests
        self.relative_ratio = relative_ratio

    def detect(self) -> list[HotShard]:
        counts = self.metrics.request_counts()
        active = {sid: n for sid, n in counts.items()}
        if not active:
            return []
        total = sum(active.values())
        n = len(active)
        fair = 1.0 / n
        findings: list[HotShard] = []

        for sid, count in active.items():
            if count < self.min_requests:
                continue
            fraction = count / total if total else 0.0
            peers = [c for k, c in active.items() if k != sid]
            peer_mean = (sum(peers) / len(peers)) if peers else 0.0
            relative = (count / peer_mean) if peer_mean > 0 else float("inf")

            reasons: list[str] = []
            if fraction >= self.load_ratio_threshold:
                reasons.append(
                    f"load {fraction * 100:.1f}% >= threshold "
                    f"{self.load_ratio_threshold * 100:.0f}%"
                )
            if relative >= self.relative_ratio:
                reasons.append(f"{relative:.2f}x peer mean (>= {self.relative_ratio})")
            if reasons:
                findings.append(
                    HotShard(
                        shard_id=sid,
                        load_fraction=round(fraction, 4),
                        request_count=count,
                        fair_fraction=round(fair, 4),
                        relative_to_peers=round(relative, 2),
                        reasons=reasons,
                    )
                )
        findings.sort(key=lambda h: h.load_fraction, reverse=True)
        return findings

    def hottest(self) -> HotShard | None:
        findings = self.detect()
        return findings[0] if findings else None

    def hottest_or_raise(self) -> HotShard:
        hottest = self.hottest()
        if hottest is None:
            raise RoutingError("No hot shard detected with the current metrics")
        return hottest
