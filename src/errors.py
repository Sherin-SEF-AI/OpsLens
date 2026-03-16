"""OpsLens exception hierarchy and FastAPI error handlers.

Provides a structured exception tree for all error conditions in OpsLens,
with machine-readable error codes, HTTP status codes, and standardized
JSON error response formatting.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Base exception
# ---------------------------------------------------------------------------


class OpsLensError(Exception):
    """Base exception for all OpsLens errors.

    Attributes:
        status_code: HTTP status code to return.
        error_code: Machine-readable error identifier (e.g. ``INCIDENT_NOT_FOUND``).
        message: Human-readable description.
        details: Optional dict with extra context for debugging.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str = "An internal error occurred",
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code

    def to_response(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict suitable for API responses."""
        payload: dict[str, Any] = {
            "error": True,
            "error_code": self.error_code,
            "message": self.message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.details:
            payload["details"] = self.details
        return payload


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigurationError(OpsLensError):
    """Invalid configuration or missing required environment variables."""

    status_code = 500
    error_code = "CONFIGURATION_ERROR"

    def __init__(self, message: str = "Invalid configuration", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ---------------------------------------------------------------------------
# Authentication / Authorization
# ---------------------------------------------------------------------------


class AuthenticationError(OpsLensError):
    """Invalid credentials, expired tokens, or missing authentication."""

    status_code = 401
    error_code = "AUTHENTICATION_ERROR"

    def __init__(self, message: str = "Authentication required", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class AuthorizationError(OpsLensError):
    """Authenticated but insufficient permissions for the requested action."""

    status_code = 403
    error_code = "AUTHORIZATION_ERROR"

    def __init__(self, message: str = "Insufficient permissions", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ---------------------------------------------------------------------------
# Incident errors
# ---------------------------------------------------------------------------


class IncidentError(OpsLensError):
    """Base exception for incident-related errors."""

    status_code = 400
    error_code = "INCIDENT_ERROR"

    def __init__(self, message: str = "Incident error", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class IncidentNotFoundError(IncidentError):
    """The requested incident does not exist."""

    status_code = 404
    error_code = "INCIDENT_NOT_FOUND"

    def __init__(self, incident_id: str = "", **kwargs: Any) -> None:
        message = f"Incident not found: {incident_id}" if incident_id else "Incident not found"
        super().__init__(message, details={"incident_id": incident_id}, **kwargs)


class InvalidTransitionError(IncidentError):
    """Attempted an invalid state transition on an incident."""

    status_code = 409
    error_code = "INVALID_TRANSITION"

    def __init__(
        self,
        current_state: str = "",
        target_state: str = "",
        incident_id: str = "",
        **kwargs: Any,
    ) -> None:
        message = f"Cannot transition from '{current_state}' to '{target_state}'"
        if incident_id:
            message += f" for incident {incident_id}"
        super().__init__(
            message,
            details={
                "incident_id": incident_id,
                "current_state": current_state,
                "target_state": target_state,
            },
            **kwargs,
        )


class DuplicateIncidentError(IncidentError):
    """An incident matching the deduplication criteria already exists."""

    status_code = 409
    error_code = "DUPLICATE_INCIDENT"

    def __init__(self, existing_id: str = "", **kwargs: Any) -> None:
        message = "Duplicate incident detected"
        if existing_id:
            message += f" (existing: {existing_id})"
        super().__init__(message, details={"existing_incident_id": existing_id}, **kwargs)


class IncidentValidationError(IncidentError):
    """Incident payload failed validation."""

    status_code = 422
    error_code = "INCIDENT_VALIDATION_ERROR"

    def __init__(self, message: str = "Incident validation failed", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ---------------------------------------------------------------------------
# MCP / Notion errors
# ---------------------------------------------------------------------------


class MCPError(OpsLensError):
    """Base exception for Notion MCP communication errors."""

    status_code = 502
    error_code = "MCP_ERROR"

    def __init__(self, message: str = "Notion MCP error", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class MCPConnectionError(MCPError):
    """Cannot reach the Notion MCP server."""

    status_code = 503
    error_code = "MCP_CONNECTION_ERROR"

    def __init__(self, message: str = "Cannot connect to Notion MCP server", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class MCPSessionExpiredError(MCPError):
    """The MCP session has expired and needs re-initialization."""

    status_code = 502
    error_code = "MCP_SESSION_EXPIRED"

    def __init__(self, message: str = "MCP session expired, re-initializing", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class MCPRateLimitedError(MCPError):
    """Notion API rate limit hit via MCP."""

    status_code = 429
    error_code = "MCP_RATE_LIMITED"

    def __init__(self, retry_after: int | None = None, **kwargs: Any) -> None:
        message = "Notion API rate limit exceeded"
        details = kwargs.pop("details", {}) or {}
        if retry_after is not None:
            message += f", retry after {retry_after}s"
            details["retry_after_seconds"] = retry_after
        super().__init__(message, details=details, **kwargs)


class MCPToolError(MCPError):
    """An MCP tool call returned an error result."""

    status_code = 502
    error_code = "MCP_TOOL_ERROR"

    def __init__(self, tool_name: str = "", message: str = "MCP tool call failed", **kwargs: Any) -> None:
        details = kwargs.pop("details", {}) or {}
        details["tool_name"] = tool_name
        full_message = f"MCP tool '{tool_name}' failed: {message}" if tool_name else message
        super().__init__(full_message, details=details, **kwargs)


# ---------------------------------------------------------------------------
# Agent / AI errors
# ---------------------------------------------------------------------------


class AgentError(OpsLensError):
    """Base exception for AI agent pipeline errors."""

    status_code = 500
    error_code = "AGENT_ERROR"

    def __init__(self, message: str = "Agent error", agent_name: str = "", **kwargs: Any) -> None:
        details = kwargs.pop("details", {}) or {}
        if agent_name:
            details["agent_name"] = agent_name
        super().__init__(message, details=details, **kwargs)


class AgentTimeoutError(AgentError):
    """An agent exceeded its execution time limit."""

    status_code = 504
    error_code = "AGENT_TIMEOUT"

    def __init__(self, agent_name: str = "", timeout_seconds: float = 0, **kwargs: Any) -> None:
        message = f"Agent '{agent_name}' timed out after {timeout_seconds}s" if agent_name else "Agent timed out"
        details = kwargs.pop("details", {}) or {}
        details["timeout_seconds"] = timeout_seconds
        super().__init__(message, agent_name=agent_name, details=details, **kwargs)


class AgentLLMError(AgentError):
    """The underlying LLM returned an error or unusable response."""

    status_code = 502
    error_code = "AGENT_LLM_ERROR"

    def __init__(self, message: str = "LLM call failed", agent_name: str = "", **kwargs: Any) -> None:
        super().__init__(message, agent_name=agent_name, **kwargs)


class AgentToolCallError(AgentError):
    """An agent's tool call (e.g. MCP, Slack) failed."""

    status_code = 502
    error_code = "AGENT_TOOL_CALL_ERROR"

    def __init__(
        self,
        tool_name: str = "",
        agent_name: str = "",
        message: str = "Agent tool call failed",
        **kwargs: Any,
    ) -> None:
        details = kwargs.pop("details", {}) or {}
        details["tool_name"] = tool_name
        super().__init__(message, agent_name=agent_name, details=details, **kwargs)


# ---------------------------------------------------------------------------
# Integration errors
# ---------------------------------------------------------------------------


class IntegrationError(OpsLensError):
    """Base exception for external integration failures."""

    status_code = 502
    error_code = "INTEGRATION_ERROR"

    def __init__(self, message: str = "Integration error", integration: str = "", **kwargs: Any) -> None:
        details = kwargs.pop("details", {}) or {}
        if integration:
            details["integration"] = integration
        super().__init__(message, details=details, **kwargs)


class SlackError(IntegrationError):
    """Slack API or webhook delivery failure."""

    error_code = "SLACK_ERROR"

    def __init__(self, message: str = "Slack integration error", **kwargs: Any) -> None:
        super().__init__(message, integration="slack", **kwargs)


class GitHubError(IntegrationError):
    """GitHub API failure."""

    error_code = "GITHUB_ERROR"

    def __init__(self, message: str = "GitHub integration error", **kwargs: Any) -> None:
        super().__init__(message, integration="github", **kwargs)


class JiraError(IntegrationError):
    """Jira API failure."""

    error_code = "JIRA_ERROR"

    def __init__(self, message: str = "Jira integration error", **kwargs: Any) -> None:
        super().__init__(message, integration="jira", **kwargs)


class WebhookDeliveryError(IntegrationError):
    """Failed to deliver an outgoing webhook."""

    error_code = "WEBHOOK_DELIVERY_ERROR"

    def __init__(self, target_url: str = "", message: str = "Webhook delivery failed", **kwargs: Any) -> None:
        details = kwargs.pop("details", {}) or {}
        details["target_url"] = target_url
        super().__init__(message, integration="webhook", details=details, **kwargs)


# ---------------------------------------------------------------------------
# Infrastructure / misc errors
# ---------------------------------------------------------------------------


class DatabaseError(OpsLensError):
    """Database operation failure."""

    status_code = 500
    error_code = "DATABASE_ERROR"

    def __init__(self, message: str = "Database error", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class RateLimitExceededError(OpsLensError):
    """API rate limit exceeded."""

    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"

    def __init__(self, retry_after: int | None = None, **kwargs: Any) -> None:
        message = "Rate limit exceeded"
        details = kwargs.pop("details", {}) or {}
        if retry_after is not None:
            message += f", retry after {retry_after}s"
            details["retry_after_seconds"] = retry_after
        super().__init__(message, details=details, **kwargs)


class EncryptionError(OpsLensError):
    """Encryption or decryption failure."""

    status_code = 500
    error_code = "ENCRYPTION_ERROR"

    def __init__(self, message: str = "Encryption error", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ValidationError(OpsLensError):
    """Generic request validation failure (non-incident)."""

    status_code = 422
    error_code = "VALIDATION_ERROR"

    def __init__(self, message: str = "Validation error", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ---------------------------------------------------------------------------
# FastAPI exception handlers
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI application.

    Handles:
    - All ``OpsLensError`` subclasses with structured JSON responses.
    - Unhandled ``Exception`` with a generic 500 response (no internals leaked).
    """

    @app.exception_handler(OpsLensError)
    async def opslens_error_handler(request: Request, exc: OpsLensError) -> JSONResponse:
        """Handle any OpsLensError and return a structured JSON response."""
        log_method = logger.warning if exc.status_code < 500 else logger.error
        log_method(
            "opslens_error",
            error_code=exc.error_code,
            status_code=exc.status_code,
            message=exc.message,
            details=exc.details,
            path=str(request.url.path),
            method=request.method,
        )

        headers: dict[str, str] = {}
        if isinstance(exc, (RateLimitExceededError, MCPRateLimitedError)):
            retry_after = exc.details.get("retry_after_seconds")
            if retry_after is not None:
                headers["Retry-After"] = str(retry_after)

        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response(),
            headers=headers or None,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unexpected errors. Logs the full traceback but returns
        a safe generic message to the client."""
        logger.error(
            "unhandled_exception",
            exc_type=type(exc).__name__,
            message=str(exc),
            path=str(request.url.path),
            method=request.method,
            traceback=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": True,
                "error_code": "INTERNAL_ERROR",
                "message": "An unexpected internal error occurred",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
