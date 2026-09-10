from cache import get_cache, set_cache, delete_cache, get_metrics


# Temporary data — this will represent PostgreSQL later
fake_database = {
    101: {
        "id": 101,
        "content": "Hello from MetaScale",
        "likes": 50
    }
}


def get_post(post_id):

    key = f"post:{post_id}"

    # 1. Check cache
    post = get_cache(key)

    if post is not None:
        return post

    # 2. Cache miss → get from database
    print("DATABASE QUERY")

    post = fake_database.get(post_id)

    # 3. Store result in cache
    if post is not None:
        set_cache(key, post, ttl=60)

    return post


print("\n--- FIRST REQUEST ---")
print(get_post(101))

print("\n--- SECOND REQUEST ---")
print(get_post(101))

print("\n--- METRICS ---")
print(get_metrics())

print("\n--- INVALIDATING CACHE ---")
delete_cache("post:101")