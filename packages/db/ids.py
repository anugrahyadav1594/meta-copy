"""Application-side global ID generation for sharded deployments.

Independent per-shard sequences would collide (each shard starts at 1), so in
SHARDED mode IDs are minted in the application as 63-bit monotonically
increasing, roughly time-sortable integers (Snowflake-like, no external
coordinator):

    | 1 unused bit | 41 bits ms since epoch | 14 bits counter | 8 bits slot |

Key-alignment (e.g. a post id that routes to the same shard as its author) is
then achieved by probing successive ids with the router — see
``ShardRouter.generate_key_routed_to``.
"""

from __future__ import annotations

import threading
import time

EPOCH_MS = 1_700_000_000_000  # fixed epoch (Nov 2023)
COUNTER_BITS = 14
SLOT_BITS = 8
MAX_COUNTER = (1 << COUNTER_BITS) - 1
MAX_SLOT = (1 << SLOT_BITS) - 1


class SnowflakeIdGenerator:
    def __init__(self, slot: int = 0) -> None:
        if not 0 <= slot <= MAX_SLOT:
            raise ValueError(f"slot must be 0..{MAX_SLOT}")
        self._slot = slot
        self._lock = threading.Lock()
        self._last_ms = -1
        self._counter = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def next_id(self) -> int:
        with self._lock:
            now = self._now_ms()
            if now == self._last_ms:
                self._counter += 1
                if self._counter > MAX_COUNTER:
                    # spin until next millisecond
                    while now <= self._last_ms:
                        now = self._now_ms()
                    self._counter = 0
            elif now < self._last_ms:  # tiny clock skew: keep monotone
                now = self._last_ms
                self._counter += 1
            else:
                self._counter = 0
            self._last_ms = now
            return (
                ((now - EPOCH_MS) << (COUNTER_BITS + SLOT_BITS))
                | (self._counter << SLOT_BITS)
                | self._slot
            )


class SequentialIdGenerator:
    """Deterministic 1..N generator used by the seeded datasets."""

    def __init__(self, start: int = 1) -> None:
        self._next = start
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            value = self._next
            self._next += 1
            return value
