"""Tests for the OpsLens error hierarchy."""

import pytest

from src.errors import (
    AgentError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DatabaseError,
    DuplicateIncidentError,
    EncryptionError,
    IncidentError,
    IncidentNotFoundError,
    InvalidTransitionError,
    MCPConnectionError,
    MCPError,
    MCPRateLimitedError,
    OpsLensError,
    RateLimitExceededError,
    SlackError,
    ValidationError,
    WebhookDeliveryError,
)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class TestOpsLensErrorBase:
    def test_default_attributes(self):
        err = OpsLensError()
        assert err.status_code == 500
        assert err.error_code == "INTERNAL_ERROR"
        assert err.message == "An internal error occurred"
        assert err.details == {}

    def test_custom_message(self):
        err = OpsLensError("something broke")
        assert err.message == "something broke"
        assert str(err) == "something broke"

    def test_custom_details(self):
        err = OpsLensError("oops", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_override_status_and_code(self):
        err = OpsLensError("custom", status_code=418, error_code="TEAPOT")
        assert err.status_code == 418
        assert err.error_code == "TEAPOT"


# ---------------------------------------------------------------------------
# to_response
# ---------------------------------------------------------------------------

class TestToResponse:
    def test_basic_response(self):
        err = OpsLensError("test error")
        resp = err.to_response()
        assert resp["error"] is True
        assert resp["error_code"] == "INTERNAL_ERROR"
        assert resp["message"] == "test error"
        assert "timestamp" in resp
        assert "details" not in resp  # empty details omitted

    def test_response_with_details(self):
        err = OpsLensError("test", details={"incident_id": "X"})
        resp = err.to_response()
        assert resp["details"] == {"incident_id": "X"}


# ---------------------------------------------------------------------------
# Specific error subclasses
# ---------------------------------------------------------------------------

class TestErrorSubclasses:
    def test_authentication_error(self):
        err = AuthenticationError()
        assert err.status_code == 401
        assert err.error_code == "AUTHENTICATION_ERROR"
        assert "Authentication" in err.message

    def test_authorization_error(self):
        err = AuthorizationError("no access")
        assert err.status_code == 403
        assert err.error_code == "AUTHORIZATION_ERROR"

    def test_incident_not_found(self):
        err = IncidentNotFoundError("OPSLENS-0042")
        assert err.status_code == 404
        assert err.error_code == "INCIDENT_NOT_FOUND"
        assert "OPSLENS-0042" in err.message
        assert err.details["incident_id"] == "OPSLENS-0042"

    def test_incident_not_found_no_id(self):
        err = IncidentNotFoundError()
        assert "Incident not found" in err.message

    def test_invalid_transition_error(self):
        err = InvalidTransitionError(
            current_state="Triggered",
            target_state="Resolved",
            incident_id="OPSLENS-0001",
        )
        assert err.status_code == 409
        assert err.error_code == "INVALID_TRANSITION"
        assert "Triggered" in err.message
        assert "Resolved" in err.message
        assert err.details["current_state"] == "Triggered"
        assert err.details["target_state"] == "Resolved"

    def test_duplicate_incident_error(self):
        err = DuplicateIncidentError(existing_id="OPSLENS-0010")
        assert err.status_code == 409
        assert "OPSLENS-0010" in err.message

    def test_rate_limit_exceeded(self):
        err = RateLimitExceededError(retry_after=30)
        assert err.status_code == 429
        assert err.error_code == "RATE_LIMIT_EXCEEDED"
        assert "30" in err.message
        assert err.details["retry_after_seconds"] == 30

    def test_rate_limit_no_retry_after(self):
        err = RateLimitExceededError()
        assert err.status_code == 429
        assert "retry_after_seconds" not in err.details

    def test_configuration_error(self):
        err = ConfigurationError("bad config")
        assert err.status_code == 500
        assert err.error_code == "CONFIGURATION_ERROR"

    def test_database_error(self):
        err = DatabaseError("connection lost")
        assert err.status_code == 500
        assert err.error_code == "DATABASE_ERROR"

    def test_encryption_error(self):
        err = EncryptionError("bad key")
        assert err.status_code == 500
        assert err.error_code == "ENCRYPTION_ERROR"

    def test_validation_error(self):
        err = ValidationError("field X required")
        assert err.status_code == 422
        assert err.error_code == "VALIDATION_ERROR"

    def test_mcp_error(self):
        err = MCPError("Notion is down")
        assert err.status_code == 502

    def test_mcp_connection_error(self):
        err = MCPConnectionError()
        assert err.status_code == 503
        assert err.error_code == "MCP_CONNECTION_ERROR"

    def test_mcp_rate_limited(self):
        err = MCPRateLimitedError(retry_after=60)
        assert err.status_code == 429
        assert err.details["retry_after_seconds"] == 60

    def test_slack_error(self):
        err = SlackError("webhook failed")
        assert err.status_code == 502
        assert err.error_code == "SLACK_ERROR"
        assert err.details["integration"] == "slack"

    def test_webhook_delivery_error(self):
        err = WebhookDeliveryError(target_url="https://example.com/hook")
        assert err.details["target_url"] == "https://example.com/hook"

    def test_agent_error(self):
        err = AgentError("timeout", agent_name="triage")
        assert err.status_code == 500
        assert err.details["agent_name"] == "triage"


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------

class TestErrorInheritance:
    def test_incident_errors_are_opslens_errors(self):
        assert isinstance(IncidentNotFoundError(), OpsLensError)
        assert isinstance(InvalidTransitionError(), OpsLensError)
        assert isinstance(DuplicateIncidentError(), OpsLensError)

    def test_incident_errors_are_incident_errors(self):
        assert isinstance(IncidentNotFoundError(), IncidentError)
        assert isinstance(InvalidTransitionError(), IncidentError)

    def test_mcp_errors_are_opslens_errors(self):
        assert isinstance(MCPConnectionError(), OpsLensError)
        assert isinstance(MCPConnectionError(), MCPError)

    def test_all_errors_are_exceptions(self):
        for cls in [
            OpsLensError,
            AuthenticationError,
            IncidentNotFoundError,
            RateLimitExceededError,
            MCPError,
            SlackError,
            EncryptionError,
        ]:
            assert issubclass(cls, Exception)
