"""Tests for the async circuit breaker pattern."""

import asyncio
import time

import pytest

from src.security.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _success():
    return "ok"


async def _failure():
    raise RuntimeError("boom")


async def _custom_error():
    raise ValueError("custom error")


# ---------------------------------------------------------------------------
# Basic state transitions
# ---------------------------------------------------------------------------

class TestCircuitBreakerStates:
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_successful_calls_stay_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(10):
            result = await cb.call(_success)
            assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb.stats["total_successes"] == 10

    @pytest.mark.asyncio
    async def test_failures_increment_counter(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)
        assert cb.stats["consecutive_failures"] == 3
        assert cb.state == CircuitState.CLOSED  # not yet at threshold

    @pytest.mark.asyncio
    async def test_threshold_opens_circuit(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)
        assert cb.state == CircuitState.OPEN
        assert cb.stats["times_opened"] == 1

    @pytest.mark.asyncio
    async def test_open_circuit_raises_immediately(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)
        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(_success)
        assert exc_info.value.breaker_name == "test"
        assert exc_info.value.recovery_remaining > 0

    @pytest.mark.asyncio
    async def test_recovery_timeout_transitions_to_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)
        assert cb._state == CircuitState.OPEN

        # Wait for recovery
        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_successful_probe_closes_circuit(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # Successful probe
        result = await cb.call(_success)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb.stats["consecutive_failures"] == 0

    @pytest.mark.asyncio
    async def test_failed_probe_reopens_circuit(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # Failed probe
        with pytest.raises(RuntimeError):
            await cb.call(_failure)
        assert cb.state == CircuitState.OPEN
        assert cb.stats["times_opened"] == 2

    @pytest.mark.asyncio
    async def test_reset_returns_to_closed(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_failure)
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.stats["consecutive_failures"] == 0
        # Cumulative stats preserved
        assert cb.stats["total_failures"] == 2


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestCircuitBreakerStats:
    @pytest.mark.asyncio
    async def test_stats_structure(self):
        cb = CircuitBreaker("test-stats", failure_threshold=5, recovery_timeout=30.0)
        stats = cb.stats
        assert stats["name"] == "test-stats"
        assert stats["state"] == "closed"
        assert stats["total_calls"] == 0
        assert stats["total_successes"] == 0
        assert stats["total_failures"] == 0
        assert stats["consecutive_failures"] == 0
        assert stats["failure_threshold"] == 5
        assert stats["recovery_timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_stats_track_calls(self):
        cb = CircuitBreaker("test", failure_threshold=10)
        await cb.call(_success)
        await cb.call(_success)
        with pytest.raises(RuntimeError):
            await cb.call(_failure)

        stats = cb.stats
        assert stats["total_calls"] == 3
        assert stats["total_successes"] == 2
        assert stats["total_failures"] == 1
        assert stats["consecutive_failures"] == 1

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failures(self):
        cb = CircuitBreaker("test", failure_threshold=10)
        with pytest.raises(RuntimeError):
            await cb.call(_failure)
        with pytest.raises(RuntimeError):
            await cb.call(_failure)
        assert cb.stats["consecutive_failures"] == 2

        await cb.call(_success)
        assert cb.stats["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Expected exceptions
# ---------------------------------------------------------------------------

class TestExpectedExceptions:
    @pytest.mark.asyncio
    async def test_only_expected_exceptions_trip_breaker(self):
        """Only RuntimeError trips the breaker, ValueError propagates without counting."""
        cb = CircuitBreaker(
            "test",
            failure_threshold=2,
            recovery_timeout=60.0,
            expected_exceptions=(RuntimeError,),
        )
        # ValueError should propagate but NOT trip the breaker
        with pytest.raises(ValueError):
            await cb.call(_custom_error)

        assert cb.stats["consecutive_failures"] == 0
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# CircuitOpenError
# ---------------------------------------------------------------------------

class TestCircuitOpenError:
    def test_error_attributes(self):
        err = CircuitOpenError("my-service", 42.5)
        assert err.breaker_name == "my-service"
        assert err.recovery_remaining == 42.5
        assert "my-service" in str(err)
        assert "OPEN" in str(err)

    def test_repr(self):
        cb = CircuitBreaker("repr-test", failure_threshold=3)
        r = repr(cb)
        assert "repr-test" in r
        assert "closed" in r
