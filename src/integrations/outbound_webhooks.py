"""Webhook-as-a-Service (Outbound) for OpsLens.

Features:
- Configurable outbound webhooks for any incident event
- Custom payload templates with variable substitution
- Event filtering (by severity, service, event type)
- Delivery tracking with retry logic
- HMAC signature for webhook security
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from src.incidents.models import Incident

logger = structlog.get_logger()


class WebhookSubscription:
    """A configured outbound webhook subscription."""

    def __init__(
        self,
        subscription_id: str,
        name: str,
        url: str,
        secret: str = "",
        events: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        payload_template: dict[str, Any] | None = None,
        enabled: bool = True,
        retry_count: int = 3,
        timeout_seconds: int = 10,
    ):
        self.subscription_id = subscription_id
        self.name = name
        self.url = url
        self.secret = secret
        self.events = events or ["*"]  # ["*"] means all events
        self.filters = filters or {}
        self.custom_headers = headers or {}
        self.payload_template = payload_template
        self.enabled = enabled
        self.retry_count = retry_count
        self.timeout_seconds = timeout_seconds
        self.created_at = datetime.now(timezone.utc)
        # Delivery stats
        self.total_sent = 0
        self.total_failed = 0
        self.last_sent_at: datetime | None = None
        self.last_error: str = ""

    def matches_event(self, event_type: str, incident: Incident | None = None) -> bool:
        """Check if this subscription should receive the given event."""
        if not self.enabled:
            return False

        # Check event type filter
        if "*" not in self.events and event_type not in self.events:
            return False

        # Check incident-based filters
        if incident and self.filters:
            # Severity filter
            if "severity" in self.filters:
                allowed = self.filters["severity"]
                if isinstance(allowed, list) and incident.severity not in allowed:
                    return False
                elif isinstance(allowed, str) and incident.severity != allowed:
                    return False

            # Service filter
            if "service" in self.filters:
                allowed = self.filters["service"]
                if isinstance(allowed, list) and incident.service not in allowed:
                    return False
                elif isinstance(allowed, str) and incident.service != allowed:
                    return False

            # Source filter
            if "source" in self.filters:
                allowed = self.filters["source"]
                if isinstance(allowed, list) and incident.source not in allowed:
                    return False
                elif isinstance(allowed, str) and incident.source != allowed:
                    return False

        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "name": self.name,
            "url": self.url,
            "secret": "****" if self.secret else "",
            "events": self.events,
            "filters": self.filters,
            "headers": self.custom_headers,
            "payload_template": self.payload_template,
            "enabled": self.enabled,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
            "stats": {
                "total_sent": self.total_sent,
                "total_failed": self.total_failed,
                "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
                "last_error": self.last_error,
            },
        }


class DeliveryRecord:
    """Record of a webhook delivery attempt."""

    def __init__(
        self,
        subscription_id: str,
        event_type: str,
        payload: dict[str, Any],
    ):
        self.subscription_id = subscription_id
        self.event_type = event_type
        self.payload = payload
        self.timestamp = datetime.now(timezone.utc)
        self.status_code: int = 0
        self.response_body: str = ""
        self.success: bool = False
        self.attempts: int = 0
        self.error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "status_code": self.status_code,
            "success": self.success,
            "attempts": self.attempts,
            "error": self.error,
        }


# All supported event types
EVENT_TYPES = [
    "incident.created",
    "incident.updated",
    "incident.resolved",
    "incident.escalated",
    "incident.status_changed",
    "incident.severity_changed",
    "alert.grouped",
    "agent.triage_completed",
    "agent.correlation_completed",
    "agent.remediation_completed",
    "agent.postmortem_completed",
    "agent.comms_completed",
    "timeline.event_added",
]


class OutboundWebhookManager:
    """Manages outbound webhook subscriptions and delivery."""

    def __init__(self):
        self._subscriptions: dict[str, WebhookSubscription] = {}
        self._delivery_history: list[DeliveryRecord] = []
        self._max_history = 1000
        self._counter = 0

    # --- Subscription Management ---

    def add_subscription(
        self,
        name: str,
        url: str,
        secret: str = "",
        events: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        payload_template: dict[str, Any] | None = None,
        enabled: bool = True,
        retry_count: int = 3,
        timeout_seconds: int = 10,
    ) -> WebhookSubscription:
        """Register a new outbound webhook subscription."""
        self._counter += 1
        sub_id = f"wh-{self._counter:04d}"

        sub = WebhookSubscription(
            subscription_id=sub_id,
            name=name,
            url=url,
            secret=secret,
            events=events,
            filters=filters,
            headers=headers,
            payload_template=payload_template,
            enabled=enabled,
            retry_count=retry_count,
            timeout_seconds=timeout_seconds,
        )

        self._subscriptions[sub_id] = sub
        logger.info("outbound_webhook_added", sub_id=sub_id, name=name, url=url)
        return sub

    def update_subscription(
        self, sub_id: str, updates: dict[str, Any]
    ) -> WebhookSubscription | None:
        """Update an existing subscription."""
        sub = self._subscriptions.get(sub_id)
        if not sub:
            return None

        for key, value in updates.items():
            if hasattr(sub, key) and key not in ("subscription_id", "created_at"):
                setattr(sub, key, value)

        logger.info("outbound_webhook_updated", sub_id=sub_id)
        return sub

    def remove_subscription(self, sub_id: str) -> bool:
        """Remove a webhook subscription."""
        if sub_id in self._subscriptions:
            del self._subscriptions[sub_id]
            logger.info("outbound_webhook_removed", sub_id=sub_id)
            return True
        return False

    def get_subscription(self, sub_id: str) -> WebhookSubscription | None:
        return self._subscriptions.get(sub_id)

    def list_subscriptions(self) -> list[dict[str, Any]]:
        return [sub.to_dict() for sub in self._subscriptions.values()]

    # --- Event Dispatch ---

    async def dispatch(
        self,
        event_type: str,
        data: dict[str, Any],
        incident: Incident | None = None,
    ) -> list[DeliveryRecord]:
        """Dispatch an event to all matching subscriptions."""
        records = []

        for sub in self._subscriptions.values():
            if not sub.matches_event(event_type, incident):
                continue

            # Build payload
            payload = self._build_payload(event_type, data, incident, sub)

            # Deliver with retries
            record = await self._deliver(sub, event_type, payload)
            records.append(record)

            # Keep delivery history
            self._delivery_history.append(record)
            if len(self._delivery_history) > self._max_history:
                self._delivery_history = self._delivery_history[-self._max_history:]

        return records

    def _build_payload(
        self,
        event_type: str,
        data: dict[str, Any],
        incident: Incident | None,
        sub: WebhookSubscription,
    ) -> dict[str, Any]:
        """Build the webhook payload, applying custom templates if configured."""

        if sub.payload_template:
            # Apply template with variable substitution
            return self._apply_template(sub.payload_template, event_type, data, incident)

        # Default payload structure
        payload: dict[str, Any] = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "opslens",
            "data": data,
        }

        if incident:
            payload["incident"] = {
                "incident_id": incident.incident_id,
                "title": incident.title,
                "severity": incident.severity,
                "status": incident.status.value,
                "service": incident.service,
                "source": incident.source,
                "triggered_at": incident.triggered_at.isoformat(),
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
                "duration_seconds": incident.duration_seconds,
                "notion_page_url": incident.notion_page_url,
                "owner": incident.owner,
                "root_cause": incident.root_cause,
            }

        return payload

    def _apply_template(
        self,
        template: dict[str, Any],
        event_type: str,
        data: dict[str, Any],
        incident: Incident | None,
    ) -> dict[str, Any]:
        """Apply a custom payload template with variable substitution.

        Variables use {{variable}} syntax:
        - {{event}} - Event type
        - {{timestamp}} - ISO timestamp
        - {{incident.id}} - Incident ID
        - {{incident.title}} - Incident title
        - {{incident.severity}} - Severity
        - {{incident.status}} - Status
        - {{incident.service}} - Service name
        - {{incident.description}} - Description
        - {{data.*}} - Access to event data fields
        """
        variables: dict[str, str] = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if incident:
            variables.update({
                "incident.id": incident.incident_id,
                "incident.title": incident.title,
                "incident.severity": incident.severity,
                "incident.status": incident.status.value,
                "incident.service": incident.service,
                "incident.source": incident.source,
                "incident.description": incident.description[:500],
                "incident.owner": incident.owner,
                "incident.root_cause": incident.root_cause,
            })

        # Flatten data dict
        for key, value in data.items():
            variables[f"data.{key}"] = str(value)

        # Apply substitution recursively
        return self._substitute(template, variables)

    def _substitute(self, obj: Any, variables: dict[str, str]) -> Any:
        """Recursively substitute {{variables}} in a template."""
        if isinstance(obj, str):
            result = obj
            for key, value in variables.items():
                result = result.replace(f"{{{{{key}}}}}", str(value))
            return result
        elif isinstance(obj, dict):
            return {k: self._substitute(v, variables) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute(item, variables) for item in obj]
        return obj

    async def _deliver(
        self,
        sub: WebhookSubscription,
        event_type: str,
        payload: dict[str, Any],
    ) -> DeliveryRecord:
        """Deliver a webhook with retry logic."""
        record = DeliveryRecord(
            subscription_id=sub.subscription_id,
            event_type=event_type,
            payload=payload,
        )

        payload_json = json.dumps(payload, default=str)

        # Build headers
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "OpsLens-Webhook/1.0",
            "X-OpsLens-Event": event_type,
            "X-OpsLens-Delivery": sub.subscription_id,
        }

        # Add HMAC signature if secret configured
        if sub.secret:
            signature = hmac.new(
                sub.secret.encode(),
                payload_json.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-OpsLens-Signature"] = f"sha256={signature}"

        # Add custom headers
        headers.update(sub.custom_headers)

        # Retry loop
        last_error = ""
        for attempt in range(1, sub.retry_count + 1):
            record.attempts = attempt
            try:
                async with httpx.AsyncClient(timeout=float(sub.timeout_seconds)) as client:
                    resp = await client.post(
                        sub.url,
                        content=payload_json,
                        headers=headers,
                    )
                    record.status_code = resp.status_code
                    record.response_body = resp.text[:500]

                    if 200 <= resp.status_code < 300:
                        record.success = True
                        sub.total_sent += 1
                        sub.last_sent_at = datetime.now(timezone.utc)
                        sub.last_error = ""

                        logger.info(
                            "outbound_webhook_delivered",
                            sub_id=sub.subscription_id,
                            event=event_type,
                            status=resp.status_code,
                        )
                        return record

                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"

            except Exception as exc:
                last_error = str(exc)

            # Exponential backoff between retries
            if attempt < sub.retry_count:
                import asyncio
                await asyncio.sleep(min(2 ** attempt, 30))

        # All retries failed
        record.error = last_error
        record.success = False
        sub.total_failed += 1
        sub.last_error = last_error

        logger.error(
            "outbound_webhook_failed",
            sub_id=sub.subscription_id,
            event=event_type,
            attempts=sub.retry_count,
            error=last_error,
        )

        return record

    # --- Delivery History ---

    def get_delivery_history(
        self,
        sub_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent delivery records, optionally filtered by subscription."""
        records = self._delivery_history
        if sub_id:
            records = [r for r in records if r.subscription_id == sub_id]
        return [r.to_dict() for r in records[-limit:]]

    # --- Test Delivery ---

    async def test_subscription(self, sub_id: str) -> dict[str, Any]:
        """Send a test webhook to a subscription."""
        sub = self._subscriptions.get(sub_id)
        if not sub:
            return {"status": "error", "message": f"Subscription {sub_id} not found"}

        test_payload = {
            "event": "test.ping",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "opslens",
            "data": {"message": "This is a test webhook from OpsLens"},
        }

        record = await self._deliver(sub, "test.ping", test_payload)
        return {
            "status": "ok" if record.success else "error",
            "status_code": record.status_code,
            "attempts": record.attempts,
            "error": record.error,
        }

    # --- Serialization ---

    def export_config(self) -> list[dict[str, Any]]:
        """Export all subscriptions for persistence."""
        result = []
        for sub in self._subscriptions.values():
            d = sub.to_dict()
            d["secret_raw"] = sub.secret  # Include real secret for persistence
            result.append(d)
        return result

    def import_config(self, configs: list[dict[str, Any]]) -> int:
        """Import subscriptions from saved config."""
        count = 0
        for cfg in configs:
            sub = WebhookSubscription(
                subscription_id=cfg.get("subscription_id", f"wh-{self._counter + 1:04d}"),
                name=cfg.get("name", ""),
                url=cfg.get("url", ""),
                secret=cfg.get("secret_raw", cfg.get("secret", "")),
                events=cfg.get("events"),
                filters=cfg.get("filters"),
                headers=cfg.get("headers"),
                payload_template=cfg.get("payload_template"),
                enabled=cfg.get("enabled", True),
                retry_count=cfg.get("retry_count", 3),
                timeout_seconds=cfg.get("timeout_seconds", 10),
            )
            self._subscriptions[sub.subscription_id] = sub
            self._counter = max(
                self._counter,
                int(sub.subscription_id.replace("wh-", "")) if sub.subscription_id.startswith("wh-") else self._counter,
            )
            count += 1

        logger.info("outbound_webhooks_imported", count=count)
        return count

    def get_supported_events(self) -> list[str]:
        """Return list of all supported event types."""
        return EVENT_TYPES.copy()
