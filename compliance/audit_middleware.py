"""Audit logging middleware for OpsLens.

Automatically logs every state-changing HTTP request (POST, PUT, DELETE, PATCH)
to the AuditLog database table. Extracts user identity from JWT tokens,
captures IP addresses, user agents, and a summary of the request body.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

# HTTP methods that represent state changes
STATE_CHANGING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Path prefixes to skip (health checks, metrics, static assets)
SKIP_PATH_PREFIXES = (
    "/healthz",
    "/readyz",
    "/metrics",
    "/static/",
    "/favicon.ico",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# Maximum size of request body summary stored in audit log (bytes)
MAX_BODY_SUMMARY_SIZE = 2048

# Regex to extract resource IDs from URL paths (UUID or integer)
RESOURCE_ID_PATTERN = re.compile(
    r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)(?:/|$)",
    re.IGNORECASE,
)

# Regex to extract resource type from URL path
RESOURCE_TYPE_PATTERN = re.compile(r"/api/(?:v\d+/)?(\w+)")


def _extract_resource_info(path: str) -> tuple[str | None, str | None]:
    """Extract resource type and ID from a URL path.

    Args:
        path: The request URL path.

    Returns:
        A tuple of (resource_type, resource_id), either may be None.
    """
    resource_type = None
    resource_id = None

    type_match = RESOURCE_TYPE_PATTERN.search(path)
    if type_match:
        resource_type = type_match.group(1)

    id_match = RESOURCE_ID_PATTERN.search(path)
    if id_match:
        resource_id = id_match.group(1)

    return resource_type, resource_id


def _extract_user_id_from_token(request: Request) -> uuid.UUID | None:
    """Attempt to extract user_id from a JWT in the Authorization header.

    Uses the project's existing JWT decode function. Returns None if no
    valid token is present or decoding fails.

    Args:
        request: The incoming HTTP request.

    Returns:
        The user's UUID or None.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]
    if not token:
        return None

    try:
        from src.auth.jwt import decode_token
        token_data = decode_token(token)
        return token_data.user_id
    except Exception:
        return None


def _get_client_ip(request: Request) -> str:
    """Extract the client IP address, respecting proxy headers.

    Args:
        request: The incoming HTTP request.

    Returns:
        The client IP address string.
    """
    # Check X-Forwarded-For first (behind reverse proxy / load balancer)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP (original client)
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # Fall back to direct connection IP
    if request.client:
        return request.client.host

    return "unknown"


async def _read_body_summary(request: Request) -> str | None:
    """Read and truncate the request body for audit logging.

    Sensitive fields (password, token, secret, key, authorization) are
    redacted. The body is truncated to MAX_BODY_SUMMARY_SIZE.

    Args:
        request: The incoming HTTP request.

    Returns:
        A truncated string summary of the body, or None.
    """
    try:
        body_bytes = await request.body()
        if not body_bytes:
            return None

        body_str = body_bytes.decode("utf-8", errors="replace")

        # Redact sensitive fields from the body summary
        sensitive_patterns = [
            (re.compile(r'"(password|token|secret|key|authorization)":\s*"[^"]*"', re.IGNORECASE),
             r'"\1": "[REDACTED]"'),
            (re.compile(r'"(password|token|secret|key|authorization)":\s*\S+', re.IGNORECASE),
             r'"\1": "[REDACTED]"'),
        ]

        for pattern, replacement in sensitive_patterns:
            body_str = pattern.sub(replacement, body_str)

        if len(body_str) > MAX_BODY_SUMMARY_SIZE:
            body_str = body_str[:MAX_BODY_SUMMARY_SIZE] + "...[truncated]"

        return body_str

    except Exception:
        return None


class AuditMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that logs state-changing requests to the AuditLog table.

    Only POST, PUT, DELETE, and PATCH requests are logged. Health checks,
    metrics endpoints, and static file requests are skipped.

    The middleware operates asynchronously and does not block request
    processing -- audit log writes happen after the response is sent.

    Usage::

        from compliance.audit_middleware import AuditMiddleware

        app = FastAPI()
        app.add_middleware(AuditMiddleware)
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip non-state-changing methods
        if request.method not in STATE_CHANGING_METHODS:
            return await call_next(request)

        # Skip health checks, metrics, and static files
        path = request.url.path
        if path.startswith(SKIP_PATH_PREFIXES):
            return await call_next(request)

        # Collect audit data before processing the request
        user_id = _extract_user_id_from_token(request)
        client_ip = _get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        resource_type, resource_id = _extract_resource_info(path)
        action = f"{request.method} {path}"

        # Read body summary (non-blocking for the response)
        body_summary = await _read_body_summary(request)

        # Process the actual request
        response = await call_next(request)

        # Write audit log asynchronously (fire-and-forget)
        try:
            await self._write_audit_log(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                body_summary=body_summary,
                ip_address=client_ip,
                user_agent=user_agent,
                status_code=response.status_code,
            )
        except Exception as exc:
            # Never let audit logging failures break the request
            logger.error(
                "audit.write_failed",
                error=str(exc),
                action=action,
                user_id=str(user_id) if user_id else None,
            )

        return response

    async def _write_audit_log(
        self,
        user_id: uuid.UUID | None,
        action: str,
        resource_type: str | None,
        resource_id: str | None,
        body_summary: str | None,
        ip_address: str,
        user_agent: str,
        status_code: int,
    ) -> None:
        """Persist an audit log entry to the database.

        Creates its own database session to avoid interfering with the
        request's transactional scope.

        Args:
            user_id: UUID of the authenticated user, or None.
            action: The HTTP method and path (e.g. "POST /api/incidents").
            resource_type: Extracted resource type from the path.
            resource_id: Extracted resource ID from the path.
            body_summary: Truncated/redacted request body.
            ip_address: Client IP address.
            user_agent: Client user-agent string.
            status_code: HTTP response status code.
        """
        from src.database.engine import AsyncSessionLocal
        from src.database.models import AuditLog

        details: dict[str, Any] = {
            "status_code": status_code,
        }
        if body_summary:
            details["request_body"] = body_summary

        async with AsyncSessionLocal() as session:
            audit_entry = AuditLog(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else None,
                created_at=datetime.now(timezone.utc),
            )
            session.add(audit_entry)
            await session.commit()

        logger.debug(
            "audit.logged",
            action=action,
            user_id=str(user_id) if user_id else "anonymous",
            resource_type=resource_type,
            resource_id=resource_id,
            status_code=status_code,
            ip_address=ip_address,
        )
