from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.composition import (
    ExecutorRegistry,
    ModuleActionDispatchRequest,
    ModuleDispatchAuthority,
    ModuleDispatchMetadata,
    ModuleDispatchProvenance,
    ModuleDispatchScope,
    ModuleExecutor,
    PlatformExtensionBundle,
    PlatformHookRegistry,
    dispatch_module_action,
)


@pytest.fixture(autouse=True)
def _fast_audit_logger(monkeypatch):
    module_executor_mod = importlib.import_module(
        "mozaiksai.core.runtime.composition.module_executor"
    )

    class _AuditLogger:
        async def log_module_action(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(module_executor_mod, "get_audit_logger", lambda: _AuditLogger())


class _OrdersModule:
    async def restricted(self, ctx, **params):  # noqa: ANN001, ANN003
        return {
            "params": params,
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.workspace_id,
            "permissions": ctx.permissions,
            "auth_token": ctx.auth_token,
            "correlation_id": ctx.correlation_id,
            "authority_kind": ctx.dispatch_authority.kind if ctx.dispatch_authority else None,
            "permission_mode": (
                ctx.dispatch_authority.permission_mode if ctx.dispatch_authority else None
            ),
            "authority_permissions": (
                list(ctx.dispatch_authority.permissions) if ctx.dispatch_authority else None
            ),
            "provenance_surface": (
                ctx.dispatch_provenance.surface if ctx.dispatch_provenance else None
            ),
            "workflow_name": (
                ctx.dispatch_provenance.workflow_name if ctx.dispatch_provenance else None
            ),
            "workflow_run_id": (
                ctx.dispatch_provenance.workflow_run_id if ctx.dispatch_provenance else None
            ),
            "causation_id": (
                ctx.dispatch_provenance.causation_id if ctx.dispatch_provenance else None
            ),
            "audit_authority_kind": (
                ctx.dispatch_audit.authority_kind if ctx.dispatch_audit else None
            ),
            "audit_correlation_id": (
                ctx.dispatch_audit.correlation_id if ctx.dispatch_audit else None
            ),
        }


def _app_with_executor() -> SimpleNamespace:
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersModule(),
        action_permissions={"restricted": ["orders.read"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    return SimpleNamespace(state=SimpleNamespace(executor_registry=registry))


@pytest.mark.asyncio
async def test_dispatch_module_action_preserves_scope_metadata_and_permissions() -> None:
    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            params={"limit": 5},
            scope=ModuleDispatchScope(
                app_id="app-1",
                user_id="user-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            ),
            metadata=ModuleDispatchMetadata(
                auth_token="token-1",
                correlation_id="corr-1",
            ),
            authority=ModuleDispatchAuthority(
                kind="app_internal",
                permission_mode="enforce",
                reason="app-local dispatch",
                actor_id="user-1",
                permissions=("orders.read",),
            ),
        ),
        app=_app_with_executor(),
    )

    assert result.success is True
    assert result.data == {
        "params": {"limit": 5},
        "app_id": "app-1",
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "permissions": ["orders.read"],
        "auth_token": "token-1",
        "correlation_id": "corr-1",
        "authority_kind": "app_internal",
        "permission_mode": "enforce",
        "authority_permissions": ["orders.read"],
        "provenance_surface": "app_local_dispatch",
        "workflow_name": None,
        "workflow_run_id": None,
        "causation_id": None,
        "audit_authority_kind": "app_internal",
        "audit_correlation_id": "corr-1",
    }


@pytest.mark.asyncio
async def test_dispatch_module_action_preserves_permission_enforcement() -> None:
    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            scope=ModuleDispatchScope(app_id="app-1", user_id="user-1"),
            authority=ModuleDispatchAuthority(
                kind="app_internal",
                permission_mode="enforce",
                reason="app-local dispatch",
                actor_id="user-1",
            ),
        ),
        app=_app_with_executor(),
    )

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_dispatch_module_action_accepts_workflow_authority_and_provenance(monkeypatch) -> None:
    policy_inputs = []

    async def before_module_execution(policy_input):
        policy_inputs.append(policy_input)
        return True

    registry = PlatformHookRegistry()
    registry._register_bundle(
        PlatformExtensionBundle(
            before_module_execution=before_module_execution,
        )
    )
    monkeypatch.setattr(PlatformHookRegistry, "_instance", registry)

    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            params={"limit": 10},
            scope=ModuleDispatchScope(
                app_id="app-1",
                user_id="workflow-user",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            ),
            metadata=ModuleDispatchMetadata(correlation_id="corr-workflow"),
            authority=ModuleDispatchAuthority(
                kind="workflow",
                permission_mode="enforce",
                reason="campaign asset workflow dispatch",
                actor_id="workflow-user",
                permissions=("orders.read",),
            ),
            provenance=ModuleDispatchProvenance(
                surface="workflow_tool",
                workflow_name="CampaignAssetGeneratorWorkflow",
                workflow_run_id="run-123",
                correlation_id="corr-workflow",
                causation_id="cause-123",
            ),
        ),
        app=_app_with_executor(),
    )

    assert result.success is True
    assert result.data["authority_kind"] == "workflow"
    assert result.data["permission_mode"] == "enforce"
    assert result.data["authority_permissions"] == ["orders.read"]
    assert result.data["permissions"] == ["orders.read"]
    assert result.data["provenance_surface"] == "workflow_tool"
    assert result.data["workflow_name"] == "CampaignAssetGeneratorWorkflow"
    assert result.data["workflow_run_id"] == "run-123"
    assert result.data["causation_id"] == "cause-123"
    assert result.data["audit_authority_kind"] == "workflow"
    assert result.data["audit_correlation_id"] == "corr-workflow"

    assert len(policy_inputs) == 1
    assert policy_inputs[0].authority.kind == "workflow"
    assert policy_inputs[0].authority.permission_mode == "enforce"
    assert policy_inputs[0].authority.permissions == ("orders.read",)
    assert policy_inputs[0].provenance.workflow_name == "CampaignAssetGeneratorWorkflow"
    assert policy_inputs[0].provenance.workflow_run_id == "run-123"
    assert policy_inputs[0].permission_check.checked is True
    assert policy_inputs[0].permission_check.allowed is True


@pytest.mark.asyncio
async def test_dispatch_module_action_workflow_authority_still_requires_permissions() -> None:
    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            scope=ModuleDispatchScope(app_id="app-1", user_id="workflow-user"),
            authority=ModuleDispatchAuthority(
                kind="workflow",
                permission_mode="enforce",
                reason="workflow dispatch",
                actor_id="workflow-user",
            ),
            provenance=ModuleDispatchProvenance(
                surface="workflow_tool",
                workflow_name="CampaignAssetGeneratorWorkflow",
            ),
        ),
        app=_app_with_executor(),
    )

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_dispatch_module_action_requires_explicit_authority() -> None:
    with pytest.raises(TypeError):
        ModuleActionDispatchRequest(  # type: ignore[call-arg]
            module="orders",
            action="restricted",
            scope=ModuleDispatchScope(app_id="app-1", user_id="user-1"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    [
        ModuleDispatchAuthority(
            kind="framework_internal",
            permission_mode="enforce",
            reason="not allowed",
        ),
        ModuleDispatchAuthority(
            kind="operator_internal",
            permission_mode="enforce",
            reason="not allowed",
        ),
        ModuleDispatchAuthority(
            kind="event_reaction",
            permission_mode="enforce",
            reason="not allowed",
        ),
    ],
)
async def test_dispatch_module_action_rejects_public_unsafe_authority(authority) -> None:
    request = ModuleActionDispatchRequest(
        module="orders",
        action="restricted",
        scope=ModuleDispatchScope(app_id="app-1", user_id="user-1"),
        authority=authority,
    )

    with pytest.raises(ValueError):
        await dispatch_module_action(request, app=_app_with_executor())


@pytest.mark.asyncio
async def test_dispatch_module_action_preserves_supplied_authority_exactly(monkeypatch) -> None:
    policy_inputs = []

    async def before_module_execution(policy_input):
        policy_inputs.append(policy_input)
        return True

    registry = PlatformHookRegistry()
    registry._register_bundle(
        PlatformExtensionBundle(before_module_execution=before_module_execution)
    )
    monkeypatch.setattr(PlatformHookRegistry, "_instance", registry)

    supplied = ModuleDispatchAuthority(
        kind="authenticated_user",
        permission_mode="enforce",
        reason="caller-stated reason",
        actor_id="user-42",
        permissions=("orders.read", "orders.write"),
    )
    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            scope=ModuleDispatchScope(app_id="app-1", user_id="user-42"),
            authority=supplied,
        ),
        app=_app_with_executor(),
    )

    assert result.success is True
    # The facade passes the caller's authority through unchanged — same object,
    # no rebuilt kind/reason/actor/permissions.
    assert policy_inputs[0].authority is supplied
