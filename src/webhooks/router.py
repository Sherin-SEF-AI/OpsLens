"""FastAPI webhook endpoints for alert ingestion."""

import asyncio
import time

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

# Circuit breaker for webhook -> incident creation (optional, graceful)
_webhook_circuit = None
try:
    from src.security.circuit_breaker import CircuitBreaker, CircuitOpenError
    _webhook_circuit = CircuitBreaker(
        name="webhook_incident_creation",
        failure_threshold=10,
        recovery_timeout=30.0,
    )
except ImportError:
    pass


def set_incident_handler(handler) -> None:
    """Set the function that processes normalized alerts into incidents."""
    global _incident_handler
    _incident_handler = handler


def _track_webhook(source: str, status: str, latency: float) -> None:
    """Best-effort metrics tracking for webhook processing."""
    try:
        from src.observability.metrics import track_webhook
        track_webhook(source, status, latency)
    except Exception:
        pass


def _try_celery_dispatch(alerts: list[UnifiedAlert]) -> bool:
    """Try to dispatch alert processing to Celery. Returns True if dispatched."""
    try:
        from src.tasks.worker import process_alerts_task
        for alert in alerts:
            process_alerts_task.delay(alert.model_dump(mode="json"))
        return True
    except ImportError:
        return False
    except Exception:
        logger.debug("celery_dispatch_failed", hint="Falling back to direct processing")
        return False


async def _process_alerts(alerts: list[UnifiedAlert]) -> None:
    """Process normalized alerts through the incident manager."""
    if _incident_handler is None:
        logger.error("no_incident_handler", msg="Incident handler not configured")
        return
    for alert in alerts:
        start = time.perf_counter()
        try:
            if _webhook_circuit is not None:
                await _webhook_circuit.call(_incident_handler, alert)
            else:
                await _incident_handler(alert)
            _track_webhook(alert.source.value, "success", time.perf_counter() - start)
        except Exception as exc:
            _track_webhook(alert.source.value, "error", time.perf_counter() - start)
            # Check if it's a circuit open error
            if _webhook_circuit is not None:
                try:
                    from src.security.circuit_breaker import CircuitOpenError
                    if isinstance(exc, CircuitOpenError):
                        logger.warning(
                            "webhook_circuit_open",
                            alert_id=alert.alert_id,
                            recovery_remaining=exc.recovery_remaining,
                        )
                        continue
                except ImportError:
                    pass
            logger.exception("alert_processing_error", alert_id=alert.alert_id)


async def _dispatch_alerts(source: str, alerts: list[UnifiedAlert], background_tasks: BackgroundTasks) -> None:
    """Dispatch alerts to Celery if available, otherwise use background tasks."""
    if not _try_celery_dispatch(alerts):
        background_tasks.add_task(_process_alerts, alerts)


@router.post("/alertmanager", status_code=202)
async def webhook_alertmanager(
    request: Request, background_tasks: BackgroundTasks
):
    """Receive Prometheus AlertManager webhooks."""
    start = time.perf_counter()
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
    await _dispatch_alerts("alertmanager", alerts, background_tasks)
    _track_webhook("alertmanager", "accepted", time.perf_counter() - start)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/grafana", status_code=202)
async def webhook_grafana(
    request: Request, background_tasks: BackgroundTasks
):
    """Receive Grafana Alerting webhooks."""
    start = time.perf_counter()
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
    await _dispatch_alerts("grafana", alerts, background_tasks)
    _track_webhook("grafana", "accepted", time.perf_counter() - start)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/pagerduty", status_code=202)
async def webhook_pagerduty(
    request: Request, background_tasks: BackgroundTasks
):
    """Receive PagerDuty v3 webhooks."""
    start = time.perf_counter()
    config: OpsLensConfig = request.app.state.config
    body = await validate_pagerduty(request, config)
    webhook = PagerDutyWebhook.model_validate(orjson.loads(body))

    logger.info(
        "webhook_received",
        source="pagerduty",
        event_type=webhook.event.event_type,
    )

    alerts = normalize_pagerduty(webhook)
    await _dispatch_alerts("pagerduty", alerts, background_tasks)
    _track_webhook("pagerduty", "accepted", time.perf_counter() - start)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/generic", status_code=202)
async def webhook_generic(
    alert: GenericAlert, background_tasks: BackgroundTasks
):
    """Receive generic JSON alert payloads."""
    start = time.perf_counter()
    logger.info(
        "webhook_received",
        source="generic",
        title=alert.title,
    )

    alerts = normalize_generic(alert)
    await _dispatch_alerts("generic", alerts, background_tasks)
    _track_webhook("generic", "accepted", time.perf_counter() - start)
    return {"status": "accepted", "alerts": len(alerts)}


@router.post("/manual", status_code=202)
async def webhook_manual(
    incident: ManualIncident, background_tasks: BackgroundTasks
):
    """Manually create an incident from the dashboard."""
    start = time.perf_counter()
    logger.info(
        "webhook_received",
        source="manual",
        title=incident.title,
    )

    alerts = normalize_manual(incident)
    await _dispatch_alerts("manual", alerts, background_tasks)
    _track_webhook("manual", "accepted", time.perf_counter() - start)
    return {"status": "accepted", "alerts": len(alerts)}
