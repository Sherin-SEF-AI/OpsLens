"""WebSocket pub/sub via Redis for multi-instance broadcasting.

Replaces in-memory WebSocket broadcast with Redis pub/sub so that
multiple OpsLens instances can each serve WebSocket clients while
sharing events through a common Redis channel.

Architecture:
    1. When an incident event occurs, the handler calls
       ``publish_incident_event(event_type, data)``.
    2. The message is published to the ``ws:incidents`` Redis channel.
    3. Each OpsLens instance runs a ``listen()`` loop that reads from
       the channel and broadcasts to its local WebSocket connections.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog

from src.cache.redis_client import RedisCache, get_redis

logger = structlog.get_logger()

# Channel name for incident-related WebSocket events
WS_CHANNEL = "ws:incidents"


class RedisPubSubManager:
    """Manages Redis pub/sub for WebSocket event distribution.

    Each application instance creates one ``RedisPubSubManager`` that
    publishes events and listens for events from other instances.

    Usage::

        manager = RedisPubSubManager()
        await manager.connect()

        # Publishing side (e.g., after incident creation)
        await manager.publish_incident_event(
            "incident_created", incident.model_dump(mode="json")
        )

        # Listening side (run in background task)
        async for event in manager.listen():
            # Broadcast to local WS clients
            await broadcast_to_local_clients(event)
    """

    def __init__(self, redis: RedisCache | None = None) -> None:
        self._redis: RedisCache = redis or get_redis()
        self._connected: bool = False

    async def connect(self, redis_url: str | None = None) -> None:
        """Ensure the Redis connection is established.

        Args:
            redis_url: Optional Redis URL. If not provided, uses the
                already-connected singleton.
        """
        if redis_url:
            await self._redis.connect(redis_url)
        self._connected = True
        logger.info("ws_pubsub.connected", channel=WS_CHANNEL)

    async def close(self) -> None:
        """Disconnect from Redis pub/sub."""
        self._connected = False
        logger.info("ws_pubsub.closed")

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish_incident_event(
        self, event_type: str, data: dict[str, Any]
    ) -> None:
        """Publish an incident event to the Redis channel.

        Args:
            event_type: Event type string, e.g. ``incident_created``,
                ``incident_updated``, ``timeline_event``, ``alert_grouped``.
            data: Event payload dict.
        """
        message = {
            "type": event_type,
            "data": data,
        }
        try:
            await self._redis.publish(WS_CHANNEL, message)
            logger.debug(
                "ws_pubsub.published",
                event_type=event_type,
                channel=WS_CHANNEL,
            )
        except Exception:
            logger.exception(
                "ws_pubsub.publish_error",
                event_type=event_type,
            )

    async def publish_raw(self, message: dict[str, Any]) -> None:
        """Publish a pre-formatted message to the channel.

        Args:
            message: Complete message dict with ``type`` and ``data`` keys.
        """
        try:
            await self._redis.publish(WS_CHANNEL, message)
        except Exception:
            logger.exception("ws_pubsub.publish_raw_error")

    # ------------------------------------------------------------------
    # Listening
    # ------------------------------------------------------------------

    async def listen(self) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to the Redis channel and yield parsed events.

        Each yielded dict has the structure::

            {
                "type": "incident_created",
                "data": { ... }
            }

        This is an infinite async generator; wrap it in an asyncio task
        and cancel the task on shutdown.

        Yields:
            Parsed event dicts from the Redis channel.
        """
        logger.info("ws_pubsub.listen_started", channel=WS_CHANNEL)
        try:
            async for message in self._redis.subscribe(WS_CHANNEL):
                if not self._connected:
                    break
                yield message
        except Exception:
            logger.exception("ws_pubsub.listen_error")
        finally:
            logger.info("ws_pubsub.listen_stopped", channel=WS_CHANNEL)

    # ------------------------------------------------------------------
    # Integration helper
    # ------------------------------------------------------------------

    def create_broadcast_fn(
        self,
    ) -> Any:
        """Create a broadcast function compatible with IncidentManager.set_ws_broadcast().

        Returns:
            An async callable that publishes events to Redis.

        Usage::

            manager = RedisPubSubManager()
            await manager.connect()
            incident_manager.set_ws_broadcast(manager.create_broadcast_fn())
        """
        async def broadcast(event: dict[str, Any]) -> None:
            event_type = event.get("type", "unknown")
            data = event.get("data", event)
            await self.publish_incident_event(event_type, data)

        return broadcast
