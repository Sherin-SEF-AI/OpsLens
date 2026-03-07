"""API response models."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class IncidentResponse(BaseModel):
    incident_id: str
    title: str
    description: str
    severity: str
    status: str
    service: str
    source: str
    triggered_at: datetime
    resolved_at: datetime | None = None
    duration_seconds: int | None = None
    notion_page_id: str = ""
    owner: str = ""
    agent_actions_count: int = 0
    labels: dict[str, str] = {}


class IncidentDetailResponse(IncidentResponse):
    timeline: list[dict[str, Any]] = []
    source_url: str = ""
    dashboard_url: str = ""
    runbook_url: str = ""
    root_cause: str = ""
    impact: str = ""
    related_incident_ids: list[str] = []
    postmortem_page_id: str = ""


class TransitionRequest(BaseModel):
    new_status: str
    reason: str = ""
    actor: str = "dashboard"


class CommentRequest(BaseModel):
    comment: str
    actor: str = "dashboard"


class StatsResponse(BaseModel):
    total: int
    active: int
    resolved: int
    mttr_by_severity: dict[str, float]
    by_severity: dict[str, int]
    by_service: dict[str, int]
