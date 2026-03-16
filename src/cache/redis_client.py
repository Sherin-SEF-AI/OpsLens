"""Redis caching layer for OpsLens with JSON support and pub/sub."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from redis.asyncio import ConnectionPool, Redis

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Cache key namespace constants
# ---------------------------------------------------------------------------

NS_INCIDENT = "incident"          # incident:{id}
NS_INCIDENTS_LIST = "incidents:list"  # incidents:list:{hash}
NS_STATS = "stats"                # stats:{org_id}
NS_USER = "user"                  # user:{id}
NS_SEARCH = "search"              # search:{hash}


def cache_key(namespace: str, identifier: str) -> str:
    """Build a namespaced cache key.

    Examples:
        cache_key("incident", "OPSLENS-0001")  -> "incident:OPSLENS-0001"
        cache_key("stats", "org-uuid")          -> "stats:org-uuid"
    """
    return f"{namespace}:{identifier}"


# ---------------------------------------------------------------------------
# RedisCache
# ---------------------------------------------------------------------------

class RedisCache:
    """Async Redis client wrapping connection pool, caching, and pub/sub.

    Usage::

        redis = RedisCache()
        await redis.connect("redis://localhost:6379/0")
        await redis.set("key", "value", ttl=300)
        value = await redis.get("key")
        await redis.close()
    """

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._redis: Redis | None = None
        self._url: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, redis_url: str = "redis://localhost:6379/0") -> None:
        """Initialize the Redis connection pool.

        Args:
            redis_url: Full Redis connection URL.
        """
        if self._redis is not None:
            return

        self._url = redis_url
        self._pool = ConnectionPool.from_url(
            redis_url,
            max_connections=20,
            decode_responses=True,
        )
        self._redis = Redis(connection_pool=self._pool)
        logger.info("redis.connected", url=redis_url.split("@")[-1])

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None
        logger.info("redis.closed")

    @property
    def client(self) -> Redis:
        """Return the underlying Redis client. Raises if not connected."""
        if self._redis is None:
            raise RuntimeError(
                "RedisCache not connected. Call connect() first."
            )
        return self._redis

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Check if Redis is reachable.

        Returns:
            True if Redis responds to PING, False otherwise.
        """
        try:
            return await self.client.ping()
        except Exception:
            logger.warning("redis.ping_failed")
            return False

    # ------------------------------------------------------------------
    # Basic key/value operations
    # ------------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        """Get a string value by key.

        Args:
            key: Cache key.

        Returns:
            The stored string or None if missing/expired.
        """
        try:
            value = await self.client.get(key)
            return value
        except Exception:
            logger.exception("redis.get_error", key=key)
            return None

    async def set(
        self, key: str, value: str, ttl: int = 300
    ) -> None:
        """Set a string value with TTL.

        Args:
            key: Cache key.
            value: String value to store.
            ttl: Time-to-live in seconds (default 300 = 5 minutes).
        """
        try:
            await self.client.set(key, value, ex=ttl)
        except Exception:
            logger.exception("redis.set_error", key=key)

    async def delete(self, key: str) -> None:
        """Delete a key.

        Args:
            key: Cache key to delete.
        """
        try:
            await self.client.delete(key)
        except Exception:
            logger.exception("redis.delete_error", key=key)

    # ------------------------------------------------------------------
    # JSON operations
    # ------------------------------------------------------------------

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Get a JSON-serialized value.

        Args:
            key: Cache key.

        Returns:
            Parsed dict or None.
        """
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("redis.json_decode_error", key=key)
            return None

    async def set_json(
        self, key: str, value: dict[str, Any], ttl: int = 300
    ) -> None:
        """Store a dict as JSON with TTL.

        Args:
            key: Cache key.
            value: Dict to serialize and store.
            ttl: Time-to-live in seconds.
        """
        try:
            serialized = json.dumps(value, default=str)
            await self.set(key, serialized, ttl=ttl)
        except Exception:
            logger.exception("redis.set_json_error", key=key)

    # ------------------------------------------------------------------
    # Pattern invalidation
    # ------------------------------------------------------------------

    async def invalidate_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern.

        Args:
            pattern: Redis glob pattern (e.g., ``incident:*``).

        Returns:
            Number of keys deleted.
        """
        deleted = 0
        try:
            cursor: int = 0
            while True:
                cursor, keys = await self.client.scan(
                    cursor=cursor, match=pattern, count=100
                )
                if keys:
                    await self.client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            if deleted > 0:
                logger.info(
                    "redis.pattern_invalidated",
                    pattern=pattern,
                    deleted=deleted,
                )
        except Exception:
            logger.exception("redis.invalidate_pattern_error", pattern=pattern)
        return deleted

    # ------------------------------------------------------------------
    # Pub/Sub
    # ------------------------------------------------------------------

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        """Publish a JSON message to a Redis channel.

        Args:
            channel: Channel name (e.g., ``ws:incidents``).
            message: Dict payload to serialize and publish.
        """
        try:
            serialized = json.dumps(message, default=str)
            await self.client.publish(channel, serialized)
        except Exception:
            logger.exception("redis.publish_error", channel=channel)

    async def subscribe(self, channel: str) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to a Redis channel and yield parsed messages.

        Args:
            channel: Channel name to subscribe to.

        Yields:
            Parsed dict messages from the channel.
        """
        pubsub = self.client.pubsub()
        try:
            await pubsub.subscribe(channel)
            logger.info("redis.subscribed", channel=channel)

            async for raw_message in pubsub.listen():
                if raw_message is None:
                    continue
                if raw_message["type"] != "message":
                    continue
                data = raw_message.get("data")
                if data is None:
                    continue
                try:
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    parsed = json.loads(data)
                    yield parsed
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "redis.subscribe_decode_error",
                        channel=channel,
                        data=str(data)[:200],
                    )
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            logger.info("redis.unsubscribed", channel=channel)

    # ------------------------------------------------------------------
    # Convenience: namespaced operations
    # ------------------------------------------------------------------

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Get cached incident data."""
        return await self.get_json(cache_key(NS_INCIDENT, incident_id))

    async def set_incident(
        self, incident_id: str, data: dict[str, Any], ttl: int = 300
    ) -> None:
        """Cache incident data."""
        await self.set_json(cache_key(NS_INCIDENT, incident_id), data, ttl=ttl)

    async def invalidate_incident(self, incident_id: str) -> None:
        """Invalidate cached incident data."""
        await self.delete(cache_key(NS_INCIDENT, incident_id))

    async def get_stats(self, org_id: str) -> dict[str, Any] | None:
        """Get cached stats for an org."""
        return await self.get_json(cache_key(NS_STATS, org_id))

    async def set_stats(
        self, org_id: str, data: dict[str, Any], ttl: int = 60
    ) -> None:
        """Cache org stats (short TTL since they change frequently)."""
        await self.set_json(cache_key(NS_STATS, org_id), data, ttl=ttl)

    async def invalidate_stats(self, org_id: str) -> None:
        """Invalidate cached stats."""
        await self.delete(cache_key(NS_STATS, org_id))

    async def invalidate_all_incidents(self) -> int:
        """Invalidate all cached incident data."""
        return await self.invalidate_pattern(f"{NS_INCIDENT}:*")

    async def invalidate_all_lists(self) -> int:
        """Invalidate all cached incident lists."""
        return await self.invalidate_pattern(f"{NS_INCIDENTS_LIST}:*")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: RedisCache | None = None


def get_redis() -> RedisCache:
    """Get the global RedisCache singleton.

    The instance is created lazily; call ``await get_redis().connect(url)``
    during application startup.

    Returns:
        The shared ``RedisCache`` instance.
    """
    global _instance
    if _instance is None:
        _instance = RedisCache()
    return _instance
