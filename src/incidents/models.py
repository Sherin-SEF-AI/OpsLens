"""Incident data models and enums."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    TRIGGERED = "Triggered"
    TRIAGED = "Triaged"
    INVESTIGATING = "Investigating"
    MITIGATED = "Mitigated"
    RESOLVED = "Resolved"
    POSTMORTEM = "Postmortem"


class TimelineEventType(str, Enum):
    CREATED = "created"
    STATUS_CHANGE = "status_change"
    AGENT_TRIAGE = "agent_triage"
    AGENT_CORRELATION = "agent_correlation"
    AGENT_REMEDIATION = "agent_remediation"
    AGENT_POSTMORTEM = "agent_postmortem"
    AGENT_COMMS = "agent_comms"
    ALERT_GROUPED = "alert_grouped"
    COMMENT = "comment"
    ESCALATION = "escalation"
    MANUAL_ACTION = "manual_action"


class TimelineEvent(BaseModel):
    """A single event in the incident timeline."""
    timestamp: datetime
    event_type: TimelineEventType
    message: str
    actor: str = "system"


class Incident(BaseModel):
    """In-memory incident representation, synchronized with Notion."""
    incident_id: str  # OPSLENS-XXXX
    title: str
    description: str
    severity: str  # P0-Critical, P1-High, etc.
    status: IncidentStatus = IncidentStatus.TRIGGERED
    service: str
    source: str
    triggered_at: datetime
    resolved_at: datetime | None = None
    duration_seconds: int | None = None
    notion_page_id: str = ""
    notion_page_url: str = ""
    fingerprint: str = ""
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    source_url: str = ""
    dashboard_url: str = ""
    runbook_url: str = ""
    root_cause: str = ""
    impact: str = ""
    owner: str = ""
    timeline: list[TimelineEvent] = []
    related_incident_ids: list[str] = []
    agent_actions_count: int = 0
    postmortem_page_id: str = ""
    raw_alert: dict[str, Any] = {}
