import json
import time
from typing import Any

import redis


class CacheProvider:
    def __init__(self, redis_url: str, default_ttl: int = 60):
        self.client = redis.from_url(
            redis_url,
            decode_responses=True,
        )
        self.default_ttl = default_ttl

        self.cache_hits = 0
        self.cache_misses = 0
        self.db_queries_avoided = 0
        self.total_latency = 0.0
        self.cache_requests = 0

        self.key_access_count: dict[str, int] = {}
        self.hot_key_limit = 5

    def get(self, key: str) -> Any | None:
        start = time.perf_counter()

        data = self.client.get(key)

        latency = (time.perf_counter() - start) * 1000
        self.total_latency += latency
        self.cache_requests += 1

        self.key_access_count[key] = self.key_access_count.get(key, 0) + 1

        if self.key_access_count[key] >= self.hot_key_limit:
            print(f"HOT KEY DETECTED: {key}")

        if data is not None:
            self.cache_hits += 1
            self.db_queries_avoided += 1
            print(f"CACHE HIT: {key}")
            return json.loads(data)

        self.cache_misses += 1
        print(f"CACHE MISS: {key}")
        return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if ttl is None:
            ttl = self.default_ttl

        self.client.set(
            key,
            json.dumps(value),
            ex=ttl,
        )

        print(f"CACHE SET: {key} (TTL={ttl}s)")

    def delete(self, key: str) -> None:
        self.client.delete(key)
        self.key_access_count.pop(key, None)

        print(f"CACHE INVALIDATED: {key}")

    def get_metrics(self) -> dict:
        total = self.cache_hits + self.cache_misses

        hit_ratio = (
            (self.cache_hits / total) * 100
            if total > 0
            else 0
        )

        miss_ratio = (
            (self.cache_misses / total) * 100
            if total > 0
            else 0
        )

        average_latency = (
            self.total_latency / self.cache_requests
            if self.cache_requests > 0
            else 0
        )

        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_ratio": round(hit_ratio, 2),
            "miss_ratio": round(miss_ratio, 2),
            "db_queries_avoided": self.db_queries_avoided,
            "average_cache_latency_ms": round(average_latency, 2),
        }