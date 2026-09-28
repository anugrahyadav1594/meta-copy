"""Baseline hash sharding: ``shard = stable_hash(key) % shard_count``.

We deliberately do NOT use Python's built-in ``hash()``: it is salted per
process (``PYTHONHASHSEED``) and therefore cannot be used for *persistent*
routing — a row written under one process must be found under every other.

The default hash is a pure-Python, dependency-free **MurmurHash3 x86_32**
(seed 0), which is stable across processes, machines, Python versions and
architectures. SHA-256 (truncated to 64 bits) and MD5 (32 bits) are offered
as alternatives; only the modulo bucket is persisted anywhere.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# MurmurHash3 x86_32 (Austin Appleby, public domain / MIT), pure Python.
# Deterministic, fast, non-cryptographic — ideal for a shard router.
# ---------------------------------------------------------------------------


def _murmur3_x86_32(data: bytes, seed: int = 0) -> int:
    key = bytearray(data)
    length = len(key)
    nblocks = length // 4

    h1 = seed & 0xFFFFFFFF
    c1 = 0xCC9E2D51
    c2 = 0x1B873593

    def rotl32(x: int, r: int) -> int:
        return ((x << r) | (x >> (32 - r))) & 0xFFFFFFFF

    # body
    for block_start in range(0, nblocks * 4, 4):
        k1 = struct.unpack_from("<I", key, block_start)[0]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = rotl32(k1, 15)
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
        h1 = rotl32(h1, 13)
        h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF

    # tail
    tail_index = nblocks * 4
    k1 = 0
    tail_size = length & 3
    if tail_size >= 3:
        k1 ^= key[tail_index + 2] << 16
    if tail_size >= 2:
        k1 ^= key[tail_index + 1] << 8
    if tail_size >= 1:
        k1 ^= key[tail_index]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = rotl32(k1, 15)
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1

    # finalization
    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
    h1 ^= h1 >> 13
    h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
    h1 ^= h1 >> 16
    return h1


def stable_hash(key: int | str, *, algo: str = "murmur3") -> int:
    """Return a stable, cross-process non-negative integer hash of ``key``.

    Integers are hashed from their decimal UTF-8 bytes; strings directly.
    Supported algos: ``murmur3`` (default, 32-bit) and ``sha256`` (64-bit).
    """
    raw = str(key).encode("utf-8")
    if algo == "murmur3":
        return _murmur3_x86_32(raw)
    if algo == "sha256":
        digest = hashlib.sha256(raw).digest()
        return struct.unpack(">Q", digest[:8])[0]
    if algo == "md5":
        digest = hashlib.md5(raw).digest()  # noqa: S324 — non-security use
        return struct.unpack(">I", digest[:4])[0]
    raise ValueError(f"Unknown stable hash algorithm: {algo!r}")


@dataclass(frozen=True)
class HashRoute:
    key: str
    shard_id: str
    bucket: int
    shard_count: int
    strategy: str = "hash"


class HashRouter:
    """Modulo router over an ordered list of shard ids."""

    def __init__(self, shard_ids: list[str], *, algo: str = "murmur3") -> None:
        if not shard_ids:
            raise ValueError("HashRouter requires at least one shard")
        self.algo = algo
        self._shards = list(shard_ids)

    # ----- topology --------------------------------------------------------
    @property
    def shard_ids(self) -> list[str]:
        return list(self._shards)

    def set_shards(self, shard_ids: list[str]) -> None:
        if not shard_ids:
            raise ValueError("at least one shard required")
        self._shards = list(shard_ids)

    # ----- routing ---------------------------------------------------------
    def route(self, key: int | str) -> HashRoute:
        digest = stable_hash(key, algo=self.algo)
        bucket = digest % len(self._shards)
        return HashRoute(
            key=str(key),
            shard_id=self._shards[bucket],
            bucket=bucket,
            shard_count=len(self._shards),
        )

    def shard_for(self, key: int | str) -> str:
        return self.route(key).shard_id

    def distribution(self, keys: list[int | str]) -> dict[str, float]:
        """Measured percentage (0..100) of keys landing on each shard."""
        counts = {sid: 0 for sid in self._shards}
        for k in keys:
            counts[self.shard_for(k)] += 1
        total = max(len(keys), 1)
        return {sid: round(100.0 * c / total, 2) for sid, c in counts.items()}
