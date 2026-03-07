"""Slack webhook notifications for incidents."""

import httpx
import structlog

from src.incidents.models import Incident

logger = structlog.get_logger()

SEVERITY_COLORS = {
    "P0-Critical": "#FF0000",
    "P1-High": "#FF8C00",
    "P2-Medium": "#FFD700",
    "P3-Low": "#4169E1",
}


async def send_slack_notification(
    webhook_url: str,
    channel: str,
    incident: Incident,
    notify_type: str = "created",
) -> None:
    """Send an incident notification to Slack via webhook."""
    if not webhook_url:
        return

    color = SEVERITY_COLORS.get(incident.severity, "#808080")

    if notify_type == "created":
        title = f"New Incident: [{incident.incident_id}] {incident.title}"
        text = (
            f"*Severity:* {incident.severity}\n"
            f"*Service:* {incident.service}\n"
            f"*Source:* {incident.source}\n"
            f"*Description:* {incident.description[:200]}"
        )
    elif notify_type == "resolved":
        title = f"Resolved: [{incident.incident_id}] {incident.title}"
        duration = ""
        if incident.duration_seconds:
            mins = incident.duration_seconds // 60
            duration = f" (Duration: {mins}m)"
        text = f"*Service:* {incident.service}{duration}"
    else:
        title = f"[{incident.incident_id}] {incident.title} - {notify_type}"
        text = f"*Status:* {incident.status.value}"

    payload = {
        "text": title,
        "attachments": [
            {
                "color": color,
                "text": text,
                "footer": "OpsLens Incident Response",
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
        logger.info(
            "slack_notification_sent",
            incident_id=incident.incident_id,
            notify_type=notify_type,
        )
    except Exception as exc:
        logger.error(
            "slack_notification_error",
            incident_id=incident.incident_id,
            exc_info=str(exc),
        )
