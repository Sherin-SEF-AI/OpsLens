"""Shared pytest fixtures for OpsLens test suite."""

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure env vars are set BEFORE any OpsLens imports touch config
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("OPSLENS_ENCRYPTION_KEY", "")
os.environ.setdefault("NOTION_TOKEN", "fake-token")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://opslens:opslens@localhost:5432/opslens_test")

from src.incidents.models import Incident, IncidentStatus, TimelineEvent, TimelineEventType
from src.webhooks.schemas import (
    AlertManagerAlert,
    AlertManagerWebhook,
    AlertSource,
    AlertStatus,
    GenericAlert,
    GrafanaAlert,
    GrafanaWebhook,
    ManualIncident,
    PagerDutyEvent,
    PagerDutyWebhook,
    Severity,
    UnifiedAlert,
)


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_incident() -> Incident:
    """A fully populated incident for testing."""
    return Incident(
        incident_id="OPSLENS-0001",
        title="High CPU on api-server",
        description="CPU usage exceeded 95% on api-server-01",
        severity="P1-High",
        status=IncidentStatus.TRIGGERED,
        service="api-server",
        source="prometheus",
        triggered_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        fingerprint="abc123",
        labels={"env": "production", "region": "us-east-1"},
        annotations={"summary": "High CPU", "runbook_url": "https://runbook.example.com/cpu"},
    )


@pytest.fixture
def sample_user_data() -> dict:
    """JWT claims for a test user."""
    return {
        "sub": str(uuid.uuid4()),
        "email": "testuser@opslens.dev",
        "role": "admin",
        "org_id": str(uuid.uuid4()),
    }


@pytest.fixture
def sample_alertmanager_webhook() -> AlertManagerWebhook:
    """A realistic AlertManager webhook payload."""
    return AlertManagerWebhook(
        status="firing",
        alerts=[
            AlertManagerAlert(
                status="firing",
                labels={
                    "alertname": "HighCPU",
                    "severity": "critical",
                    "service": "api-server",
                    "job": "node-exporter",
                },
                annotations={
                    "summary": "CPU usage is above 95%",
                    "description": "api-server-01 has had CPU > 95% for 5 minutes",
                    "dashboard_url": "https://grafana.example.com/d/cpu",
                    "runbook_url": "https://wiki.example.com/runbooks/cpu",
                },
                startsAt=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                generatorURL="http://prometheus:9090/graph?g0.expr=cpu",
                fingerprint="prom-fp-001",
            )
        ],
    )


@pytest.fixture
def sample_grafana_webhook() -> GrafanaWebhook:
    """A realistic Grafana webhook payload."""
    return GrafanaWebhook(
        status="firing",
        title="[FIRING] HighMemory",
        message="Memory usage is above 90%",
        alerts=[
            GrafanaAlert(
                status="firing",
                labels={
                    "alertname": "HighMemory",
                    "severity": "warning",
                    "service": "web-frontend",
                },
                annotations={
                    "summary": "Memory above 90%",
                    "description": "web-frontend is running low on memory",
                },
                startsAt=datetime(2026, 3, 15, 11, 0, 0, tzinfo=timezone.utc),
                dashboardURL="https://grafana.example.com/d/mem",
                fingerprint="graf-fp-001",
            )
        ],
    )


@pytest.fixture
def sample_pagerduty_webhook() -> PagerDutyWebhook:
    """A realistic PagerDuty webhook payload."""
    return PagerDutyWebhook(
        event=PagerDutyEvent(
            id="pd-evt-001",
            event_type="incident.triggered",
            occurred_at=datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc),
            data={
                "title": "Database connection pool exhausted",
                "description": "PostgreSQL connection pool is at 100%",
                "urgency": "high",
                "service": {"name": "database", "id": "PDB123"},
                "html_url": "https://pagerduty.com/incidents/PDB123",
            },
        )
    )


@pytest.fixture
def sample_generic_alert() -> GenericAlert:
    """A generic alert payload."""
    return GenericAlert(
        title="Disk space low",
        description="Root partition is 95% full on storage-01",
        severity="high",
        service="storage",
        labels={"host": "storage-01", "mount": "/"},
        url="https://monitor.example.com/disk",
        timestamp=datetime(2026, 3, 15, 13, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_manual_incident() -> ManualIncident:
    """A manual incident creation payload."""
    return ManualIncident(
        title="Customer reports slow checkout",
        description="Multiple customers are reporting timeouts during checkout",
        severity="P1",
        service="checkout-service",
        labels={"reported_by": "support-team"},
    )


@pytest.fixture
def sample_unified_alert() -> UnifiedAlert:
    """A unified alert object."""
    return UnifiedAlert(
        alert_id="test-alert-001",
        title="Test Alert",
        description="This is a test alert",
        severity=Severity.P1,
        status=AlertStatus.FIRING,
        service="test-service",
        source=AlertSource.MANUAL,
        labels={"env": "test"},
        triggered_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        fingerprint="test-fp-001",
    )


# ---------------------------------------------------------------------------
# Mock MCP client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mcp_client() -> MagicMock:
    """A mocked NotionMCPClient that returns sensible defaults."""
    client = MagicMock()
    client.initialize = AsyncMock()
    client.close = AsyncMock()
    client._initialized = True
    client.call_tool = AsyncMock(return_value={"success": True})
    return client


# ---------------------------------------------------------------------------
# Temporary settings file fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_settings_file(tmp_path):
    """Create a temporary settings.json file."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "slack": {"webhook_url": "", "channel": "#test"},
        "github": {"token": "", "org": ""},
        "ai": {"llm_provider": "gemini"},
    }))
    return str(settings_path)
