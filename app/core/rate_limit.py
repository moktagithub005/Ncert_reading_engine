from typing import cast

from redis.exceptions import RedisError

from app.cache.redis import get_redis


def allow_request(key: str, maximum: int, window_seconds: int = 60) -> bool:
    """Use one Redis counter window for multi-worker rate limiting."""
    try:
        client = get_redis()
        count = cast(int, client.incr(key))
        if count == 1:
            client.expire(key, window_seconds)
        return count <= maximum
    except RedisError:
        return False
