"""Integration tests for OpsLens API endpoints.

Uses a minimal FastAPI app with mocked dependencies to test endpoint
behavior without needing MCP, Notion, or LLM connections.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.incidents.manager import IncidentManager
from src.incidents.models import Incident, IncidentStatus
from src.webhooks.schemas import AlertSource, AlertStatus, Severity, UnifiedAlert


# ---------------------------------------------------------------------------
# Fixture: minimal app with mocked manager
# ---------------------------------------------------------------------------

def _make_sample_incident(
    incident_id: str = "OPSLENS-0001",
    title: str = "CPU spike on api-server",
    status: IncidentStatus = IncidentStatus.TRIGGERED,
) -> Incident:
    return Incident(
        incident_id=incident_id,
        title=title,
        description="CPU exceeds 95%",
        severity="P1-High",
        status=status,
        service="api-server",
        source="prometheus",
        triggered_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        fingerprint="fp001",
    )


@pytest.fixture
def test_app():
    """Build a minimal FastAPI app that mirrors main.py wiring but skips
    lifespan / external dependencies."""
    from src.api.router import router as api_router, set_dependencies, set_alert_handler
    from src.webhooks.router import router as webhook_router, set_incident_handler

    app = FastAPI()
    app.include_router(api_router)
    app.include_router(webhook_router)

    # Store config on app.state so webhook validators can access it
    from src.config import OpsLensConfig
    app.state.config = OpsLensConfig(
        ALERTMANAGER_SECRET="",
        GRAFANA_SECRET="",
        PAGERDUTY_WEBHOOK_SECRET="",
    )

    # Mock incident manager
    mgr = MagicMock(spec=IncidentManager)

    inc1 = _make_sample_incident("OPSLENS-0001", "CPU spike", IncidentStatus.TRIGGERED)
    inc2 = _make_sample_incident("OPSLENS-0002", "Disk full", IncidentStatus.RESOLVED)

    mgr.get_all_incidents.return_value = [inc1, inc2]
    mgr.get_active_incidents.return_value = [inc1]
    mgr.get_incident.side_effect = lambda iid: (
        inc1 if iid == "OPSLENS-0001"
        else inc2 if iid == "OPSLENS-0002"
        else None
    )
    mgr.get_stats.return_value = {
        "total": 2,
        "active": 1,
        "resolved": 1,
        "by_severity": {"P1-High": 2},
        "by_status": {"Triggered": 1, "Resolved": 1},
        "by_service": {"api-server": 2},
        "mttr_seconds": 0,
        "mttr_by_severity": {"P1-High": 0.0},
        "p0_count": 0,
        "p1_count": 2,
    }
    mgr.create_incident = AsyncMock(return_value=inc1)

    set_dependencies(mgr)

    # Wire the alert handler as the webhook router expects
    async def _mock_alert_handler(alert):
        await mgr.create_incident(alert)

    set_incident_handler(_mock_alert_handler)
    set_alert_handler(_mock_alert_handler)

    # Health endpoint (add directly to avoid lifespan)
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "mcp_connected": False,
            "active_incidents": 1,
        }

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "active_incidents" in data


# ---------------------------------------------------------------------------
# Incidents list
# ---------------------------------------------------------------------------

class TestListIncidents:
    def test_list_all(self, client):
        resp = client.get("/api/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_list_active(self, client):
        resp = client.get("/api/incidents/active")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["incident_id"] == "OPSLENS-0001"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats(self, client):
        resp = client.get("/api/incidents/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["active"] == 1


# ---------------------------------------------------------------------------
# Get single incident
# ---------------------------------------------------------------------------

class TestGetIncident:
    def test_existing_incident(self, client):
        resp = client.get("/api/incidents/OPSLENS-0001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incident_id"] == "OPSLENS-0001"

    def test_nonexistent_returns_404(self, client):
        resp = client.get("/api/incidents/OPSLENS-9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Manual webhook
# ---------------------------------------------------------------------------

class TestManualWebhook:
    def test_manual_creates_incident(self, client):
        resp = client.post(
            "/webhooks/manual",
            json={
                "title": "Manual test incident",
                "description": "Testing manual creation",
                "severity": "P2",
                "service": "test-service",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["alerts"] == 1


# ---------------------------------------------------------------------------
# Playground endpoints
# ---------------------------------------------------------------------------

class TestPlayground:
    def test_playground_test_normalizes(self, client):
        resp = client.post(
            "/api/playground/test",
            json={
                "source": "manual",
                "payload": {
                    "title": "Test playground",
                    "severity": "P1",
                    "service": "playground-svc",
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["alerts_parsed"] == 1
        assert data["validation"] == "passed"
        alert = data["alerts"][0]
        assert alert["title"] == "Test playground"
        assert alert["service"] == "playground-svc"

    def test_playground_test_invalid_source(self, client):
        resp = client.post(
            "/api/playground/test",
            json={
                "source": "invalid_source",
                "payload": {"title": "x"},
            },
        )
        assert resp.status_code == 400

    def test_playground_send_creates_incident(self, client):
        resp = client.post(
            "/api/playground/send",
            json={
                "source": "manual",
                "payload": {
                    "title": "Live send test",
                    "severity": "P2",
                    "service": "test",
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["alerts"] == 1


# ---------------------------------------------------------------------------
# Generic webhook
# ---------------------------------------------------------------------------

class TestGenericWebhook:
    def test_generic_webhook(self, client):
        resp = client.post(
            "/webhooks/generic",
            json={
                "title": "Generic test",
                "description": "A generic alert",
                "severity": "warning",
                "service": "my-service",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
