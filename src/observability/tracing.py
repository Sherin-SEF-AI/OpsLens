"""OpenTelemetry distributed tracing for OpsLens."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Generator

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Lazy imports -- all OpenTelemetry packages are optional.  When they are
# missing the module still loads and the public helpers become safe no-ops.
# ---------------------------------------------------------------------------

_otel_available: bool = False
_trace_mod: Any = None  # opentelemetry.trace
_context_mod: Any = None  # opentelemetry.context

try:
    from opentelemetry import trace as _trace_mod, context as _context_mod  # type: ignore[assignment]
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME

    _otel_available = True
except ImportError:
    pass

# Store our tracer globally once initialised.
_tracer: Any = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_tracing(
    app: Any,
    service_name: str = "opslens",
) -> None:
    """Initialise OpenTelemetry tracing and instrument the FastAPI app.

    If the required packages are not installed the function logs a warning
    and returns without raising.
    """
    global _tracer

    if not _otel_available:
        logger.warning("opentelemetry_not_installed", hint="pip install opentelemetry-sdk opentelemetry-api")
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("otel_otlp_exporter_configured", endpoint=otlp_endpoint)
        except ImportError:
            logger.warning(
                "otlp_exporter_not_installed",
                hint="pip install opentelemetry-exporter-otlp-proto-grpc",
            )
            # Fall back to console
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("otel_console_exporter_fallback")
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("otel_console_exporter_configured", reason="no OTEL_EXPORTER_OTLP_ENDPOINT set")

    _trace_mod.set_tracer_provider(provider)
    _tracer = _trace_mod.get_tracer(service_name)

    # --- Instrument libraries (each is optional) ---

    # FastAPI
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore

        FastAPIInstrumentor.instrument_app(app)
        logger.info("otel_fastapi_instrumented")
    except ImportError:
        logger.debug("otel_fastapi_instrumentor_not_installed")

    # httpx
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # type: ignore

        HTTPXClientInstrumentor().instrument()
        logger.info("otel_httpx_instrumented")
    except ImportError:
        logger.debug("otel_httpx_instrumentor_not_installed")

    # SQLAlchemy
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # type: ignore

        SQLAlchemyInstrumentor().instrument()
        logger.info("otel_sqlalchemy_instrumented")
    except ImportError:
        logger.debug("otel_sqlalchemy_instrumentor_not_installed")


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def _get_tracer() -> Any:
    """Return the configured tracer, or a no-op tracer if OTEL is unavailable."""
    global _tracer
    if _tracer is not None:
        return _tracer
    if _otel_available:
        _tracer = _trace_mod.get_tracer("opslens")
        return _tracer
    return None


@contextmanager
def trace_agent_run(
    agent_type: str, incident_id: str
) -> Generator[Any, None, None]:
    """Context manager that wraps an agent run in an OTEL span.

    Usage::

        with trace_agent_run("triage", incident.id) as span:
            result = await run_triage(incident)
            span.set_attribute("confidence", result.confidence)
    """
    tracer = _get_tracer()
    if tracer is None:
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(
        f"agent.{agent_type}",
        attributes={
            "agent.type": agent_type,
            "incident.id": incident_id,
        },
    ) as span:
        yield span


@contextmanager
def trace_mcp_call(tool_name: str) -> Generator[Any, None, None]:
    """Context manager that wraps an MCP tool call in an OTEL span."""
    tracer = _get_tracer()
    if tracer is None:
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(
        f"mcp.{tool_name}",
        attributes={"mcp.tool": tool_name},
    ) as span:
        yield span


@contextmanager
def trace_webhook_processing(source: str) -> Generator[Any, None, None]:
    """Context manager that wraps webhook processing in an OTEL span."""
    tracer = _get_tracer()
    if tracer is None:
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(
        f"webhook.{source}",
        attributes={"webhook.source": source},
    ) as span:
        yield span


def add_span_attributes(**kwargs: Any) -> None:
    """Add custom attributes to the current active span (if any)."""
    if not _otel_available:
        return
    span = _trace_mod.get_current_span()
    if span is None or not span.is_recording():
        return
    for key, value in kwargs.items():
        span.set_attribute(key, value)


def get_trace_id() -> str | None:
    """Return the current trace ID as a hex string, or None."""
    if not _otel_available:
        return None
    span = _trace_mod.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if ctx is None or ctx.trace_id == 0:
        return None
    return format(ctx.trace_id, "032x")


# ---------------------------------------------------------------------------
# structlog processor for trace-id injection
# ---------------------------------------------------------------------------


def trace_id_processor(
    logger_instance: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor that injects the current OTEL trace_id into every
    log entry when available."""
    tid = get_trace_id()
    if tid:
        event_dict["trace_id"] = tid
    return event_dict


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """Lightweight stand-in when OTEL is not available."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass
