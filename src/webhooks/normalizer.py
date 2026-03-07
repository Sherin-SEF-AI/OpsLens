"""Normalize all alert source formats into UnifiedAlert."""

import hashlib
from datetime import datetime, timezone

from src.webhooks.schemas import (
    AlertManagerWebhook,
    AlertSource,
    AlertStatus,
    GenericAlert,
    GrafanaWebhook,
    ManualIncident,
    PagerDutyWebhook,
    Severity,
    UnifiedAlert,
)


SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.P0,
    "high": Severity.P1,
    "warning": Severity.P1,
    "medium": Severity.P2,
    "info": Severity.P3,
    "low": Severity.P3,
    "none": Severity.P3,
    "p0": Severity.P0,
    "p1": Severity.P1,
    "p2": Severity.P2,
    "p3": Severity.P3,
}


def _make_fingerprint(service: str, title: str, source: str) -> str:
    raw = f"{service}|{title}|{source}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _map_severity(raw: str) -> Severity:
    return SEVERITY_MAP.get(raw.lower().strip(), Severity.P2)


def normalize_alertmanager(webhook: AlertManagerWebhook) -> list[UnifiedAlert]:
    """Normalize Prometheus AlertManager webhook into UnifiedAlerts."""
    alerts: list[UnifiedAlert] = []
    for alert in webhook.alerts:
        title = alert.annotations.get(
            "summary", alert.labels.get("alertname", "Unknown Alert")
        )
        service = alert.labels.get(
            "service", alert.labels.get("job", "unknown")
        )
        severity_raw = alert.labels.get("severity", "medium")
        fingerprint = alert.fingerprint or _make_fingerprint(
            service, title, "prometheus"
        )

        alerts.append(
            UnifiedAlert(
                alert_id=fingerprint,
                title=title,
                description=alert.annotations.get("description", title),
                severity=_map_severity(severity_raw),
                status=AlertStatus(alert.status),
                service=service,
                source=AlertSource.PROMETHEUS,
                labels=alert.labels,
                annotations=alert.annotations,
                triggered_at=alert.startsAt,
                resolved_at=alert.endsAt if alert.status == "resolved" else None,
                source_url=alert.generatorURL,
                dashboard_url=alert.annotations.get("dashboard_url", ""),
                runbook_url=alert.annotations.get("runbook_url", ""),
                raw_payload=webhook.model_dump(mode="json"),
                fingerprint=fingerprint,
            )
        )
    return alerts


def normalize_grafana(webhook: GrafanaWebhook) -> list[UnifiedAlert]:
    """Normalize Grafana webhook into UnifiedAlerts."""
    alerts: list[UnifiedAlert] = []
    for alert in webhook.alerts:
        title = alert.annotations.get(
            "summary", alert.labels.get("alertname", webhook.title or "Grafana Alert")
        )
        service = alert.labels.get("service", "unknown")
        severity_raw = alert.labels.get("severity", "medium")
        fingerprint = alert.fingerprint or _make_fingerprint(
            service, title, "grafana"
        )

        alerts.append(
            UnifiedAlert(
                alert_id=fingerprint,
                title=title,
                description=alert.annotations.get(
                    "description", webhook.message or title
                ),
                severity=_map_severity(severity_raw),
                status=AlertStatus(alert.status),
                service=service,
                source=AlertSource.GRAFANA,
                labels=alert.labels,
                annotations=alert.annotations,
                triggered_at=alert.startsAt,
                resolved_at=alert.endsAt if alert.status == "resolved" else None,
                source_url=alert.generatorURL,
                dashboard_url=alert.dashboardURL,
                runbook_url=alert.annotations.get("runbook_url", ""),
                raw_payload=webhook.model_dump(mode="json"),
                fingerprint=fingerprint,
            )
        )
    return alerts


def normalize_pagerduty(webhook: PagerDutyWebhook) -> list[UnifiedAlert]:
    """Normalize PagerDuty webhook into UnifiedAlerts."""
    event = webhook.event
    data = event.data

    # Map PagerDuty event types to alert status
    status = AlertStatus.FIRING
    if "resolved" in event.event_type:
        status = AlertStatus.RESOLVED

    # Map urgency to severity
    urgency = data.get("urgency", "low")
    severity = Severity.P1 if urgency == "high" else Severity.P2

    title = data.get("title", data.get("summary", "PagerDuty Incident"))
    service_info = data.get("service", {})
    service = service_info.get("name", "unknown") if isinstance(service_info, dict) else "unknown"

    fingerprint = _make_fingerprint(service, title, "pagerduty")

    return [
        UnifiedAlert(
            alert_id=event.id,
            title=title,
            description=data.get("description", title),
            severity=severity,
            status=status,
            service=service,
            source=AlertSource.PAGERDUTY,
            labels={"urgency": urgency, "event_type": event.event_type},
            annotations={},
            triggered_at=event.occurred_at,
            resolved_at=event.occurred_at if status == AlertStatus.RESOLVED else None,
            source_url=data.get("html_url", ""),
            dashboard_url="",
            runbook_url="",
            raw_payload=webhook.model_dump(mode="json"),
            fingerprint=fingerprint,
        )
    ]


def normalize_generic(alert: GenericAlert) -> list[UnifiedAlert]:
    """Normalize generic alert into UnifiedAlert."""
    title = alert.title
    service = alert.service
    fingerprint = _make_fingerprint(service, title, "generic")
    triggered_at = alert.timestamp or datetime.now(timezone.utc)

    return [
        UnifiedAlert(
            alert_id=fingerprint,
            title=title,
            description=alert.description or title,
            severity=_map_severity(alert.severity),
            status=AlertStatus.FIRING,
            service=service,
            source=AlertSource.GENERIC,
            labels=alert.labels,
            annotations={},
            triggered_at=triggered_at,
            source_url=alert.url,
            raw_payload=alert.model_dump(mode="json"),
            fingerprint=fingerprint,
        )
    ]


def normalize_manual(incident: ManualIncident) -> list[UnifiedAlert]:
    """Normalize manual incident creation into UnifiedAlert."""
    title = incident.title
    service = incident.service
    fingerprint = _make_fingerprint(service, title, "manual")

    return [
        UnifiedAlert(
            alert_id=fingerprint,
            title=title,
            description=incident.description or title,
            severity=_map_severity(incident.severity),
            status=AlertStatus.FIRING,
            service=service,
            source=AlertSource.MANUAL,
            labels=incident.labels,
            annotations={},
            triggered_at=datetime.now(timezone.utc),
            raw_payload=incident.model_dump(mode="json"),
            fingerprint=fingerprint,
        )
    ]
