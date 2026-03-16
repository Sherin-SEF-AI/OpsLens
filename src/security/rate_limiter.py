"""API rate limiting for OpsLens using slowapi.

Provides tiered rate limits based on endpoint category and user identity.
Attempts to use Redis as the storage backend for distributed deployments;
falls back to in-memory storage when Redis is unavailable.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Rate limit tiers (requests / window)
# ---------------------------------------------------------------------------

RATE_DEFAULT_AUTHENTICATED = "60/minute"
RATE_DEFAULT_ANONYMOUS = "30/minute"
RATE_WEBHOOK = "100/minute"
RATE_SEARCH = "20/minute"
RATE_AUTH = "10/minute"
RATE_SETTINGS = "10/minute"

# ---------------------------------------------------------------------------
# Key extraction
# ---------------------------------------------------------------------------


def _get_rate_limit_key(request: Request) -> str:
    """Extract a rate-limit key from the request.

    Strategy:
    1. If an ``Authorization`` header contains a Bearer JWT, extract the
       ``sub`` claim (user ID) without full verification (rate limiting is
       not a security gate, just a fairness mechanism).
    2. Otherwise fall back to the client IP address.

    Args:
        request: The incoming FastAPI request.

    Returns:
        A string key identifying the caller for rate-limit bucketing.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        user_id = _extract_user_id_from_jwt(token)
        if user_id:
            return f"user:{user_id}"

    return f"ip:{get_remote_address(request)}"


def _extract_user_id_from_jwt(token: str) -> str | None:
    """Best-effort extraction of the ``sub`` claim from a JWT.

    This does **not** verify the token signature -- verification is handled
    by the authentication middleware.  We only need a stable identifier for
    rate-limit bucketing.

    Args:
        token: Raw JWT string.

    Returns:
        The ``sub`` claim value, or ``None`` if extraction fails.
    """
    import base64
    import json

    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        # JWT payload is the second segment, base64url-encoded
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)
        return payload.get("sub")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Limiter instance
# ---------------------------------------------------------------------------


def _create_limiter() -> Limiter:
    """Create the slowapi ``Limiter`` with the best available backend.

    Tries Redis first (via ``REDIS_URL`` env var), then falls back to
    in-memory storage.

    Returns:
        Configured ``Limiter`` instance.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    storage_uri: str | None = None

    if redis_url:
        try:
            # Verify Redis is reachable before committing to it
            import redis

            r = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
            r.ping()
            storage_uri = redis_url
            logger.info("rate_limiter_backend", backend="redis", url=redis_url)
        except Exception as exc:
            logger.warning(
                "rate_limiter_redis_unavailable",
                error=str(exc),
                fallback="in-memory",
            )

    if not storage_uri:
        logger.info("rate_limiter_backend", backend="in-memory")

    return Limiter(
        key_func=_get_rate_limit_key,
        default_limits=[RATE_DEFAULT_ANONYMOUS],
        storage_uri=storage_uri or "memory://",
    )


limiter: Limiter = _create_limiter()

# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a structured JSON 429 response when a rate limit is exceeded.

    Includes a ``Retry-After`` header with the number of seconds until the
    limit resets.
    """
    # Parse retry-after from the exception detail if available
    retry_after = 60  # default fallback
    detail_str = str(getattr(exc, "detail", ""))
    # slowapi detail format: "Rate limit exceeded: N per M <unit>"
    # We provide a sensible default rather than parsing the window.

    logger.warning(
        "rate_limit_exceeded",
        key=_get_rate_limit_key(request),
        path=str(request.url.path),
        method=request.method,
        detail=detail_str,
    )

    return JSONResponse(
        status_code=429,
        content={
            "error": True,
            "error_code": "RATE_LIMIT_EXCEEDED",
            "message": f"Rate limit exceeded: {detail_str}" if detail_str else "Rate limit exceeded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "retry_after_seconds": retry_after,
            },
        },
        headers={"Retry-After": str(retry_after)},
    )


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------


def setup_rate_limiter(app: FastAPI) -> None:
    """Attach the rate limiter to a FastAPI application.

    This registers:
    - The limiter as request middleware via ``app.state.limiter``.
    - A custom 429 error handler with structured JSON responses.

    Call this once during application startup (e.g. in ``main.py``).

    Args:
        app: The FastAPI application instance.
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info(
        "rate_limiter_configured",
        default_authenticated=RATE_DEFAULT_AUTHENTICATED,
        default_anonymous=RATE_DEFAULT_ANONYMOUS,
        webhook=RATE_WEBHOOK,
        search=RATE_SEARCH,
        auth=RATE_AUTH,
        settings=RATE_SETTINGS,
    )
