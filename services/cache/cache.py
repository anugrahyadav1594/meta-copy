import json
import redis
import time


# Redis connection
redis_client = redis.Redis(
    host="localhost",
    port=6379,
    decode_responses=True
)


# -----------------------------
# Cache Metrics
# -----------------------------

cache_hits = 0
cache_misses = 0
db_queries_avoided = 0

total_cache_latency = 0
cache_requests = 0


# -----------------------------
# Hot Key Detection
# -----------------------------

key_access_count = {}

HOT_KEY_LIMIT = 5


# -----------------------------
# Get data from Redis
# -----------------------------

def get_cache(key):

    global cache_hits
    global cache_misses
    global db_queries_avoided
    global total_cache_latency
    global cache_requests

    start_time = time.perf_counter()

    data = redis_client.get(key)

    latency = (time.perf_counter() - start_time) * 1000

    total_cache_latency += latency
    cache_requests += 1

    # Track how many times this key is requested
    key_access_count[key] = key_access_count.get(key, 0) + 1

    if key_access_count[key] >= HOT_KEY_LIMIT:
        print(f"HOT KEY DETECTED: {key}")

    # Cache hit
    if data:

        cache_hits += 1
        db_queries_avoided += 1

        print(f"CACHE HIT: {key}")

        return json.loads(data)

    # Cache miss
    cache_misses += 1

    print(f"CACHE MISS: {key}")

    return None


# -----------------------------
# Store data in Redis
# -----------------------------

def set_cache(key, value, ttl=60):

    redis_client.set(
        key,
        json.dumps(value),
        ex=ttl
    )

    print(f"Stored {key} in cache for {ttl} seconds")


# -----------------------------
# Invalidate cache
# -----------------------------

def delete_cache(key):

    redis_client.delete(key)

    # Reset hot-key counter after invalidation
    if key in key_access_count:
        del key_access_count[key]

    print(f"Cache invalidated: {key}")


# -----------------------------
# Cache Metrics
# -----------------------------

def get_metrics():

    total_requests = cache_hits + cache_misses

    if total_requests > 0:

        hit_ratio = (cache_hits / total_requests) * 100
        miss_ratio = (cache_misses / total_requests) * 100

    else:

        hit_ratio = 0
        miss_ratio = 0

    if cache_requests > 0:

        average_latency = (
            total_cache_latency / cache_requests
        )

    else:

        average_latency = 0

    return {
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "hit_ratio": round(hit_ratio, 2),
        "miss_ratio": round(miss_ratio, 2),
        "db_queries_avoided": db_queries_avoided,
        "average_cache_latency_ms": round(
            average_latency, 2
        )
    }