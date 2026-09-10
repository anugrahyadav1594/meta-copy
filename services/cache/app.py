from fastapi import FastAPI

from cache import get_cache, set_cache, delete_cache, get_metrics


app = FastAPI(title="MetaScale Cache Service")


# Temporary data
# This will later be replaced by PostgreSQL.
database = {
    101: {
        "id": 101,
        "content": "Hello from MetaScale",
        "likes": 50
    },
    102: {
        "id": 102,
        "content": "Distributed caching is interesting",
        "likes": 100
    }
}


@app.get("/api/v1/posts/{post_id}")
def get_post(post_id: int):

    # Redis cache key
    cache_key = f"post:{post_id}"

    # 1. Check cache
    post = get_cache(cache_key)

    if post is not None:
        return {
            "source": "cache",
            "data": post
        }

    # 2. Cache miss → database
    post = database.get(post_id)

    if post is None:
        return {
            "error": "Post not found"
        }

    # 3. Store result in cache
    set_cache(cache_key, post, ttl=60)

    return {
        "source": "database",
        "data": post
    }


@app.delete("/api/v1/cache/posts/{post_id}")
def invalidate_post_cache(post_id: int):

    cache_key = f"post:{post_id}"

    delete_cache(cache_key)

    return {
        "message": "Cache invalidated",
        "key": cache_key
    }


@app.get("/api/v1/cache/metrics")
def cache_metrics():

    return get_metrics()