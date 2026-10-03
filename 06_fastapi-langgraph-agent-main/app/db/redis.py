"""Async Redis client lifecycle.

A single Redis connection pool is created when the app starts.
Other modules can use get_redis() to access the same Redis client.
"""

from __future__ import annotations

import redis.asyncio as redis
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

# Store the Redis client so it can be reused across the application.
_client: redis.Redis | None = None


# Create and connect the Redis client.
async def connect_redis() -> redis.Redis:
    global _client

    # Create a Redis client using the URL from the application settings.
    _client = redis.from_url(settings.redis_url, decode_responses=True)

    # Check that the Redis server is reachable.
    await _client.ping()

    log.info("redis_connected", url=settings.redis_url)
    return _client


# Close the Redis connection
async def disconnect_redis() -> None:
    global _client

    # Close the Redis connection if it was created.
    if _client is not None:
        await _client.aclose()

        # Remove the stored client after closing the connection.
        _client = None
        log.info("redis_disconnected")


# Return the existing Redis client.
def get_redis() -> redis.Redis:
    if _client is None:
        raise RuntimeError("Redis client not initialized - did the app lifespan run?")
    return _client
