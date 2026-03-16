"""Celery tasks for inbound and outbound webhook processing."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from celery import Task
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.tasks.celery_app import celery_app

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Synchronous DB session helper (Celery tasks run in sync context)
# ---------------------------------------------------------------------------

def _get_sync_session() -> Session:
    """Create a synchronous SQLAlchemy session for Celery workers.

    Celery tasks are synchronous, so we use the sync engine variant.
    The engine reads DATABASE_URL from the same config as the async version
    but uses psycopg2 (sync) instead of asyncpg.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.config import get_config

    config = get_config()
    db_url: str = getattr(
        config,
        "DATABASE_URL",
        "postgresql+asyncpg://opslens:opslens@localhost:5432/opslens",
    )
    # Convert async URL to sync
    sync_url = db_url.replace("+asyncpg", "+psycopg2").replace("postgresql+psycopg2", "postgresql+psycopg2")
    if sync_url.startswith("postgresql://"):
        sync_url = sync_url  # already plain psycopg2
    elif "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "")

    engine = create_engine(sync_url, pool_size=5, pool_pre_ping=True)
    SyncSession = sessionmaker(bind=engine, expire_on_commit=False)
    return SyncSession()


def _get_config():
    """Lazy-load OpsLens config."""
    from src.config import get_config
    return get_config()


# ---------------------------------------------------------------------------
# Signature validation (sync version)
# ---------------------------------------------------------------------------

def _validate_signature(source: str, payload_bytes: bytes, signature: str | None) -> bool:
    """Validate webhook signature based on source type.

    Returns True if signature is valid or no secret is configured.
    """
    if not signature:
        return True

    config = _get_config()

    secret = ""
    if source == "prometheus":
        secret = config.ALERTMANAGER_SECRET
    elif source == "grafana":
        secret = config.GRAFANA_SECRET
    elif source == "pagerduty":
        secret = config.PAGERDUTY_WEBHOOK_SECRET

    if not secret:
        return True

    if source == "pagerduty":
        expected = "v1=" + hmac.new(
            secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    elif source == "grafana":
        return signature == f"Bearer {secret}"
    else:
        expected = hmac.new(
            secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)


# ---------------------------------------------------------------------------
# Alert normalization (sync wrappers)
# ---------------------------------------------------------------------------

def _normalize_payload(source: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a webhook payload to a list of UnifiedAlert dicts.

    We import normalizers and convert the result to dicts for serialization.
    """
    from src.webhooks.normalizer import (
        normalize_alertmanager,
        normalize_generic,
        normalize_grafana,
        normalize_manual,
        normalize_pagerduty,
    )
    from src.webhooks.schemas import (
        AlertManagerWebhook,
        GenericAlert,
        GrafanaWebhook,
        ManualIncident,
        PagerDutyWebhook,
    )

    alerts = []
    if source == "prometheus":
        webhook = AlertManagerWebhook(**payload)
        alerts = normalize_alertmanager(webhook)
    elif source == "grafana":
        webhook = GrafanaWebhook(**payload)
        alerts = normalize_grafana(webhook)
    elif source == "pagerduty":
        webhook = PagerDutyWebhook(**payload)
        alerts = normalize_pagerduty(webhook)
    elif source == "manual":
        incident = ManualIncident(**payload)
        alerts = normalize_manual(incident)
    else:
        alert = GenericAlert(**payload)
        alerts = normalize_generic(alert)

    return [a.model_dump(mode="json") for a in alerts]


def _check_suppression_rules(alert_dict: dict[str, Any], org_id: str | None) -> bool:
    """Check if an alert should be suppressed by any active rule.

    Returns True if the alert should be suppressed.
    """
    if not org_id:
        return False

    from src.webhooks.schemas import UnifiedAlert

    session = _get_sync_session()
    try:
        from src.database.models import ActionTypeEnum, AlertRule

        alert_obj = UnifiedAlert(**alert_dict)

        stmt = select(AlertRule).where(
            AlertRule.org_id == uuid.UUID(org_id),
            AlertRule.is_active.is_(True),
            AlertRule.action_type == ActionTypeEnum.SUPPRESS,
        )
        result = session.execute(stmt)
        rules = result.scalars().all()

        for rule in rules:
            # Simple field matching for suppression rules in sync context
            condition_config = rule.condition_config or {}
            field_name = condition_config.get("field", "")
            operator = condition_config.get("operator", "eq")
            value = condition_config.get("value")

            if not field_name or value is None:
                continue

            alert_value = getattr(alert_obj, field_name, None)
            if alert_value is None:
                continue

            alert_str = str(alert_value).lower()
            value_str = str(value).lower()

            matched = False
            if operator == "eq":
                matched = alert_str == value_str
            elif operator == "contains":
                matched = value_str in alert_str
            elif operator == "in" and isinstance(value, list):
                matched = alert_str in [str(v).lower() for v in value]

            if matched:
                logger.info(
                    "webhook_task.alert_suppressed",
                    rule_name=rule.name,
                    alert_title=alert_obj.title,
                )
                return True

        return False
    finally:
        session.close()


def _create_or_group_incident(
    alert_dict: dict[str, Any], org_id: str | None
) -> dict[str, Any]:
    """Create a new incident or group the alert into an existing one.

    Uses the database to check for deduplication by fingerprint and
    creates a DB incident record.

    Returns dict with incident_id, status, grouped.
    """
    from src.database.models import Incident as IncidentModel, IncidentStatusEnum

    fingerprint = alert_dict.get("fingerprint", "")
    title = alert_dict.get("title", "Unknown Alert")
    service = alert_dict.get("service", "unknown")
    severity = alert_dict.get("severity", "P2-Medium")
    source = alert_dict.get("source", "generic")
    description = alert_dict.get("description", "")

    session = _get_sync_session()
    try:
        # Check for existing incident with same fingerprint (dedup)
        if fingerprint:
            stmt = select(IncidentModel).where(
                IncidentModel.alert_fingerprint == fingerprint,
                IncidentModel.status.notin_([
                    IncidentStatusEnum.RESOLVED,
                    IncidentStatusEnum.POSTMORTEM,
                ]),
            )
            result = session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(
                    "webhook_task.alert_grouped",
                    fingerprint=fingerprint,
                    existing_incident=existing.incident_id,
                )
                return {
                    "incident_id": existing.incident_id,
                    "status": "grouped",
                    "grouped": True,
                }

        # Check for similar active incidents in same service (smart grouping)
        stmt = select(IncidentModel).where(
            IncidentModel.service == service,
            IncidentModel.status.notin_([
                IncidentStatusEnum.RESOLVED,
                IncidentStatusEnum.POSTMORTEM,
            ]),
        )
        result = session.execute(stmt)
        active_incidents = result.scalars().all()

        for active in active_incidents:
            # Simple title similarity check
            if active.title and title:
                words_a = set(active.title.lower().split())
                words_b = set(title.lower().split())
                if words_a and words_b:
                    overlap = len(words_a & words_b) / max(
                        len(words_a | words_b), 1
                    )
                    if overlap > 0.6:
                        logger.info(
                            "webhook_task.smart_group",
                            existing=active.incident_id,
                            similarity=round(overlap, 2),
                        )
                        return {
                            "incident_id": active.incident_id,
                            "status": "grouped",
                            "grouped": True,
                        }

        # Count existing incidents for ID generation
        count_stmt = select(IncidentModel).order_by(
            IncidentModel.created_at.desc()
        ).limit(1)
        count_result = session.execute(count_stmt)
        latest = count_result.scalar_one_or_none()

        # Generate incident ID
        counter = 1
        if latest and latest.incident_id:
            import re
            m = re.search(r"OPSLENS-(\d+)", latest.incident_id)
            if m:
                counter = int(m.group(1)) + 1

        incident_id = f"OPSLENS-{counter:04d}"

        # Create new incident record
        org_uuid = uuid.UUID(org_id) if org_id else uuid.uuid4()
        new_incident = IncidentModel(
            incident_id=incident_id,
            title=title,
            description=description[:2000] if description else None,
            status=IncidentStatusEnum.TRIGGERED,
            severity=severity,
            service=service,
            source=source,
            alert_fingerprint=fingerprint,
            org_id=org_uuid,
            metadata_=alert_dict.get("raw_payload", {}),
        )
        session.add(new_incident)
        session.commit()

        logger.info(
            "webhook_task.incident_created",
            incident_id=incident_id,
            severity=severity,
            service=service,
        )
        return {
            "incident_id": incident_id,
            "status": "created",
            "grouped": False,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="src.tasks.webhook_tasks.process_webhook",
    max_retries=3,
    soft_time_limit=30,
    time_limit=60,
    acks_late=True,
)
def process_webhook(
    self: Task,
    source: str,
    payload: dict[str, Any],
    signature: str | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    """Process an inbound webhook: validate, normalize, create/group incident.

    Args:
        source: Alert source type (prometheus, grafana, pagerduty, generic, manual).
        payload: Raw webhook payload as dict.
        signature: Optional HMAC signature for validation.
        org_id: Optional organization UUID string.

    Returns:
        Dict with incident_id, status (created/grouped/suppressed), grouped flag.
    """
    log = logger.bind(
        source=source,
        task_id=self.request.id,
    )
    log.info("webhook_task.processing")
    start = time.monotonic()

    try:
        # 1. Validate signature
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        if not _validate_signature(source, payload_bytes, signature):
            log.warning("webhook_task.invalid_signature")
            return {
                "incident_id": None,
                "status": "rejected",
                "grouped": False,
                "error": "Invalid webhook signature",
            }

        # 2. Normalize to UnifiedAlert(s)
        alert_dicts = _normalize_payload(source, payload)
        if not alert_dicts:
            log.warning("webhook_task.no_alerts_normalized")
            return {
                "incident_id": None,
                "status": "empty",
                "grouped": False,
            }

        # Process the first alert (primary); additional alerts are grouped
        primary_alert = alert_dicts[0]

        # 3. Check suppression rules
        if _check_suppression_rules(primary_alert, org_id):
            log.info("webhook_task.suppressed")
            return {
                "incident_id": None,
                "status": "suppressed",
                "grouped": False,
            }

        # 4. Create incident or group
        result = _create_or_group_incident(primary_alert, org_id)

        # Process additional alerts from the same webhook (e.g., AlertManager batch)
        for extra_alert in alert_dicts[1:]:
            if not _check_suppression_rules(extra_alert, org_id):
                _create_or_group_incident(extra_alert, org_id)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "webhook_task.completed",
            incident_id=result.get("incident_id"),
            status=result.get("status"),
            alerts_processed=len(alert_dicts),
            duration_ms=elapsed_ms,
        )
        return result

    except Exception as exc:
        log.exception("webhook_task.error", error=str(exc))
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(
    bind=True,
    name="src.tasks.webhook_tasks.process_webhook_batch",
    max_retries=3,
    soft_time_limit=60,
    time_limit=120,
    acks_late=True,
)
def process_webhook_batch(
    self: Task,
    webhooks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process multiple webhooks in a single task for batch efficiency.

    Args:
        webhooks: List of dicts, each with keys: source, payload, signature, org_id.

    Returns:
        List of result dicts, one per webhook.
    """
    log = logger.bind(task_id=self.request.id, batch_size=len(webhooks))
    log.info("webhook_batch_task.processing")
    start = time.monotonic()

    results: list[dict[str, Any]] = []
    for i, wh in enumerate(webhooks):
        try:
            result = process_webhook(
                wh.get("source", "generic"),
                wh.get("payload", {}),
                wh.get("signature"),
                wh.get("org_id"),
            )
            results.append(result)
        except Exception as exc:
            log.exception(
                "webhook_batch_task.item_error",
                index=i,
                error=str(exc),
            )
            results.append({
                "incident_id": None,
                "status": "error",
                "grouped": False,
                "error": str(exc),
            })

    elapsed_ms = int((time.monotonic() - start) * 1000)
    log.info(
        "webhook_batch_task.completed",
        processed=len(results),
        duration_ms=elapsed_ms,
    )
    return results


@celery_app.task(
    bind=True,
    name="src.tasks.webhook_tasks.deliver_outbound_webhook",
    max_retries=3,
    soft_time_limit=30,
    time_limit=60,
    acks_late=True,
)
def deliver_outbound_webhook(
    self: Task,
    subscription_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    """Deliver an outbound webhook to a subscriber with retry logic.

    Args:
        subscription_id: Webhook subscription identifier.
        event_type: Event type being delivered (e.g., incident.created).
        payload: Complete webhook payload to deliver.

    Returns:
        True if delivery succeeded, False otherwise.
    """
    log = logger.bind(
        task_id=self.request.id,
        subscription_id=subscription_id,
        event_type=event_type,
    )
    log.info("outbound_webhook.delivering")

    session = _get_sync_session()
    try:
        # Load subscription config from settings.json or in-memory config
        # For now, payload must include delivery metadata
        url = payload.pop("__delivery_url", "")
        secret = payload.pop("__delivery_secret", "")
        timeout_seconds = payload.pop("__delivery_timeout", 10)
        custom_headers = payload.pop("__delivery_headers", {})

        if not url:
            log.error("outbound_webhook.no_url")
            return False

        # Build payload JSON
        payload_json = json.dumps(payload, default=str)

        # Build headers
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "OpsLens-Webhook/1.0",
            "X-OpsLens-Event": event_type,
            "X-OpsLens-Delivery": subscription_id,
        }

        # HMAC signature
        if secret:
            sig = hmac.new(
                secret.encode(),
                payload_json.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-OpsLens-Signature"] = f"sha256={sig}"

        # Add custom headers
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)

        # Deliver
        with httpx.Client(timeout=float(timeout_seconds)) as client:
            resp = client.post(url, content=payload_json, headers=headers)

        if 200 <= resp.status_code < 300:
            log.info(
                "outbound_webhook.delivered",
                status_code=resp.status_code,
            )
            return True

        log.warning(
            "outbound_webhook.non_2xx",
            status_code=resp.status_code,
            body=resp.text[:200],
        )
        # Retry on server errors
        if resp.status_code >= 500:
            raise self.retry(
                exc=Exception(f"HTTP {resp.status_code}"),
                countdown=min(2 ** (self.request.retries + 1), 300),
            )

        return False

    except httpx.TimeoutException as exc:
        log.warning("outbound_webhook.timeout", error=str(exc))
        raise self.retry(
            exc=exc,
            countdown=min(2 ** (self.request.retries + 1), 300),
        )
    except Exception as exc:
        if "Retry" in type(exc).__name__:
            raise
        log.exception("outbound_webhook.error", error=str(exc))
        raise self.retry(
            exc=exc,
            countdown=min(2 ** (self.request.retries + 1), 300),
        )
    finally:
        session.close()
