"""CORS configuration for OpsLens.

In development mode common local origins are allowed automatically.
In production the ``CORS_ORIGINS`` environment variable **must** be set
to a comma-separated list of allowed origins.
"""

from __future__ import annotations

import os

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = structlog.get_logger()

# Origins allowed automatically in development mode
_DEV_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8080",
]


def get_cors_origins(environment: str | None = None) -> list[str]:
    """Build the list of allowed CORS origins.

    Strategy:
    1. Read ``CORS_ORIGINS`` from the environment (comma-separated).
       If set, those origins are used regardless of environment mode.
    2. In ``development`` mode (default), append standard localhost
       origins so the Vite dev server and other local tools work
       out of the box.
    3. In ``production`` mode *without* ``CORS_ORIGINS`` set, return
       an empty list (effectively blocking all cross-origin requests)
       and log a warning.

    Args:
        environment: ``"development"`` or ``"production"``.  Defaults to
            the ``ENVIRONMENT`` env var, falling back to ``"production"``.

    Returns:
        Deduplicated list of allowed origin strings.
    """
    if environment is None:
        environment = os.environ.get("ENVIRONMENT", "production").lower()

    explicit_origins: list[str] = []
    cors_env = os.environ.get("CORS_ORIGINS", "").strip()
    if cors_env:
        explicit_origins = [
            origin.strip() for origin in cors_env.split(",") if origin.strip()
        ]

    if environment == "development":
        # Merge explicit + dev defaults, preserving order and uniqueness
        combined = list(dict.fromkeys(explicit_origins + _DEV_ORIGINS))
        return combined

    # Production
    if not explicit_origins:
        logger.warning(
            "cors_no_origins_configured",
            environment=environment,
            hint="Set CORS_ORIGINS env var (comma-separated) to allow cross-origin requests in production.",
        )
        return []

    return explicit_origins


def setup_cors(app: FastAPI, environment: str = "production") -> None:
    """Add CORS middleware to the FastAPI application.

    Args:
        app: The FastAPI application instance.
        environment: ``"development"`` or ``"production"``.
    """
    origins = get_cors_origins(environment)

    allow_credentials = True
    allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    allow_headers = [
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-Webhook-Signature",
        "X-PagerDuty-Signature",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,  # preflight cache: 10 minutes
    )

    if origins:
        logger.info(
            "cors_configured",
            environment=environment,
            origins=origins,
            allow_credentials=allow_credentials,
        )
    else:
        logger.info(
            "cors_configured_no_origins",
            environment=environment,
            message="No CORS origins allowed. Cross-origin requests will be blocked.",
        )
