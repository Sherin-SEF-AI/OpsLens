"""SQLAlchemy 2.0 ORM models for OpsLens."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    """Generate a time-ordered UUID (v7 when available, v4 fallback)."""
    try:
        return uuid.uuid7()  # type: ignore[attr-defined]
    except AttributeError:
        return uuid.uuid4()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    VIEWER = "viewer"
    RESPONDER = "responder"
    COMMANDER = "commander"
    ADMIN = "admin"


class AuthProvider(str, enum.Enum):
    LOCAL = "local"
    GOOGLE = "google"
    GITHUB = "github"


class IncidentStatusEnum(str, enum.Enum):
    TRIGGERED = "Triggered"
    TRIAGED = "Triaged"
    INVESTIGATING = "Investigating"
    MITIGATED = "Mitigated"
    RESOLVED = "Resolved"
    POSTMORTEM = "Postmortem"


class SeverityEnum(str, enum.Enum):
    P0 = "P0-Critical"
    P1 = "P1-High"
    P2 = "P2-Medium"
    P3 = "P3-Low"


class TimelineEventTypeEnum(str, enum.Enum):
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


class AgentTypeEnum(str, enum.Enum):
    TRIAGE = "triage"
    CORRELATION = "correlation"
    REMEDIATION = "remediation"
    POSTMORTEM = "postmortem"
    COMMS = "comms"


class ConditionTypeEnum(str, enum.Enum):
    THRESHOLD = "threshold"
    PATTERN = "pattern"
    COMPOSITE = "composite"


class ActionTypeEnum(str, enum.Enum):
    CREATE_INCIDENT = "create_incident"
    ESCALATE = "escalate"
    NOTIFY = "notify"
    SUPPRESS = "suppress"


class RotationTypeEnum(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    CUSTOM = "custom"


class SLABreachTypeEnum(str, enum.Enum):
    RESPONSE = "response"
    ACKNOWLEDGE = "acknowledge"
    RESOLUTION = "resolution"


class RunbookStatusEnum(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportTypeEnum(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Declarative base for all OpsLens models."""
    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    notion_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notion_root_page_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    settings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    alert_rules: Mapped[list["AlertRule"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    oncall_schedules: Mapped[list["OnCallSchedule"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    sla_policies: Mapped[list["SLAPolicy"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    reports: Mapped[list["IncidentReport"]] = relationship(back_populates="organization", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False),
        nullable=False,
        default=UserRole.VIEWER,
    )
    provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, name="auth_provider", native_enum=False),
        nullable=False,
        default=AuthProvider.LOCAL,
    )
    provider_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="users")
    assigned_incidents: Mapped[list["Incident"]] = relationship(back_populates="assignee")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")
    created_alert_rules: Mapped[list["AlertRule"]] = relationship(back_populates="creator")
    runbook_executions: Mapped[list["RunbookExecution"]] = relationship(back_populates="executor")

    __table_args__ = (
        Index("ix_users_org_id", "org_id"),
        Index("ix_users_provider", "provider", "provider_id"),
    )


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    incident_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[IncidentStatusEnum] = mapped_column(
        Enum(IncidentStatusEnum, name="incident_status", native_enum=False),
        nullable=False,
        default=IncidentStatusEnum.TRIGGERED,
    )
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    service: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    alert_fingerprint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notion_page_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="incidents")
    assignee: Mapped[Optional["User"]] = relationship(back_populates="assigned_incidents")
    timeline_events: Mapped[list["TimelineEvent"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="TimelineEvent.created_at"
    )
    agent_results: Mapped[list["AgentResult"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    sla_breaches: Mapped[list["SLABreach"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    runbook_executions: Mapped[list["RunbookExecution"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_severity", "severity"),
        Index("ix_incidents_org_id", "org_id"),
        Index("ix_incidents_created_at", "created_at"),
        Index("ix_incidents_service", "service"),
        Index("ix_incidents_alert_fingerprint", "alert_fingerprint"),
    )


# ---------------------------------------------------------------------------
# TimelineEvent
# ---------------------------------------------------------------------------

class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[TimelineEventTypeEnum] = mapped_column(
        Enum(TimelineEventTypeEnum, name="timeline_event_type", native_enum=False),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    incident: Mapped["Incident"] = relationship(back_populates="timeline_events")

    __table_args__ = (
        Index("ix_timeline_events_incident_id", "incident_id"),
        Index("ix_timeline_events_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

class AgentResult(Base):
    __tablename__ = "agent_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    agent_type: Mapped[AgentTypeEnum] = mapped_column(
        Enum(AgentTypeEnum, name="agent_type", native_enum=False),
        nullable=False,
    )
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tool_calls: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    incident: Mapped["Incident"] = relationship(back_populates="agent_results")

    __table_args__ = (
        Index("ix_agent_results_incident_id", "incident_id"),
    )


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=dict)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# AlertRule
# ---------------------------------------------------------------------------

class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    condition_type: Mapped[ConditionTypeEnum] = mapped_column(
        Enum(ConditionTypeEnum, name="condition_type", native_enum=False),
        nullable=False,
    )
    condition_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    action_type: Mapped[ActionTypeEnum] = mapped_column(
        Enum(ActionTypeEnum, name="action_type", native_enum=False),
        nullable=False,
    )
    action_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="alert_rules")
    creator: Mapped[Optional["User"]] = relationship(back_populates="created_alert_rules")

    __table_args__ = (
        Index("ix_alert_rules_org_id", "org_id"),
    )


# ---------------------------------------------------------------------------
# OnCallSchedule
# ---------------------------------------------------------------------------

class OnCallSchedule(Base):
    __tablename__ = "oncall_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rotation_type: Mapped[RotationTypeEnum] = mapped_column(
        Enum(RotationTypeEnum, name="rotation_type", native_enum=False),
        nullable=False,
        default=RotationTypeEnum.WEEKLY,
    )
    members: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=list)
    current_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalation_policy: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=dict)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="oncall_schedules")

    __table_args__ = (
        Index("ix_oncall_schedules_org_id", "org_id"),
        UniqueConstraint("team_name", "org_id", name="uq_oncall_team_org"),
    )


# ---------------------------------------------------------------------------
# SLAPolicy
# ---------------------------------------------------------------------------

class SLAPolicy(Base):
    __tablename__ = "sla_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    response_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    acknowledge_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="sla_policies")
    breaches: Mapped[list["SLABreach"]] = relationship(back_populates="sla_policy", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_sla_policies_org_id", "org_id"),
    )


# ---------------------------------------------------------------------------
# SLABreach
# ---------------------------------------------------------------------------

class SLABreach(Base):
    __tablename__ = "sla_breaches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    sla_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sla_policies.id", ondelete="CASCADE"), nullable=False
    )
    breach_type: Mapped[SLABreachTypeEnum] = mapped_column(
        Enum(SLABreachTypeEnum, name="sla_breach_type", native_enum=False),
        nullable=False,
    )
    breached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    incident: Mapped["Incident"] = relationship(back_populates="sla_breaches")
    sla_policy: Mapped["SLAPolicy"] = relationship(back_populates="breaches")

    __table_args__ = (
        Index("ix_sla_breaches_incident_id", "incident_id"),
    )


# ---------------------------------------------------------------------------
# RunbookExecution
# ---------------------------------------------------------------------------

class RunbookExecution(Base):
    __tablename__ = "runbook_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    runbook_name: Mapped[str] = mapped_column(String(255), nullable=False)
    runbook_notion_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[RunbookStatusEnum] = mapped_column(
        Enum(RunbookStatusEnum, name="runbook_status", native_enum=False),
        nullable=False,
        default=RunbookStatusEnum.PENDING,
    )
    steps_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steps_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    executed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    output: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    incident: Mapped["Incident"] = relationship(back_populates="runbook_executions")
    executor: Mapped[Optional["User"]] = relationship(back_populates="runbook_executions")

    __table_args__ = (
        Index("ix_runbook_executions_incident_id", "incident_id"),
    )


# ---------------------------------------------------------------------------
# IncidentReport
# ---------------------------------------------------------------------------

class IncidentReport(Base):
    __tablename__ = "incident_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid
    )
    report_type: Mapped[ReportTypeEnum] = mapped_column(
        Enum(ReportTypeEnum, name="report_type", native_enum=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=dict)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    generated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="reports")

    __table_args__ = (
        Index("ix_incident_reports_org_id", "org_id"),
        Index("ix_incident_reports_created_at", "created_at"),
    )
