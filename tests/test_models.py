"""Tests for Pydantic data models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.incidents.models import (
    Incident,
    IncidentStatus,
    TimelineEvent,
    TimelineEventType,
)
from src.webhooks.schemas import (
    AlertSource,
    AlertStatus,
    GenericAlert,
    ManualIncident,
    Severity,
    UnifiedAlert,
)


# ---------------------------------------------------------------------------
# Incident model
# ---------------------------------------------------------------------------

class TestIncidentModel:
    def test_creation_with_valid_data(self, sample_incident):
        assert sample_incident.incident_id == "OPSLENS-0001"
        assert sample_incident.title == "High CPU on api-server"
        assert sample_incident.severity == "P1-High"
        assert sample_incident.status == IncidentStatus.TRIGGERED
        assert sample_incident.service == "api-server"
        assert sample_incident.source == "prometheus"

    def test_default_values(self):
        inc = Incident(
            incident_id="OPSLENS-0002",
            title="Test",
            description="Test desc",
            severity="P2-Medium",
            service="svc",
            source="manual",
            triggered_at=datetime.now(timezone.utc),
        )
        assert inc.status == IncidentStatus.TRIGGERED
        assert inc.resolved_at is None
        assert inc.notion_page_id == ""
        assert inc.timeline == []
        assert inc.related_incident_ids == []
        assert inc.labels == {}
        assert inc.agent_actions_count == 0

    def test_status_enum_values(self):
        assert IncidentStatus.TRIGGERED.value == "Triggered"
        assert IncidentStatus.POSTMORTEM.value == "Postmortem"

    def test_model_dump_serialization(self, sample_incident):
        data = sample_incident.model_dump()
        assert isinstance(data, dict)
        assert data["incident_id"] == "OPSLENS-0001"
        assert data["status"] == IncidentStatus.TRIGGERED
        assert isinstance(data["labels"], dict)

    def test_model_dump_json_mode(self, sample_incident):
        data = sample_incident.model_dump(mode="json")
        assert isinstance(data["status"], str)
        assert data["status"] == "Triggered"


# ---------------------------------------------------------------------------
# TimelineEvent model
# ---------------------------------------------------------------------------

class TestTimelineEvent:
    def test_creation(self):
        event = TimelineEvent(
            timestamp=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
            event_type=TimelineEventType.CREATED,
            message="Incident created from Prometheus alert",
        )
        assert event.actor == "system"  # default
        assert event.event_type == TimelineEventType.CREATED

    def test_with_custom_actor(self):
        event = TimelineEvent(
            timestamp=datetime.now(timezone.utc),
            event_type=TimelineEventType.AGENT_TRIAGE,
            message="Triage completed",
            actor="triage-agent",
        )
        assert event.actor == "triage-agent"

    def test_all_event_types(self):
        expected_types = {
            "created", "status_change", "agent_triage", "agent_correlation",
            "agent_remediation", "agent_postmortem", "agent_comms",
            "alert_grouped", "comment", "escalation", "manual_action",
        }
        actual_types = {t.value for t in TimelineEventType}
        assert actual_types == expected_types


# ---------------------------------------------------------------------------
# UnifiedAlert model
# ---------------------------------------------------------------------------

class TestUnifiedAlert:
    def test_creation(self, sample_unified_alert):
        assert sample_unified_alert.alert_id == "test-alert-001"
        assert sample_unified_alert.severity == Severity.P1
        assert sample_unified_alert.status == AlertStatus.FIRING
        assert sample_unified_alert.source == AlertSource.MANUAL

    def test_severity_enum_values(self):
        assert Severity.P0.value == "P0-Critical"
        assert Severity.P1.value == "P1-High"
        assert Severity.P2.value == "P2-Medium"
        assert Severity.P3.value == "P3-Low"

    def test_alert_status_enum(self):
        assert AlertStatus.FIRING.value == "firing"
        assert AlertStatus.RESOLVED.value == "resolved"

    def test_alert_source_enum(self):
        sources = {s.value for s in AlertSource}
        assert sources == {"prometheus", "grafana", "pagerduty", "generic", "manual"}

    def test_model_dump(self, sample_unified_alert):
        data = sample_unified_alert.model_dump()
        assert data["alert_id"] == "test-alert-001"
        assert isinstance(data["labels"], dict)

    def test_optional_fields_default(self):
        alert = UnifiedAlert(
            alert_id="min",
            title="Minimal",
            description="Minimal alert",
            severity=Severity.P3,
            status=AlertStatus.FIRING,
            service="svc",
            source=AlertSource.GENERIC,
            triggered_at=datetime.now(timezone.utc),
        )
        assert alert.resolved_at is None
        assert alert.source_url == ""
        assert alert.labels == {}
        assert alert.raw_payload == {}


# ---------------------------------------------------------------------------
# GenericAlert / ManualIncident models
# ---------------------------------------------------------------------------

class TestGenericAlert:
    def test_defaults(self):
        alert = GenericAlert(title="Test")
        assert alert.service == "unknown"
        assert alert.severity == "P2"
        assert alert.labels == {}
        assert alert.timestamp is None

    def test_full(self):
        alert = GenericAlert(
            title="Full alert",
            description="Full description",
            severity="critical",
            service="api",
            labels={"env": "prod"},
            url="http://example.com",
            timestamp=datetime.now(timezone.utc),
        )
        assert alert.title == "Full alert"
        assert alert.labels["env"] == "prod"


class TestManualIncident:
    def test_defaults(self):
        inc = ManualIncident(title="Test")
        assert inc.service == "unknown"
        assert inc.severity == "P2"
        assert inc.description == ""

    def test_full(self):
        inc = ManualIncident(
            title="Manual",
            description="Manual desc",
            severity="P0",
            service="checkout",
            labels={"team": "infra"},
        )
        assert inc.labels["team"] == "infra"
