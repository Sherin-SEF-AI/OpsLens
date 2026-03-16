"""Sentry error tracking integration for OpsLens."""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Lazy import -- Sentry SDK is optional.
# ---------------------------------------------------------------------------

_sentry_available: bool = False
_sentry_sdk: Any = None

try:
    import sentry_sdk as _sentry_sdk  # type: ignore[assignment]
    _sentry_available = True
except ImportError:
    pass

# Patterns for scrubbing sensitive data from event payloads.
_SENSITIVE_KEYS_RE = re.compile(
    r"(token|key|secret|password|authorization|credential|api_key|apikey|dsn|private)",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def _scrub_data(obj: Any) -> Any:
    """Recursively redact values whose keys look sensitive."""
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _SENSITIVE_KEYS_RE.search(k) else _scrub_data(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(_scrub_data(item) for item in obj)
    return obj


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Scrub sensitive fields before sending to Sentry."""
    # Scrub request data
    request_data = event.get("request")
    if isinstance(request_data, dict):
        if "headers" in request_data:
            request_data["headers"] = _scrub_data(request_data["headers"])
        if "data" in request_data:
            request_data["data"] = _scrub_data(request_data["data"])
        if "cookies" in request_data:
            request_data["cookies"] = _REDACTED
        if "query_string" in request_data:
            request_data["query_string"] = _scrub_data(request_data["query_string"])

    # Scrub extra context
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = _scrub_data(extra)

    # Scrub breadcrumb data
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        values = breadcrumbs.get("values", [])
        for bc in values:
            if isinstance(bc.get("data"), dict):
                bc["data"] = _scrub_data(bc["data"])

    # Scrub exception frames local variables
    exception = event.get("exception")
    if isinstance(exception, dict):
        for exc_val in exception.get("values", []):
            stacktrace = exc_val.get("stacktrace")
            if isinstance(stacktrace, dict):
                for frame in stacktrace.get("frames", []):
                    if isinstance(frame.get("vars"), dict):
                        frame["vars"] = _scrub_data(frame["vars"])

    return event


def setup_sentry(
    dsn: str | None = None,
    environment: str = "production",
    release: str | None = None,
) -> None:
    """Initialise the Sentry SDK.

    If *dsn* is ``None`` or empty the SDK is **not** initialised and a
    warning is logged instead.
    """
    if not _sentry_available:
        logger.warning(
            "sentry_sdk_not_installed",
            hint="pip install sentry-sdk[fastapi]",
        )
        return

    if not dsn:
        logger.warning("sentry_dsn_not_configured", hint="Set SENTRY_DSN to enable error tracking")
        return

    traces_sample_rate = 1.0 if environment == "development" else 0.1

    integrations: list[Any] = []

    # FastAPI / Starlette
    try:
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # type: ignore
        from sentry_sdk.integrations.starlette import StarletteIntegration  # type: ignore
        integrations.append(FastApiIntegration())
        integrations.append(StarletteIntegration())
    except ImportError:
        logger.debug("sentry_fastapi_integration_not_available")

    # Celery
    try:
        from sentry_sdk.integrations.celery import CeleryIntegration  # type: ignore
        integrations.append(CeleryIntegration())
    except ImportError:
        logger.debug("sentry_celery_integration_not_available")

    # SQLAlchemy
    try:
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration  # type: ignore
        integrations.append(SqlalchemyIntegration())
    except ImportError:
        logger.debug("sentry_sqlalchemy_integration_not_available")

    _sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        before_send=_before_send,
        integrations=integrations,
        send_default_pii=False,
    )

    _sentry_sdk.set_tag("service", "opslens")
    _sentry_sdk.set_tag("environment", environment)

    logger.info(
        "sentry_initialized",
        environment=environment,
        traces_sample_rate=traces_sample_rate,
    )


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


def capture_exception(
    error: BaseException,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Capture an exception and send it to Sentry.

    Returns the Sentry event ID, or ``None`` if Sentry is not configured.
    """
    if not _sentry_available or not _sentry_sdk.is_initialized():
        logger.error(
            "exception_not_sent_to_sentry",
            error=str(error),
            error_type=type(error).__name__,
        )
        return None

    if context:
        _sentry_sdk.set_context("custom", context)

    event_id: str = _sentry_sdk.capture_exception(error)
    return event_id


def capture_message(
    message: str,
    level: str = "info",
    context: dict[str, Any] | None = None,
) -> str | None:
    """Send a message to Sentry.

    *level* should be one of ``"debug"``, ``"info"``, ``"warning"``,
    ``"error"``, ``"fatal"``.
    """
    if not _sentry_available or not _sentry_sdk.is_initialized():
        return None

    if context:
        _sentry_sdk.set_context("custom", context)

    event_id: str = _sentry_sdk.capture_message(message, level=level)
    return event_id


def set_user_context(
    user_id: str | None = None,
    email: str | None = None,
    role: str | None = None,
) -> None:
    """Set user information on the current Sentry scope."""
    if not _sentry_available or not _sentry_sdk.is_initialized():
        return

    user_data: dict[str, Any] = {}
    if user_id:
        user_data["id"] = user_id
    if email:
        user_data["email"] = email
    if role:
        user_data["role"] = role

    if user_data:
        _sentry_sdk.set_user(user_data)


def add_breadcrumb(
    message: str,
    category: str = "custom",
    data: dict[str, Any] | None = None,
) -> None:
    """Add a breadcrumb to the current Sentry scope."""
    if not _sentry_available or not _sentry_sdk.is_initialized():
        return

    _sentry_sdk.add_breadcrumb(
        message=message,
        category=category,
        data=data or {},
    )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SentryMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that sets Sentry user context per request.

    It extracts user information from a JWT ``Authorization`` header when
    present.  The token is decoded **without** verification here -- actual
    auth enforcement happens elsewhere.  This is purely for Sentry context.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if _sentry_available and _sentry_sdk.is_initialized():
            self._set_user_from_request(request)
            add_breadcrumb(
                message=f"{request.method} {request.url.path}",
                category="http",
                data={
                    "method": request.method,
                    "url": str(request.url),
                },
            )

        return await call_next(request)

    @staticmethod
    def _set_user_from_request(request: Request) -> None:
        """Try to extract user info from a JWT bearer token."""
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return

        token = auth_header[7:].strip()
        if not token:
            return

        try:
            import base64
            import json as _json

            # Decode the payload segment (index 1) without verification.
            parts = token.split(".")
            if len(parts) < 2:
                return

            payload_b64 = parts[1]
            # Add padding if needed
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding

            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))

            user_id = payload.get("sub") or payload.get("user_id")
            email = payload.get("email")
            role = payload.get("role")

            set_user_context(
                user_id=str(user_id) if user_id else None,
                email=email,
                role=role,
            )
        except Exception:
            # JWT parsing is best-effort for Sentry context; never crash.
            pass
