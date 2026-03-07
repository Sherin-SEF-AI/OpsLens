"""FastAPI webhook endpoints for alert ingestion."""

import asyncio

import orjson
import structlog
from fastapi import APIRouter, BackgroundTasks, Request

from src.config import OpsLensConfig
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
    UnifiedAlert,
)
from src.webhooks.validator import (
    validate_alertmanager,
    validate_grafana,
    validate_pagerduty,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# This will be set by main.py when the app starts
_incident_handler = None


def set_incident_handler(handler) -> None:
    """Set the function that processes normalized alerts into incidents."""
    global _incident_handler
    _incident_handler = handler


async def _process_alerts(alerts: list[UnifiedAlert]) -> None:
    """Process normalized alerts through the incident manager."""
    if _incident_handler is None:
        logger.error("no_incident_handler", msg="Incident handler not configured")
        return
    for alert in alerts:
        try:
            await _incident_handler(alert)
        except Exception:
            logger.exception("alert_processing_error", alert_id=alert.alert_id)


@router.post("/alertmanager", status_code=202)
async def webhook_alertmanager(
    request: Request, background_tasks: BackgroundTasks
):
    """Receive Prometheus AlertManager webhooks."""
    config: OpsLensConfig = request.app.state.config
    body = await validate_alertmanager(request, config)
    webhook = AlertManagerWebhook.model_validate(orjson.loads(body))

    logger.info(
        "webhook_received",
        source="alertmanager",
        status=webhook.status,
        alert_count=len(webhook.alerts),
    )

    alerts = normalize_alertmanager(webhook)
    background_tasks.add_task(_process_alerts, alerts)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/grafana", status_code=202)
async def webhook_grafana(
    request: Request, background_tasks: BackgroundTasks
):
    """Receive Grafana Alerting webhooks."""
    config: OpsLensConfig = request.app.state.config
    body = await validate_grafana(request, config)
    webhook = GrafanaWebhook.model_validate(orjson.loads(body))

    logger.info(
        "webhook_received",
        source="grafana",
        status=webhook.status,
        alert_count=len(webhook.alerts),
    )

    alerts = normalize_grafana(webhook)
    background_tasks.add_task(_process_alerts, alerts)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/pagerduty", status_code=202)
async def webhook_pagerduty(
    request: Request, background_tasks: BackgroundTasks
):
    """Receive PagerDuty v3 webhooks."""
    config: OpsLensConfig = request.app.state.config
    body = await validate_pagerduty(request, config)
    webhook = PagerDutyWebhook.model_validate(orjson.loads(body))

    logger.info(
        "webhook_received",
        source="pagerduty",
        event_type=webhook.event.event_type,
    )

    alerts = normalize_pagerduty(webhook)
    background_tasks.add_task(_process_alerts, alerts)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/generic", status_code=202)
async def webhook_generic(
    alert: GenericAlert, background_tasks: BackgroundTasks
):
    """Receive generic JSON alert payloads."""
    logger.info(
        "webhook_received",
        source="generic",
        title=alert.title,
    )

    alerts = normalize_generic(alert)
    background_tasks.add_task(_process_alerts, alerts)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/manual", status_code=202)
async def webhook_manual(
    incident: ManualIncident, background_tasks: BackgroundTasks
):
    """Manually create an incident from the dashboard."""
    logger.info(
        "webhook_received",
        source="manual",
        title=incident.title,
    )

    alerts = normalize_manual(incident)
    background_tasks.add_task(_process_alerts, alerts)
    return {"status": "accepted", "alerts": len(alerts)}
