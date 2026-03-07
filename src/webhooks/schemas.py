"""Pydantic models for webhook payloads from each alert source."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Enums ---

class Severity(str, Enum):
    P0 = "P0-Critical"
    P1 = "P1-High"
    P2 = "P2-Medium"
    P3 = "P3-Low"


class AlertStatus(str, Enum):
    FIRING = "firing"
    RESOLVED = "resolved"


class AlertSource(str, Enum):
    PROMETHEUS = "prometheus"
    GRAFANA = "grafana"
    PAGERDUTY = "pagerduty"
    GENERIC = "generic"
    MANUAL = "manual"


# --- Prometheus AlertManager ---

class AlertManagerAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str = ""


class AlertManagerWebhook(BaseModel):
    version: str = "4"
    groupKey: str = ""
    truncatedAlerts: int = 0
    status: Literal["firing", "resolved"]
    receiver: str = ""
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: str = ""
    alerts: list[AlertManagerAlert]


# --- Grafana Alerting ---

class GrafanaAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str = ""
    silenceURL: str = ""
    dashboardURL: str = ""
    panelURL: str = ""
    values: dict[str, Any] = {}


class GrafanaWebhook(BaseModel):
    receiver: str = ""
    status: Literal["firing", "resolved"]
    orgId: int = 0
    alerts: list[GrafanaAlert]
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: str = ""
    version: str = "1"
    groupKey: str = ""
    truncatedAlerts: int = 0
    title: str = ""
    state: str = ""
    message: str = ""


# --- PagerDuty ---

class PagerDutyEvent(BaseModel):
    id: str
    event_type: str
    resource_type: str = ""
    occurred_at: datetime
    agent: dict[str, Any] | None = None
    client: dict[str, Any] | None = None
    data: dict[str, Any] = {}


class PagerDutyWebhook(BaseModel):
    event: PagerDutyEvent


# --- Generic ---

class GenericAlert(BaseModel):
    title: str
    description: str = ""
    severity: str = "P2"
    service: str = "unknown"
    source: str = "generic"
    labels: dict[str, str] = {}
    url: str = ""
    timestamp: datetime | None = None


# --- Manual ---

class ManualIncident(BaseModel):
    title: str
    description: str = ""
    severity: str = "P2"
    service: str = "unknown"
    labels: dict[str, str] = {}


# --- Unified Alert (canonical format) ---

class UnifiedAlert(BaseModel):
    """Canonical alert format used throughout OpsLens."""
    alert_id: str
    title: str
    description: str
    severity: Severity
    status: AlertStatus
    service: str
    source: AlertSource
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    triggered_at: datetime
    resolved_at: datetime | None = None
    source_url: str = ""
    dashboard_url: str = ""
    runbook_url: str = ""
    raw_payload: dict[str, Any] = {}
    fingerprint: str = ""
