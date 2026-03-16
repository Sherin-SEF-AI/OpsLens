"""Prometheus metrics for OpsLens observability."""

from __future__ import annotations

import time
from typing import Callable

import structlog
from fastapi import Request, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

INCIDENTS_TOTAL = Counter(
    "opslens_incidents_total",
    "Total incidents created",
    ["severity", "service", "source"],
)

INCIDENTS_ACTIVE = Gauge(
    "opslens_incidents_active",
    "Currently active incidents",
    ["severity"],
)

INCIDENT_RESOLUTION_SECONDS = Histogram(
    "opslens_incident_resolution_seconds",
    "Incident resolution time",
    ["severity"],
    buckets=[60, 300, 600, 1800, 3600, 7200, 14400, 28800, 86400],
)

AGENT_DURATION_SECONDS = Histogram(
    "opslens_agent_duration_seconds",
    "Agent execution time",
    ["agent_type"],
    buckets=[1, 2, 5, 10, 20, 30, 60, 120, 300],
)

AGENT_CONFIDENCE = Histogram(
    "opslens_agent_confidence",
    "Agent confidence scores",
    ["agent_type"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

WEBHOOK_REQUESTS_TOTAL = Counter(
    "opslens_webhook_requests_total",
    "Webhook requests",
    ["source", "status"],
)

WEBHOOK_LATENCY_SECONDS = Histogram(
    "opslens_webhook_latency_seconds",
    "Webhook processing latency",
    ["source"],
)

MCP_REQUESTS_TOTAL = Counter(
    "opslens_mcp_requests_total",
    "MCP tool calls",
    ["tool", "status"],
)

MCP_LATENCY_SECONDS = Histogram(
    "opslens_mcp_latency_seconds",
    "MCP call latency",
    ["tool"],
)

WS_CONNECTIONS = Gauge(
    "opslens_ws_connections_active",
    "Active WebSocket connections",
)

API_REQUESTS_TOTAL = Counter(
    "opslens_api_requests_total",
    "API requests",
    ["method", "endpoint", "status"],
)

API_LATENCY_SECONDS = Histogram(
    "opslens_api_latency_seconds",
    "API request latency",
    ["method", "endpoint"],
)

DB_QUERY_SECONDS = Histogram(
    "opslens_db_query_seconds",
    "Database query latency",
    ["operation"],
)

CACHE_HITS = Counter(
    "opslens_cache_hits_total",
    "Cache hits",
    ["cache_type"],
)

CACHE_MISSES = Counter(
    "opslens_cache_misses_total",
    "Cache misses",
    ["cache_type"],
)

SLA_BREACHES_TOTAL = Counter(
    "opslens_sla_breaches_total",
    "SLA breaches",
    ["severity", "breach_type"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "opslens_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["name"],
)

TASK_QUEUE_SIZE = Gauge(
    "opslens_task_queue_size",
    "Celery task queue size",
    ["queue"],
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def track_incident_created(severity: str, service: str, source: str) -> None:
    """Record a new incident creation."""
    INCIDENTS_TOTAL.labels(severity=severity, service=service, source=source).inc()
    INCIDENTS_ACTIVE.labels(severity=severity).inc()
    logger.debug(
        "metric_incident_created",
        severity=severity,
        service=service,
        source=source,
    )


def track_agent_run(
    agent_type: str, duration_seconds: float, confidence: float
) -> None:
    """Record an agent execution with its duration and confidence score."""
    AGENT_DURATION_SECONDS.labels(agent_type=agent_type).observe(duration_seconds)
    AGENT_CONFIDENCE.labels(agent_type=agent_type).observe(confidence)
    logger.debug(
        "metric_agent_run",
        agent_type=agent_type,
        duration_seconds=round(duration_seconds, 3),
        confidence=round(confidence, 3),
    )


def track_webhook(source: str, status: str, latency_seconds: float) -> None:
    """Record a webhook request."""
    WEBHOOK_REQUESTS_TOTAL.labels(source=source, status=status).inc()
    WEBHOOK_LATENCY_SECONDS.labels(source=source).observe(latency_seconds)


def track_mcp_call(tool: str, status: str, latency_seconds: float) -> None:
    """Record an MCP tool call."""
    MCP_REQUESTS_TOTAL.labels(tool=tool, status=status).inc()
    MCP_LATENCY_SECONDS.labels(tool=tool).observe(latency_seconds)


def track_api_request(
    method: str, endpoint: str, status: str, latency_seconds: float
) -> None:
    """Record an API request."""
    API_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
    API_LATENCY_SECONDS.labels(method=method, endpoint=endpoint).observe(
        latency_seconds
    )


def track_sla_breach(severity: str, breach_type: str) -> None:
    """Record an SLA breach event."""
    SLA_BREACHES_TOTAL.labels(severity=severity, breach_type=breach_type).inc()
    logger.warning(
        "sla_breach_detected", severity=severity, breach_type=breach_type
    )


# ---------------------------------------------------------------------------
# Metrics middleware
# ---------------------------------------------------------------------------

# Paths that should not be recorded to avoid high-cardinality noise.
_SKIP_PATHS: set[str] = {"/metrics", "/healthz", "/readyz", "/health", "/openapi.json", "/docs", "/redoc"}


def _normalize_path(path: str) -> str:
    """Collapse path parameters to reduce label cardinality.

    e.g. /api/incidents/abc-123 -> /api/incidents/{id}
    """
    parts = path.rstrip("/").split("/")
    normalised: list[str] = []
    for part in parts:
        if not part:
            continue
        # Heuristic: if the segment looks like an ID (UUID-ish, numeric, or
        # longer than 20 chars), collapse it.
        if (
            len(part) > 20
            or part.replace("-", "").replace("_", "").isalnum()
            and not part.isalpha()
            and len(part) > 8
        ):
            normalised.append("{id}")
        else:
            normalised.append(part)
    return "/" + "/".join(normalised) if normalised else "/"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that records Prometheus request metrics."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        method = request.method
        endpoint = _normalize_path(request.url.path)
        start = time.perf_counter()

        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed = time.perf_counter() - start
            status = str(response.status_code) if response else "500"
            track_api_request(method, endpoint, status, elapsed)


# ---------------------------------------------------------------------------
# /metrics endpoint handler
# ---------------------------------------------------------------------------


async def metrics_endpoint(request: Request) -> Response:
    """Return Prometheus metrics in exposition format."""
    body = generate_latest()
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)
