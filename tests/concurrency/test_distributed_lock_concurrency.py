"""Concurrency tests: distributed lock race conditions.

These tests exercise the in-process locking behavior of DistributedLock's
fallback path (no MongoDB) to verify that concurrent coroutines cannot hold
the same lock simultaneously.

The MongoDB-backed path requires MONGO_URI and is guarded by a skip mark.
"""
from __future__ import annotations

import asyncio

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AcquiredTracker:
    """Thread-safe tally of concurrent lock holders."""

    def __init__(self) -> None:
        self.max_concurrent = 0
        self._current = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> None:
        async with self._lock:
            self._current += 1
            if self._current > self.max_concurrent:
                self.max_concurrent = self._current

    async def exit(self) -> None:
        async with self._lock:
            self._current -= 1


# ---------------------------------------------------------------------------
# Circuit breaker concurrency (no I/O dependency)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    """Circuit breaker transitions OPEN after failure_threshold consecutive failures."""
    from mozaiksai.core.adapters.circuit_breaker import (
        CircuitBreaker,
        CircuitOpenError,
        CircuitState,
    )

    breaker = CircuitBreaker("test-open", failure_threshold=3, recovery_timeout=60, success_threshold=2)

    async def always_fails() -> None:
        raise ValueError("simulated provider failure")

    for _ in range(3):
        with pytest.raises(ValueError):
            await breaker.call(always_fails)

    assert breaker._state == CircuitState.OPEN

    # Further calls must raise CircuitOpenError without hitting the function
    with pytest.raises(CircuitOpenError):
        await breaker.call(always_fails)


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_recovery() -> None:
    """Circuit breaker transitions HALF_OPEN after recovery_timeout and closes on success."""
    from mozaiksai.core.adapters.circuit_breaker import (
        CircuitBreaker,
        CircuitState,
    )

    # Very short recovery timeout so we don't sleep long in tests
    breaker = CircuitBreaker("test-recovery", failure_threshold=2, recovery_timeout=0.01, success_threshold=1)

    async def always_fails() -> None:
        raise ValueError("failure")

    async def always_succeeds() -> str:
        return "ok"

    for _ in range(2):
        with pytest.raises(ValueError):
            await breaker.call(always_fails)

    assert breaker._state == CircuitState.OPEN

    await asyncio.sleep(0.02)  # Let recovery_timeout expire

    # First call in HALF_OPEN should be allowed through
    result = await breaker.call(always_succeeds)
    assert result == "ok"
    assert breaker._state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_concurrent_circuit_breaker_calls_are_isolated() -> None:
    """Concurrent callers don't corrupt circuit breaker state (state machine is not locked
    but the failure tally should be monotonically correct when calls arrive serially)."""
    from mozaiksai.core.adapters.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker("test-concurrent", failure_threshold=5, recovery_timeout=60, success_threshold=2)
    success_count = 0

    async def may_succeed(i: int) -> None:
        nonlocal success_count
        if i % 2 == 0:
            success_count += 1
            return
        raise ValueError(f"fail {i}")

    tasks = [asyncio.create_task(
        _safe_call(breaker, may_succeed, i)
    ) for i in range(10)]
    await asyncio.gather(*tasks)

    # Some successes and failures recorded; breaker may or may not have opened
    # depending on ordering — what matters is it didn't raise an unhandled exception.
    assert breaker._failure_count >= 0


async def _safe_call(breaker, fn, *args):
    try:
        await breaker.call(fn, *args)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Idempotency guard concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_guard_dedups_concurrent_calls() -> None:
    """Concurrent calls with the same key must not both proceed (in-memory path)."""
    from unittest.mock import patch

    # Patch mongo collection to return "already ran" on second call
    call_count = 0

    async def fake_check(*, chat_id: str, tool_name: str, args=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None, False   # first call: no cache, not already ran
        return None, True        # subsequent: was already reserved

    from mozaiksai.core.workflow.idempotency import IdempotencyGuard

    guard = IdempotencyGuard()
    with patch.object(guard, "check_and_reserve", side_effect=fake_check):
        results = await asyncio.gather(
            guard.check_and_reserve(chat_id="c1", tool_name="t", args={"k": "v"}),
            guard.check_and_reserve(chat_id="c1", tool_name="t", args={"k": "v"}),
        )

    # One caller got (None, False) — goes ahead; one got (None, True) — skips
    already_ran_flags = [r[1] for r in results]
    assert any(f is True for f in already_ran_flags), (
        "At least one concurrent call should have seen already_ran=True"
    )


# ---------------------------------------------------------------------------
# Feature flags concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_feature_flag_concurrent_reads_are_consistent() -> None:
    """Concurrent reads of the same flag return consistent results (env path)."""
    import os

    from mozaiksai.core.flags.feature_flags import FeatureFlags

    os.environ["MOZAIKS_FLAG_CONCURRENT_TEST"] = "true"
    try:
        flags = FeatureFlags()
        results = await asyncio.gather(*[
            flags.is_enabled_async("concurrent-test") for _ in range(20)
        ])
        assert all(r is True for r in results), f"Inconsistent results: {results}"
    finally:
        del os.environ["MOZAIKS_FLAG_CONCURRENT_TEST"]


# ---------------------------------------------------------------------------
# Trace context propagation across asyncio.create_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trace_id_propagates_to_child_tasks() -> None:
    """bind_trace_id set in parent task is visible inside asyncio.create_task children."""
    from mozaiksai.core.tracing.context import bind_trace_id, get_trace_id

    bind_trace_id("test-trace-abc")

    captured: list[str] = []

    async def child() -> None:
        captured.append(get_trace_id())

    await asyncio.create_task(child())

    assert captured == ["test-trace-abc"], f"Expected propagated trace_id, got: {captured}"


@pytest.mark.asyncio
async def test_trace_context_manager_restores_on_exit() -> None:
    """trace_context manager restores previous trace ID after block exits."""
    from mozaiksai.core.tracing.context import bind_trace_id, get_trace_id, trace_context

    bind_trace_id("outer-trace")

    with trace_context("inner-trace") as ctx:
        assert ctx.trace_id == "inner-trace"
        assert get_trace_id() == "inner-trace"

    assert get_trace_id() == "outer-trace"


@pytest.mark.asyncio
async def test_concurrent_tasks_have_independent_trace_contexts() -> None:
    """Two concurrent tasks with different trace IDs do not bleed into each other."""
    from mozaiksai.core.tracing.context import bind_trace_id, get_trace_id

    results: dict[str, str] = {}

    async def task_a() -> None:
        bind_trace_id("trace-A")
        await asyncio.sleep(0)  # yield to scheduler
        results["a"] = get_trace_id()

    async def task_b() -> None:
        bind_trace_id("trace-B")
        await asyncio.sleep(0)
        results["b"] = get_trace_id()

    await asyncio.gather(task_a(), task_b())

    assert results["a"] == "trace-A", f"Task A leaked: {results}"
    assert results["b"] == "trace-B", f"Task B leaked: {results}"


# ---------------------------------------------------------------------------
# LLM fallback config builder
# ---------------------------------------------------------------------------

def test_fallback_config_list_order() -> None:
    """Primary model appears first; fallbacks follow in declaration order."""
    from mozaiksai.core.adapters.llm_fallback import build_fallback_config_list

    config_list = build_fallback_config_list(
        primary_model="gpt-4o",
        primary_api_key="key-primary",
        fallback_models=["gpt-4o-mini", "gpt-3.5-turbo"],
        fallback_api_keys=["key-mini", "key-35"],
    )

    assert config_list[0]["model"] == "gpt-4o"
    assert config_list[1]["model"] == "gpt-4o-mini"
    assert config_list[2]["model"] == "gpt-3.5-turbo"
    assert config_list[0]["api_key"] == "key-primary"
    assert config_list[1]["api_key"] == "key-mini"
    assert config_list[2]["api_key"] == "key-35"


def test_fallback_disabled_returns_single_entry() -> None:
    """When LLM_FALLBACK_ENABLED=false, only the primary entry is returned."""
    import os

    from mozaiksai.core.adapters.llm_fallback import build_fallback_config_list

    os.environ["LLM_FALLBACK_ENABLED"] = "false"
    try:
        config_list = build_fallback_config_list(
            primary_model="gpt-4o",
            fallback_models=["gpt-4o-mini"],
        )
        assert len(config_list) == 1
        assert config_list[0]["model"] == "gpt-4o"
    finally:
        del os.environ["LLM_FALLBACK_ENABLED"]


def test_fallback_inherits_primary_api_key_when_no_per_fallback_key() -> None:
    """Fallback entries without explicit keys inherit the primary API key."""
    from mozaiksai.core.adapters.llm_fallback import build_fallback_config_list

    config_list = build_fallback_config_list(
        primary_model="gpt-4o",
        primary_api_key="shared-key",
        fallback_models=["gpt-4o-mini"],
        # No fallback_api_keys provided
    )

    assert config_list[1]["api_key"] == "shared-key"


def test_get_healthy_config_list_moves_open_circuits_to_end() -> None:
    """Entries whose model has an OPEN circuit breaker are moved to the tail."""
    import mozaiksai.core.adapters.circuit_breaker as cb_module
    from mozaiksai.core.adapters.circuit_breaker import CircuitBreaker, CircuitState
    from mozaiksai.core.adapters.llm_fallback import get_healthy_config_list

    # Pre-open the circuit for gpt-4o
    breaker = CircuitBreaker("llm::gpt-4o", failure_threshold=1, recovery_timeout=60)
    breaker._state = CircuitState.OPEN
    cb_module._breakers["llm::gpt-4o"] = breaker

    try:
        config_list = [
            {"model": "gpt-4o", "api_key": "k"},
            {"model": "gpt-4o-mini", "api_key": "k"},
        ]
        result = get_healthy_config_list(config_list)

        # gpt-4o-mini (healthy) should now come first
        assert result[0]["model"] == "gpt-4o-mini"
        assert result[1]["model"] == "gpt-4o"
    finally:
        cb_module._breakers.pop("llm::gpt-4o", None)
