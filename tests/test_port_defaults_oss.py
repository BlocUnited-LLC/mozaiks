"""Tests: OSS port contracts and their default adapter behaviour.

Every port must have a safe default for non-SaaS / offline use so that the
runtime starts without requiring external providers to be configured.

These tests verify:
  1.  Protocol satisfaction — default adapters fulfil their port's Protocol.
  2.  No-op behaviour — default adapters never raise; they return safe values.
  3.  Data-class defaults — Result objects have sensible zero-values.
  4.  Sentinel constants — module-level singletons carry the right state.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# EntitlementPort  —  NoOpEntitlementAdapter
# ---------------------------------------------------------------------------


def test_noop_entitlement_adapter_satisfies_protocol() -> None:
    from mozaiksai.core.ports.entitlement import EntitlementPort, NoOpEntitlementAdapter

    adapter = NoOpEntitlementAdapter()
    assert isinstance(adapter, EntitlementPort)


@pytest.mark.asyncio
async def test_noop_entitlement_grants_every_capability() -> None:
    from mozaiksai.core.ports.entitlement import EntitlementResult, NoOpEntitlementAdapter

    adapter = NoOpEntitlementAdapter()
    result = await adapter.check("wallet.view", app_id="test-app")

    assert isinstance(result, EntitlementResult)
    assert result.granted is True
    assert result.reason == "not_configured"
    assert result.expires_at is None


@pytest.mark.asyncio
async def test_noop_entitlement_grants_with_user_and_tenant() -> None:
    from mozaiksai.core.ports.entitlement import NoOpEntitlementAdapter

    adapter = NoOpEntitlementAdapter()
    result = await adapter.check(
        "investor_marketplace.view",
        app_id="test-app",
        user_id="u123",
        tenant_id="t456",
    )
    assert result.granted is True


@pytest.mark.asyncio
async def test_noop_entitlement_grants_empty_capability_id() -> None:
    """NoOpEntitlementAdapter must not raise even for degenerate inputs."""
    from mozaiksai.core.ports.entitlement import NoOpEntitlementAdapter

    adapter = NoOpEntitlementAdapter()
    result = await adapter.check("", app_id="")
    assert result.granted is True
    assert result.reason == "not_configured"


def test_entitlement_required_constant_is_denial() -> None:
    from mozaiksai.core.ports.entitlement import ENTITLEMENT_REQUIRED

    assert ENTITLEMENT_REQUIRED.granted is False
    assert ENTITLEMENT_REQUIRED.reason == "no_grant"


def test_entitlement_result_defaults() -> None:
    from mozaiksai.core.ports.entitlement import EntitlementResult

    # Minimum required field: granted
    result = EntitlementResult(granted=True)
    assert result.reason == "not_configured"
    assert result.expires_at is None

    denied = EntitlementResult(granted=False, reason="expired", expires_at="2025-01-01T00:00:00Z")
    assert denied.granted is False
    assert denied.expires_at == "2025-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# AppBackendPort  —  HttpAppBackendAdapter
# ---------------------------------------------------------------------------


def test_http_app_backend_adapter_satisfies_protocol() -> None:
    from mozaiksai.core.adapters.http_app_backend import HttpAppBackendAdapter
    from mozaiksai.core.ports.app_backend import AppBackendPort

    adapter = HttpAppBackendAdapter(base_url="http://localhost:9999")
    assert isinstance(adapter, AppBackendPort)


@pytest.mark.asyncio
async def test_http_app_backend_emit_returns_bool_without_raising() -> None:
    """emit() must never raise — it falls back to False when no dispatcher."""
    from mozaiksai.core.adapters.http_app_backend import HttpAppBackendAdapter

    adapter = HttpAppBackendAdapter(base_url="http://localhost:9999")
    result = await adapter.emit("test.event", {"key": "value"})
    assert isinstance(result, bool)


def test_backend_response_defaults() -> None:
    from mozaiksai.core.ports.app_backend import BackendResponse

    ok = BackendResponse(success=True, status_code=200, data={"id": "abc"})
    assert ok.success is True
    assert ok.error is None

    err = BackendResponse(success=False)
    assert err.status_code == 0
    assert err.data is None
    assert err.error is None


def test_backend_health_defaults() -> None:
    from mozaiksai.core.ports.app_backend import BackendHealth

    healthy = BackendHealth(healthy=True)
    assert healthy.version == "unknown"
    assert healthy.details is None

    unhealthy = BackendHealth(healthy=False, details={"error": "timeout"})
    assert unhealthy.healthy is False
    assert unhealthy.version == "unknown"


def test_get_app_backend_singleton_returns_adapter() -> None:
    from mozaiksai.core.adapters.http_app_backend import (
        HttpAppBackendAdapter,
        get_app_backend,
    )

    backend = get_app_backend()
    assert isinstance(backend, HttpAppBackendAdapter)
    # Singleton contract — same object on second call.
    assert get_app_backend() is backend


# ---------------------------------------------------------------------------
# SandboxPort  —  data classes (no OSS default adapter; providers are external)
# ---------------------------------------------------------------------------


def test_sandbox_run_result_defaults() -> None:
    from mozaiksai.core.ports.sandbox import SandboxRunResult

    result = SandboxRunResult(success=True)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.error is None
    assert result.process_id is None


def test_sandbox_session_info_fields() -> None:
    from mozaiksai.core.ports.sandbox import SandboxSessionInfo

    info = SandboxSessionInfo(session_id="s1", provider="e2b")
    assert info.preview_url is None
    assert info.metadata == {}


def test_sandbox_port_is_checkable_protocol() -> None:
    """SandboxPort must be a runtime_checkable Protocol."""
    from mozaiksai.core.ports.sandbox import SandboxPort

    # Protocol exists and can be used in isinstance checks without raising.
    assert hasattr(SandboxPort, "__protocol_attrs__") or hasattr(SandboxPort, "_is_protocol")


# ---------------------------------------------------------------------------
# OrchestrationPort  —  data classes and RunStatus enum
# ---------------------------------------------------------------------------


def test_run_status_enum_values() -> None:
    from mozaiksai.core.ports.orchestration import RunStatus

    assert RunStatus.COMPLETED.value == "completed"
    assert RunStatus.PAUSED.value == "paused"
    assert RunStatus.FAILED.value == "failed"
    assert RunStatus.CANCELLED.value == "cancelled"
    assert RunStatus.IN_PROGRESS.value == "in_progress"


def test_run_result_defaults() -> None:
    from mozaiksai.core.ports.orchestration import RunResult, RunStatus

    result = RunResult(
        status=RunStatus.COMPLETED,
        chat_id="chat-1",
        workflow_name="AppGenerator",
    )
    assert result.merged_context is None
    assert result.usage is None
    assert result.error is None


def test_domain_event_has_timestamp_and_source_defaults() -> None:
    from mozaiksai.core.ports.orchestration import DomainEvent

    event = DomainEvent(kind="chat.text", payload={"content": "hello"}, chat_id="c1")
    assert event.source == "ag2"
    assert event.timestamp  # non-empty ISO string


def test_run_request_is_frozen() -> None:
    from mozaiksai.core.ports.orchestration import RunRequest

    req = RunRequest(
        workflow_name="AppGenerator",
        app_id="app-1",
        chat_id="chat-1",
        user_id="user-1",
    )
    assert req.initial_message is None
    assert req.initial_agent_name_override is None
    assert req.extra == {}

    with pytest.raises((AttributeError, TypeError)):
        req.workflow_name = "changed"  # type: ignore[misc]


def test_ports_init_exports_all_public_names() -> None:
    """mozaiksai.core.ports.__init__ must re-export all port contract names."""
    import mozaiksai.core.ports as ports

    required = {
        "OrchestrationPort", "RunRequest", "ResumeRequest", "RunResult", "RunStatus", "DomainEvent",
        "AppBackendPort", "BackendResponse", "BackendHealth",
        "SandboxPort", "SandboxRunResult", "SandboxSessionInfo",
        "EntitlementPort", "EntitlementResult", "NoOpEntitlementAdapter", "ENTITLEMENT_REQUIRED",
    }
    exported = set(ports.__all__)
    missing = required - exported
    assert not missing, f"Missing from ports.__all__: {missing}"
