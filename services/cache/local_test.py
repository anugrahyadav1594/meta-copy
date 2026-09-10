import time


# Temporary in-memory cache
# This is only for testing because Redis is not running yet.
cache = {}

hits = 0
misses = 0


def get_cache(key):
    global hits, misses

    # Key does not exist
    if key not in cache:
        misses += 1
        print("CACHE MISS")
        return None

    # Check if TTL has expired
    if time.time() >= cache[key]["expiry"]:
        del cache[key]

        misses += 1
        print("CACHE MISS - TTL EXPIRED")
        return None

    # Cache hit
    hits += 1
    print("CACHE HIT")

    return cache[key]["value"]


def set_cache(key, value, ttl=60):
    # Calculate when the cached data should expire
    expiry_time = time.time() + ttl

    cache[key] = {
        "value": value,
        "expiry": expiry_time
    }

    print(f"Stored in cache (TTL = {ttl} seconds)")


def delete_cache(key):
    if key in cache:
        del cache[key]

    print("CACHE INVALIDATED")


# Temporary fake database
# Later this will be replaced by PostgreSQL.
database = {
    101: {
        "id": 101,
        "content": "Hello from MetaScale",
        "likes": 50
    }
}


def get_post(post_id):

    # Our cache key
    key = f"post:{post_id}"

    # Step 1: Check cache
    post = get_cache(key)

    if post is not None:
        return post

    # Step 2: Cache miss → query database
    print("DATABASE QUERY")

    post = database.get(post_id)

    # Step 3: Store database result in cache
    if post is not None:
        set_cache(key, post, ttl=5)

    return post


# --------------------------------------------------
# TEST 1: First request
# --------------------------------------------------

print("\n--- FIRST REQUEST ---")

print(get_post(101))


# --------------------------------------------------
# TEST 2: Second request
# --------------------------------------------------

print("\n--- SECOND REQUEST ---")

print(get_post(101))


# --------------------------------------------------
# TEST 3: TTL expiration
# --------------------------------------------------

print("\nWaiting 6 seconds for TTL to expire...")

time.sleep(6)

print("\n--- AFTER TTL EXPIRATION ---")

print(get_post(101))


# --------------------------------------------------
# TEST 4: Metrics
# --------------------------------------------------

print("\n--- METRICS ---")

total_requests = hits + misses

if total_requests > 0:
    hit_ratio = (hits / total_requests) * 100
    miss_ratio = (misses / total_requests) * 100
else:
    hit_ratio = 0
    miss_ratio = 0

print("Cache hits:", hits)
print("Cache misses:", misses)
print("Hit ratio:", round(hit_ratio, 2), "%")
print("Miss ratio:", round(miss_ratio, 2), "%")


# --------------------------------------------------
# TEST 5: Cache invalidation
# --------------------------------------------------

print("\n--- INVALIDATION ---")

delete_cache("post:101")


# --------------------------------------------------
# TEST 6: Request after invalidation
# --------------------------------------------------

print("\n--- AFTER INVALIDATION ---")

print(get_post(101))
print("\n--- HOT KEY TEST ---")

for i in range(7):
    print(f"\nRequest {i + 1}")
    get_post(101)