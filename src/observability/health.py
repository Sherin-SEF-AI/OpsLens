"""Enhanced health check system for OpsLens."""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CheckStatus(str, Enum):
    """Status of an individual health check."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class OverallStatus(str, Enum):
    """Overall health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class CheckResult:
    """Result of a single health check."""
    name: str
    status: CheckStatus
    latency_ms: float
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "message": self.message,
            "details": self.details,
        }


@dataclass
class HealthReport:
    """Aggregated health report from all checks."""
    status: OverallStatus
    checks: dict[str, CheckResult]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "timestamp": self.timestamp,
            "checks": {name: result.to_dict() for name, result in self.checks.items()},
        }


# ---------------------------------------------------------------------------
# HealthChecker
# ---------------------------------------------------------------------------


class HealthChecker:
    """Registry of health checks with aggregated reporting."""

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], Awaitable[CheckResult]]] = {}

    def register_check(
        self,
        name: str,
        check_fn: Callable[[], Awaitable[CheckResult]],
    ) -> None:
        """Register a named async health check function."""
        self._checks[name] = check_fn
        logger.debug("health_check_registered", name=name)

    async def check_all(self, timeout_seconds: float = 10.0) -> HealthReport:
        """Run all registered checks concurrently and produce a report."""
        results: dict[str, CheckResult] = {}

        async def _run(name: str, fn: Callable[[], Awaitable[CheckResult]]) -> None:
            try:
                result = await asyncio.wait_for(fn(), timeout=timeout_seconds)
                results[name] = result
            except asyncio.TimeoutError:
                results[name] = CheckResult(
                    name=name,
                    status=CheckStatus.FAIL,
                    latency_ms=timeout_seconds * 1000,
                    message=f"Check timed out after {timeout_seconds}s",
                )
            except Exception as exc:
                results[name] = CheckResult(
                    name=name,
                    status=CheckStatus.FAIL,
                    latency_ms=0,
                    message=f"Check raised: {type(exc).__name__}: {exc}",
                )

        tasks = [_run(name, fn) for name, fn in self._checks.items()]
        if tasks:
            await asyncio.gather(*tasks)

        # Determine overall status
        statuses = [r.status for r in results.values()]
        if any(s == CheckStatus.FAIL for s in statuses):
            overall = OverallStatus.UNHEALTHY
        elif any(s == CheckStatus.WARN for s in statuses):
            overall = OverallStatus.DEGRADED
        else:
            overall = OverallStatus.HEALTHY

        return HealthReport(
            status=overall,
            checks=results,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Built-in check factories
# ---------------------------------------------------------------------------


def _make_database_check(app: FastAPI) -> Callable[[], Awaitable[CheckResult]]:
    """Check database connectivity via SELECT 1."""

    async def _check() -> CheckResult:
        start = time.perf_counter()
        try:
            # Try SQLAlchemy engine on app.state if available
            engine = getattr(app.state, "db_engine", None)
            if engine is None:
                return CheckResult(
                    name="database",
                    status=CheckStatus.WARN,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    message="No database engine configured",
                )

            from sqlalchemy import text

            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="database",
                status=CheckStatus.PASS,
                latency_ms=elapsed,
                message="OK",
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="database",
                status=CheckStatus.FAIL,
                latency_ms=elapsed,
                message=f"Database unreachable: {exc}",
            )

    return _check


def _make_redis_check(app: FastAPI) -> Callable[[], Awaitable[CheckResult]]:
    """Check Redis connectivity via PING."""

    async def _check() -> CheckResult:
        start = time.perf_counter()
        try:
            redis = getattr(app.state, "redis", None)
            if redis is None:
                return CheckResult(
                    name="redis",
                    status=CheckStatus.WARN,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    message="No Redis client configured",
                )

            pong = await redis.ping()
            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="redis",
                status=CheckStatus.PASS if pong else CheckStatus.FAIL,
                latency_ms=elapsed,
                message="PONG" if pong else "No response",
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="redis",
                status=CheckStatus.FAIL,
                latency_ms=elapsed,
                message=f"Redis unreachable: {exc}",
            )

    return _check


def _make_notion_mcp_check(app: FastAPI) -> Callable[[], Awaitable[CheckResult]]:
    """Check Notion MCP server connectivity."""

    async def _check() -> CheckResult:
        start = time.perf_counter()
        try:
            mcp_client = getattr(app.state, "mcp_client", None)
            if mcp_client is None:
                return CheckResult(
                    name="notion_mcp",
                    status=CheckStatus.FAIL,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    message="MCP client not initialised",
                )

            initialized = getattr(mcp_client, "_initialized", False)
            elapsed = (time.perf_counter() - start) * 1000

            if initialized:
                return CheckResult(
                    name="notion_mcp",
                    status=CheckStatus.PASS,
                    latency_ms=elapsed,
                    message="MCP session active",
                    details={"url": getattr(mcp_client, "_url", "unknown")},
                )
            else:
                return CheckResult(
                    name="notion_mcp",
                    status=CheckStatus.WARN,
                    latency_ms=elapsed,
                    message="MCP session not yet initialised (will retry on use)",
                )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="notion_mcp",
                status=CheckStatus.FAIL,
                latency_ms=elapsed,
                message=f"MCP check failed: {exc}",
            )

    return _check


def _make_celery_check(app: FastAPI) -> Callable[[], Awaitable[CheckResult]]:
    """Check Celery worker availability via inspect.ping()."""

    async def _check() -> CheckResult:
        start = time.perf_counter()
        try:
            celery_app = getattr(app.state, "celery_app", None)
            if celery_app is None:
                return CheckResult(
                    name="celery",
                    status=CheckStatus.WARN,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    message="No Celery app configured",
                )

            loop = asyncio.get_running_loop()
            inspect = celery_app.control.inspect()
            pong = await loop.run_in_executor(None, inspect.ping)

            elapsed = (time.perf_counter() - start) * 1000
            if pong:
                worker_count = len(pong)
                return CheckResult(
                    name="celery",
                    status=CheckStatus.PASS,
                    latency_ms=elapsed,
                    message=f"{worker_count} worker(s) responding",
                    details={"workers": list(pong.keys())},
                )
            else:
                return CheckResult(
                    name="celery",
                    status=CheckStatus.FAIL,
                    latency_ms=elapsed,
                    message="No Celery workers responding",
                )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="celery",
                status=CheckStatus.FAIL,
                latency_ms=elapsed,
                message=f"Celery check failed: {exc}",
            )

    return _check


def _make_disk_space_check(
    min_free_gb: float = 1.0,
) -> Callable[[], Awaitable[CheckResult]]:
    """Check that available disk space exceeds a threshold."""

    async def _check() -> CheckResult:
        start = time.perf_counter()
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            used_pct = (usage.used / usage.total) * 100

            elapsed = (time.perf_counter() - start) * 1000

            if free_gb < min_free_gb:
                return CheckResult(
                    name="disk_space",
                    status=CheckStatus.FAIL,
                    latency_ms=elapsed,
                    message=f"Low disk space: {free_gb:.1f}GB free (min {min_free_gb}GB)",
                    details={
                        "free_gb": round(free_gb, 2),
                        "total_gb": round(total_gb, 2),
                        "used_percent": round(used_pct, 1),
                    },
                )
            elif free_gb < min_free_gb * 2:
                return CheckResult(
                    name="disk_space",
                    status=CheckStatus.WARN,
                    latency_ms=elapsed,
                    message=f"Disk space getting low: {free_gb:.1f}GB free",
                    details={
                        "free_gb": round(free_gb, 2),
                        "total_gb": round(total_gb, 2),
                        "used_percent": round(used_pct, 1),
                    },
                )
            else:
                return CheckResult(
                    name="disk_space",
                    status=CheckStatus.PASS,
                    latency_ms=elapsed,
                    message=f"{free_gb:.1f}GB free",
                    details={
                        "free_gb": round(free_gb, 2),
                        "total_gb": round(total_gb, 2),
                        "used_percent": round(used_pct, 1),
                    },
                )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="disk_space",
                status=CheckStatus.FAIL,
                latency_ms=elapsed,
                message=f"Disk space check failed: {exc}",
            )

    return _check


# ---------------------------------------------------------------------------
# Route setup
# ---------------------------------------------------------------------------


def setup_health_checks(app: FastAPI) -> HealthChecker:
    """Register built-in health checks and mount probe endpoints on *app*.

    Returns the ``HealthChecker`` instance so callers can add custom checks.
    """
    checker = HealthChecker()

    # Register built-in checks
    checker.register_check("database", _make_database_check(app))
    checker.register_check("redis", _make_redis_check(app))
    checker.register_check("notion_mcp", _make_notion_mcp_check(app))
    checker.register_check("celery", _make_celery_check(app))
    checker.register_check("disk_space", _make_disk_space_check(min_free_gb=1.0))

    # Store on app state for access from other parts of the app
    app.state.health_checker = checker

    # ---- Liveness probe ----

    @app.get("/healthz", tags=["health"])
    async def liveness() -> JSONResponse:
        """Liveness probe -- is the process alive and able to handle requests."""
        return JSONResponse(
            content={
                "status": "alive",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            status_code=200,
        )

    # ---- Readiness probe ----

    @app.get("/readyz", tags=["health"])
    async def readiness() -> JSONResponse:
        """Readiness probe -- are all critical dependencies healthy."""
        report = await checker.check_all()

        if report.status == OverallStatus.UNHEALTHY:
            status_code = 503
        else:
            status_code = 200

        return JSONResponse(
            content=report.to_dict(),
            status_code=status_code,
        )

    # ---- Detailed health ----
    # We enhance the existing /health endpoint by adding a `detailed`
    # query parameter.  The original /health route defined in main.py
    # still works; this adds /health/detailed.

    @app.get("/health/detailed", tags=["health"])
    async def health_detailed() -> JSONResponse:
        """Full health report with all registered checks."""
        report = await checker.check_all()

        # Augment with app-level info when available
        extra: dict[str, Any] = {}
        try:
            if hasattr(app.state, "incident_manager"):
                extra["active_incidents"] = len(
                    app.state.incident_manager.get_active_incidents()
                )
        except Exception:
            pass

        try:
            from src.main import ws_manager

            extra["ws_clients"] = len(ws_manager._connections)
        except Exception:
            pass

        body = report.to_dict()
        body["extra"] = extra

        status_code = 503 if report.status == OverallStatus.UNHEALTHY else 200
        return JSONResponse(content=body, status_code=status_code)

    logger.info("health_checks_configured", checks=list(checker._checks.keys()))
    return checker
