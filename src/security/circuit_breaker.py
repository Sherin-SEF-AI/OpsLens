"""Circuit breaker pattern for OpsLens external service calls.

Prevents cascading failures by short-circuiting calls to unhealthy
dependencies (Notion MCP, LLM APIs, Slack, GitHub) when repeated failures
are detected.  After a configurable recovery timeout the circuit allows a
single probe request through to test whether the dependency has recovered.

States:
    CLOSED  -- Normal operation.  Failures are counted.
    OPEN    -- Failures exceeded threshold.  All calls fail immediately.
    HALF_OPEN -- Recovery timeout elapsed.  One probe call is allowed.
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Tuple, Type

import structlog

logger = structlog.get_logger()


class CircuitState(str, enum.Enum):
    """Possible states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted against an OPEN circuit breaker.

    Attributes:
        breaker_name: Name of the circuit breaker that rejected the call.
        recovery_remaining: Seconds until the circuit transitions to HALF_OPEN.
    """

    def __init__(self, breaker_name: str, recovery_remaining: float) -> None:
        self.breaker_name = breaker_name
        self.recovery_remaining = recovery_remaining
        super().__init__(
            f"Circuit '{breaker_name}' is OPEN. "
            f"Recovery in {recovery_remaining:.1f}s."
        )


@dataclass
class CircuitStats:
    """Runtime statistics for a circuit breaker."""

    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_failure_time: float | None = None
    last_success_time: float | None = None
    state: CircuitState = CircuitState.CLOSED
    times_opened: int = 0
    last_opened_time: float | None = None


class CircuitBreaker:
    """Async-compatible circuit breaker for protecting external service calls.

    Args:
        name: Human-readable name for logging.
        failure_threshold: Number of consecutive failures before opening.
        recovery_timeout: Seconds to wait in OPEN state before probing.
        expected_exceptions: Tuple of exception types that count as failures.
            Other exceptions propagate without tripping the breaker.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current circuit state, accounting for automatic OPEN -> HALF_OPEN
        transition when the recovery timeout has elapsed."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def stats(self) -> dict[str, Any]:
        """Return a snapshot of circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "total_calls": self._stats.total_calls,
            "total_successes": self._stats.total_successes,
            "total_failures": self._stats.total_failures,
            "consecutive_failures": self._stats.consecutive_failures,
            "last_failure_time": self._stats.last_failure_time,
            "last_success_time": self._stats.last_success_time,
            "times_opened": self._stats.times_opened,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }

    async def call(self, async_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *async_func* with circuit breaker protection.

        Args:
            async_func: The async callable to protect.
            *args: Positional arguments forwarded to *async_func*.
            **kwargs: Keyword arguments forwarded to *async_func*.

        Returns:
            The return value of *async_func*.

        Raises:
            CircuitOpenError: If the circuit is OPEN and recovery timeout
                has not yet elapsed.
            Exception: Any exception from *async_func* is re-raised after
                the breaker records the failure.
        """
        async with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                remaining = self.recovery_timeout - (time.monotonic() - self._opened_at)
                raise CircuitOpenError(self.name, max(0.0, remaining))

            if current_state == CircuitState.HALF_OPEN:
                logger.info(
                    "circuit_breaker_probe",
                    name=self.name,
                    message="Allowing probe request in HALF_OPEN state",
                )

        # Execute outside the lock so we don't hold it during I/O
        self._stats.total_calls += 1
        try:
            result = await async_func(*args, **kwargs)
        except self.expected_exceptions as exc:
            await self._record_failure(exc)
            raise
        else:
            await self._record_success()
            return result

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state.

        Clears consecutive failure count but preserves cumulative stats.
        """
        previous = self._state
        self._state = CircuitState.CLOSED
        self._stats.state = CircuitState.CLOSED
        self._stats.consecutive_failures = 0
        logger.info(
            "circuit_breaker_manual_reset",
            name=self.name,
            previous_state=previous.value,
        )

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------

    async def _record_success(self) -> None:
        """Record a successful call, potentially closing the circuit."""
        async with self._lock:
            now = time.monotonic()
            self._stats.total_successes += 1
            self._stats.last_success_time = now

            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                # Successful probe -> close the circuit
                previous = self._state
                self._state = CircuitState.CLOSED
                self._stats.state = CircuitState.CLOSED
                self._stats.consecutive_failures = 0
                logger.info(
                    "circuit_breaker_closed",
                    name=self.name,
                    previous_state=previous.value,
                    message="Probe succeeded, circuit closed",
                )
            else:
                # Already closed, just reset the failure counter
                self._stats.consecutive_failures = 0

    async def _record_failure(self, exc: BaseException) -> None:
        """Record a failed call, potentially opening the circuit."""
        async with self._lock:
            now = time.monotonic()
            self._stats.total_failures += 1
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = now
            self._last_failure_time = now

            if self.state == CircuitState.HALF_OPEN:
                # Probe failed -> reopen
                self._state = CircuitState.OPEN
                self._stats.state = CircuitState.OPEN
                self._opened_at = now
                self._stats.times_opened += 1
                logger.warning(
                    "circuit_breaker_reopened",
                    name=self.name,
                    error=str(exc),
                    recovery_timeout=self.recovery_timeout,
                )
            elif (
                self._state == CircuitState.CLOSED
                and self._stats.consecutive_failures >= self.failure_threshold
            ):
                # Threshold exceeded -> open
                self._state = CircuitState.OPEN
                self._stats.state = CircuitState.OPEN
                self._opened_at = now
                self._stats.times_opened += 1
                self._stats.last_opened_time = now
                logger.warning(
                    "circuit_breaker_opened",
                    name=self.name,
                    consecutive_failures=self._stats.consecutive_failures,
                    threshold=self.failure_threshold,
                    recovery_timeout=self.recovery_timeout,
                    last_error=str(exc),
                )
            else:
                logger.debug(
                    "circuit_breaker_failure",
                    name=self.name,
                    consecutive_failures=self._stats.consecutive_failures,
                    threshold=self.failure_threshold,
                    error=str(exc),
                )

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self.state.value}, "
            f"failures={self._stats.consecutive_failures}/{self.failure_threshold})"
        )


# ---------------------------------------------------------------------------
# Pre-configured circuit breakers for OpsLens services
# ---------------------------------------------------------------------------

mcp_circuit: CircuitBreaker = CircuitBreaker(
    name="notion_mcp",
    failure_threshold=5,
    recovery_timeout=60.0,
)
"""Circuit breaker for Notion MCP server calls."""

llm_circuit: CircuitBreaker = CircuitBreaker(
    name="llm_api",
    failure_threshold=3,
    recovery_timeout=30.0,
)
"""Circuit breaker for LLM API calls (Gemini / Anthropic)."""

slack_circuit: CircuitBreaker = CircuitBreaker(
    name="slack",
    failure_threshold=5,
    recovery_timeout=120.0,
)
"""Circuit breaker for Slack API / webhook calls."""

github_circuit: CircuitBreaker = CircuitBreaker(
    name="github",
    failure_threshold=5,
    recovery_timeout=120.0,
)
"""Circuit breaker for GitHub API calls."""
