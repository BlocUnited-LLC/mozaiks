from __future__ import annotations

import asyncio
import json
from copy import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from starlette.websockets import WebSocketState

from factory_app.app.modules.security_readiness.backend.handler import SecurityReadinessModule
from factory_app.app.modules.security_readiness.backend.service import SecurityReadinessService
from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    inspect_generated_app_security,
)
from factory_app.workflows.SecurityReadiness.tools.record_security_findings import (
    record_security_findings,
)
from mozaiksai.core.auth.adapters.base import UserClaims
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow import module_tools
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from tests.test_security_readiness_module import _WrapperStylePersistence

ROOT = Path(__file__).resolve().parents[1]
RUN = ("SecurityReadiness", "factory-host", "chat_1")


def _bridge(project="project_a", *, user_id="owner_1"):
    config = yaml.safe_load((ROOT / "factory_app/workflows/SecurityReadiness/context_variables.yaml").read_text())
    definitions = load_context_variables_config(config).definitions
    policy = build_context_authority_policy(workflow_name=RUN[0], definitions=definitions)
    bridge = ContextVariablesBridge({
        "app_id": RUN[1], "user_id": user_id, "build_id": "build_1", "build_registry_id": project,
        "artifact_version_id": "artifact_1", "security_readiness_mode": "advisory",
        "generated_files": {"app/app.json": json.dumps({"authRequired": True})},
    }, authority_policy=policy)
    bridge._bind_run(RUN, policy)
    return bridge


@pytest.fixture
def live_runtime(monkeypatch):
    manifest = yaml.safe_load((ROOT / "factory_app/app/modules/security_readiness/module.yaml").read_text())
    event_contract = yaml.safe_load((ROOT / "factory_app/app/modules/security_readiness/contracts/events.yaml").read_text())
    executor = ModuleExecutor()
    executor.register(
        "security_readiness", SecurityReadinessModule(),
        action_method_map={action["id"]: action["handler_method"] for action in manifest["actions"]},
        action_permissions={action["id"]: action["permissions"] for action in manifest["actions"]},
        action_schemas={action["id"]: {"input": action["input_schema"], "output": action["output_schema"]} for action in manifest["actions"]},
        action_emits={action["id"]: action.get("emits", []) for action in manifest["actions"]},
        event_payload_schemas={event["type"]: event["payload_schema"] for event in event_contract["events"]},
    )
    persistence = _WrapperStylePersistence(RUN[1])
    scopes = []

    def storage(request):
        scopes.append(request)
        return persistence

    monkeypatch.setattr(executor, "_build_persistence_context", storage)
    monkeypatch.setattr(executor, "_emit_dispatch_audit", AsyncMock())
    principal = WebSocketUser.from_claims(UserClaims(
        user_id="owner_1", app_id=RUN[1], tenant_id="tenant_1", workspace_id="workspace_1",
        scopes=["security_readiness.manage"], provider="keycloak", raw_claims={"exp": 9_999_999_999},
    ))
    app = SimpleNamespace(state=SimpleNamespace(
        executor_registry=SimpleNamespace(module_executor=executor),
        module_action_surfaces={"security_readiness": {action["id"]: action.get("api_surface") for action in manifest["actions"]}},
    ))
    socket = SimpleNamespace(
        state=SimpleNamespace(user=principal), app=app,
        client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
    )
    conn = {"websocket": socket, "active": True, "app_id": RUN[1], "user_id": "owner_1"}
    transport = SimpleNamespace(connections={RUN[2]: conn})
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(module_tools, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(module_tools, "get_platform_hooks", PlatformHookRegistry)
    return SimpleNamespace(principal=principal, connection=conn, transport=transport, persistence=persistence, scopes=scopes)


@pytest.mark.asyncio
async def test_real_bound_scan_records_through_executor_and_project_filter(live_runtime):
    for project in ("project_a", "project_b", "project_a"):
        bridge = _bridge(project)
        scanned = await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
        assert scanned["status"] == "attention_required"
        recorded = await _wrap_tool_with_context(record_security_findings, bridge)()
        assert recorded["persisted"] is True
    rows = live_runtime.persistence.collection_handle.rows
    assert len(rows) == 2
    assert {row["build_registry_id"] for row in rows} == {"project_a", "project_b"}
    assert all(row["owner_user_id"] == "owner_1" for row in rows)
    assert all(row["app_id"] == RUN[1] and row["remediation"] and row["evidence_ref"] for row in rows)
    request = live_runtime.scopes[0]
    assert request.authority.kind == "workflow"
    assert request.authority.permission_mode == "enforce"
    assert request.auth_token is None
    assert (request.tenant_id, request.workspace_id) == ("tenant_1", "workspace_1")
    ctx = SimpleNamespace(user_id="owner_1", persistence=live_runtime.persistence)
    visible = await SecurityReadinessService().list_findings(ctx, app_id=RUN[1], build_registry_id="project_b")
    assert visible["count"] == 1
    assert visible["findings"][0]["build_registry_id"] == "project_b"


@pytest.mark.asyncio
async def test_permission_denial_remains_visible_and_context_cannot_grant(live_runtime):
    live_runtime.principal.scopes = []
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is False
    assert result["persistence_error"] == "PERMISSION_DENIED"
    assert bridge.get("security_readiness_summary")["persistence_error"] == "PERMISSION_DENIED"
    assert not live_runtime.persistence.collection_handle.rows
    with pytest.raises(ContextAuthorityError):
        await _wrap_tool_with_context(record_security_findings, bridge)(context_variables={"permissions": ["security_readiness.manage"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "expired", "foreign_app", "foreign_user", "foreign_actor_and_connection", "foreign_chat"])
async def test_dispatch_rejects_unavailable_or_mismatched_principal(live_runtime, monkeypatch, fault):
    if fault == "missing":
        live_runtime.transport.connections.clear()
    elif fault == "expired":
        live_runtime.principal.raw_claims["exp"] = 1
    elif fault == "foreign_app":
        live_runtime.principal.app_id = "foreign-app"
    elif fault == "foreign_user":
        live_runtime.principal.user_id = "other_user"
    elif fault == "foreign_actor_and_connection":
        live_runtime.principal.user_id = live_runtime.connection["user_id"] = "other_user"
    elif fault == "foreign_chat":
        live_runtime.principal.chat_id = "other_chat"
    attempts = []
    original = module_tools._live_session
    def observe(*args, **kwargs):
        attempts.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(module_tools, "_live_session", observe)
    monkeypatch.setattr(module_tools, "_RECONNECT_TIMEOUT_SECONDS", 0.02)
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is False
    assert result["persistence_error"].startswith("workflow_session_")
    assert not live_runtime.persistence.collection_handle.rows
    if fault != "missing":
        assert len(attempts) == 1


@pytest.mark.asyncio
async def test_detached_child_cannot_reuse_revoked_tool_authority(live_runtime):
    ready = asyncio.Event()

    async def tool(context_variables):
        async def later():
            await ready.wait()
            return await module_tools.dispatch_workflow_module_action("security_readiness", "record_assessment", {})
        return asyncio.create_task(later())

    pending = await _wrap_tool_with_context(tool, _bridge())()
    ready.set()
    with pytest.raises(PermissionError, match="workflow_tool_invocation_unavailable"):
        await pending
    assert not live_runtime.scopes


@pytest.mark.asyncio
async def test_local_dispatch_uses_configured_no_auth_permissions(live_runtime, monkeypatch):
    from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter

    monkeypatch.setattr(module_tools, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(module_tools, "get_auth_adapter", NoAuthAdapter)
    live_runtime.principal.scopes = ["access_as_user"]
    live_runtime.principal.provider = "none"
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is True
    assert live_runtime.scopes[0].authority.permission_mode == "enforce"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["internal", "admin_internal", "missing"])
async def test_workflow_cannot_dispatch_internal_or_undeclared_action(live_runtime, surface):
    surfaces = live_runtime.connection["websocket"].app.state.module_action_surfaces["security_readiness"]
    if surface == "missing":
        surfaces.pop("record_assessment")
    else:
        surfaces["record_assessment"] = surface
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is False
    assert result["persistence_error"] == "workflow_module_action_unavailable"
    assert not live_runtime.scopes


@pytest.mark.asyncio
async def test_recording_without_optional_lineage_matches_event_schema(live_runtime):
    bridge = _bridge()
    bridge.set("build_id", None)
    bridge.set("artifact_version_id", None)
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    recorded = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert recorded["persisted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disconnect", "closed_socket", "connection", "socket", "principal", "expiry", "scopes"])
async def test_principal_is_revalidated_after_awaited_scope_hook(live_runtime, monkeypatch, change):
    async def resolve(**kwargs):
        if change == "disconnect":
            live_runtime.connection["active"] = False
        elif change == "closed_socket":
            live_runtime.connection["websocket"].client_state = WebSocketState.DISCONNECTED
        elif change == "connection":
            live_runtime.transport.connections[RUN[2]] = dict(live_runtime.connection)
        elif change == "socket":
            live_runtime.connection["websocket"] = copy(live_runtime.connection["websocket"])
        elif change == "principal":
            live_runtime.connection["websocket"].state.user = copy(live_runtime.principal)
        elif change == "expiry":
            live_runtime.principal.raw_claims["exp"] = 1
        else:
            live_runtime.principal.scopes.clear()
        return {**kwargs["requested_scope"], "permissions": kwargs["default_permissions"]}

    monkeypatch.setattr(module_tools, "get_platform_hooks", lambda: SimpleNamespace(call_module_scope=resolve))
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is False
    assert result["persistence_error"].startswith("workflow_session_")
    assert not live_runtime.persistence.collection_handle.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_successor_reconnect_reacquires_principal_and_permissions(live_runtime, monkeypatch, authorized):
    from mozaiksai.core.workflow.pack.journey_orchestrator import JourneyOrchestrator

    source = {**live_runtime.connection, "ws_id": 1}
    JourneyOrchestrator()._ensure_connection_alias(
        transport=live_runtime.transport, source_conn=source, target_chat_id=RUN[2],
        workflow_name=RUN[0], app_id=RUN[1], user_id="owner_1",
    )
    principals = []
    async def resolve(**kwargs):
        principals.append(kwargs["principal"])
        if len(principals) == 1:
            renewed = copy(live_runtime.principal)
            renewed.scopes = ["security_readiness.manage"] if authorized else []
            socket = copy(source["websocket"])
            socket.state = SimpleNamespace(user=renewed)
            live_runtime.transport.connections[RUN[2]] = {
                **source, "websocket": socket, "ws_id": 2,
            }
        return {**kwargs["requested_scope"], "permissions": kwargs["default_permissions"]}

    monkeypatch.setattr(module_tools, "get_platform_hooks", lambda: SimpleNamespace(call_module_scope=resolve))
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is authorized
    if not authorized:
        assert result["persistence_error"] == "PERMISSION_DENIED"
    assert len(principals) == 2
    assert principals[0] is not principals[1]
    assert len(live_runtime.scopes) == len(live_runtime.persistence.collection_handle.rows) == int(authorized)


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", ["missing", "disconnected"])
async def test_connection_gap_waits_for_live_same_owner_socket(live_runtime, gap):
    initial = live_runtime.connection
    renewed = copy(initial["websocket"])
    renewed.state = SimpleNamespace(user=copy(live_runtime.principal))
    if gap == "missing":
        live_runtime.transport.connections.clear()
    else:
        initial["websocket"].client_state = WebSocketState.DISCONNECTED
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()

    async def reconnect():
        await asyncio.sleep(0)
        live_runtime.transport.connections[RUN[2]] = {**initial, "websocket": renewed}

    reconnecting = asyncio.create_task(reconnect())
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    await reconnecting
    assert result["persisted"] is True
    assert len(live_runtime.persistence.collection_handle.rows) == 1


@pytest.mark.asyncio
async def test_missing_runtime_actor_never_uses_connection_as_actor_source(live_runtime):
    bridge = _bridge(user_id=None)
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persistence_error"] == "workflow_tool_invocation_unavailable"
    assert not live_runtime.scopes


@pytest.mark.asyncio
async def test_reconnect_wait_cannot_outlive_tool_invocation(live_runtime, monkeypatch):
    original = module_tools._live_session
    attempted = asyncio.Event()
    def observe(*args, **kwargs):
        attempted.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(module_tools, "_live_session", observe)
    live_runtime.transport.connections.clear()

    async def launch(context_variables):
        pending = asyncio.create_task(module_tools.dispatch_workflow_module_action(
            "security_readiness", "record_assessment", {},
        ))
        await attempted.wait()
        return pending

    pending = await _wrap_tool_with_context(launch, _bridge())()
    live_runtime.transport.connections[RUN[2]] = live_runtime.connection
    with pytest.raises(PermissionError, match="workflow_tool_invocation_unavailable"):
        await pending
    assert not live_runtime.scopes


@pytest.mark.asyncio
async def test_dispatch_is_never_retried_after_execution_begins(live_runtime, monkeypatch):
    dispatch = AsyncMock(side_effect=module_tools._ConnectionUnavailable("execution_started"))
    monkeypatch.setattr(module_tools, "dispatch_module_action", dispatch)
    bridge = _bridge()
    await _wrap_tool_with_context(inspect_generated_app_security, bridge)()
    result = await _wrap_tool_with_context(record_security_findings, bridge)()
    assert result["persisted"] is False
    assert result["persistence_error"] == "execution_started"
    dispatch.assert_awaited_once()
