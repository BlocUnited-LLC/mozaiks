from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
    require_unchanged_runtime_authority,
)
from mozaiksai.core.workflow.context.frozen import detach


@pytest.mark.parametrize(
    "key",
    ["app_id", "user_id", "chat_id", "workflow_name", "workspace_id",
     "build_registry_id", "target_app_id", "permissions", "api_key_handle"],
)
@pytest.mark.parametrize("mutation", ["replace", "add", "delete"])
def test_immutable_authority_changes_are_rejected(key, mutation):
    before = {} if mutation == "add" else {key: "original-private-value"}
    after = {} if mutation == "delete" else {key: "changed-private-value"}
    with pytest.raises(ContextAuthorityError, match=f"key={key}") as exc:
        require_unchanged_runtime_authority(before, after)
    assert "private-value" not in str(exc.value)


def test_custom_immutable_declaration_is_checked():
    policy = build_context_authority_policy(
        workflow_name="GuardSmoke",
        definitions={"execution_scope": {
            "type": "object", "source": {"type": "state", "default": {}},
            "authority_class": "immutable_runtime_authority",
        }},
    )
    with pytest.raises(ContextAuthorityError, match="key=execution_scope"):
        require_unchanged_runtime_authority(
            {"execution_scope": {"owner": "one"}},
            {"execution_scope": {"owner": "two"}}, policy=policy,
        )


def test_mutable_preload_and_identical_authority_are_allowed():
    require_unchanged_runtime_authority(
        {"app_id": "host", "permissions": ["read"], "summary": "old"},
        {"app_id": "host", "permissions": ["read"], "summary": "new", "ready": True},
    )


def test_missing_authority_is_not_equivalent_to_null():
    with pytest.raises(ContextAuthorityError, match="key=app_id"):
        require_unchanged_runtime_authority({"app_id": None}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("container_kind", ["runtime", "dict"])
@pytest.mark.parametrize("mutation", ["replace", "delete", "nested", "caught_error"])
async def test_startup_identity_change_stops_before_agent_creation(
    monkeypatch, container_kind, mutation,
):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import orchestration_patterns as orchestration
    from mozaiksai.core.workflow import task_batches
    from mozaiksai.core.workflow.execution import lifecycle
    from mozaiksai.core.workflow.outputs import structured

    initial = {"app_id": "factory-host", "permissions": ["read"]}
    policy = build_context_authority_policy(workflow_name="GuardSmoke", definitions={
        "permissions": {"type": "array", "source": {"type": "state", "default": []}},
    })
    context = (create_context_container(initial, authority_policy=policy)
               if container_kind == "runtime" else detach(initial))
    persistence = SimpleNamespace(
        load_run_events=AsyncMock(return_value=[]),
        create_chat_session=AsyncMock(),
        get_or_assign_cache_seed=AsyncMock(return_value=1),
        fetch_chat_session_extra_context=AsyncMock(return_value={}),
        mark_chat_completed=AsyncMock(),
    )
    transport = SimpleNamespace(connections={}, send_event_to_ui=AsyncMock())
    agents_factory = AsyncMock(return_value={})
    network_phase = AsyncMock()
    manager = lifecycle.LifecycleToolManager("GuardSmoke")
    manager._emit_lifecycle_event = AsyncMock()

    def mutate(context_variables):
        if mutation == "delete":
            if container_kind == "runtime":
                context_variables.remove("app_id")
            else:
                del context_variables["app_id"]
            return
        key, value = ("permissions", ["read", "admin"]) if mutation == "nested" else ("app_id", "target-app")
        if container_kind == "runtime":
            context_variables.set(key, value)
        elif mutation == "nested":
            context_variables[key].append("admin")
        else:
            context_variables[key] = value
        if mutation == "caught_error":
            raise RuntimeError("hook error swallowed by LifecycleToolManager")

    hook = mutate

    manager.tools[lifecycle.LifecycleTrigger.BEFORE_CHAT] = [lifecycle.LifecycleTool(
        trigger=lifecycle.LifecycleTrigger.BEFORE_CHAT, agent=None,
        file="mutate.py", function="mutate", description=None,
        callable=hook, accepts_context=True,
    )]
    monkeypatch.setattr(orchestration, "AG2PersistenceManager", lambda: persistence)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(orchestration, "_run_ag2_network_phase", network_phase)
    monkeypatch.setattr(orchestration, "_load_workflow_config", lambda name: {
        "config": {"workflow_startup_mode": "AgentDriven"},
        "max_turns": 2, "workflow_startup_mode": "AgentDriven",
        "initial_agent_name": "PlannerAgent",
    })
    monkeypatch.setattr(task_batches, "load_task_batches_config", lambda name: None)
    monkeypatch.setattr(structured, "load_workflow_structured_outputs", lambda name: ({}, {}))
    monkeypatch.setattr(lifecycle, "get_lifecycle_manager", lambda name: manager)

    with pytest.raises(ContextAuthorityError, match="lifecycle_changed_runtime_authority"):
        await orchestration.run_workflow_orchestration(
            workflow_name="GuardSmoke", app_id="factory-host", chat_id="chat-one",
            user_id="user-one", initial_message="Build an app",
            agents_factory=agents_factory, context_factory=lambda: context,
        )

    agents_factory.assert_not_awaited()
    network_phase.assert_not_awaited()
    persistence.mark_chat_completed.assert_not_awaited()
    assert persistence.create_chat_session.await_args.kwargs["app_id"] == "factory-host"
    events = [call.args[0] for call in transport.send_event_to_ui.await_args_list]
    assert [event["kind"] for event in events] == ["error", "run_complete"]
    assert events[-1]["status"] == "failed"
    assert events[-1]["run_completed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("container_kind", ["runtime"])
async def test_factory_registration_hook_passes_identity_guard(monkeypatch, container_kind):
    """The real ValueEngine registration hook must not trip the identity guard.

    It reserves the generated app's identity in the declared generated_app_id
    context variable and leaves the executing app_id untouched, so
    orchestration proceeds past require_unchanged_runtime_authority into agent
    creation (which fails here only because this smoke defines no agents).
    Declaration-governed passage requires a resolved authority policy; plain
    dict contexts (no policy) keep the fail-closed name heuristic.
    """
    from factory_app.workflows.ValueEngine.tools import create_app_record
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import orchestration_patterns as orchestration
    from mozaiksai.core.workflow import task_batches
    from mozaiksai.core.workflow.execution import lifecycle
    from mozaiksai.core.workflow.outputs import structured

    initial = {"app_id": "factory-host", "chat_id": "chat-one", "user_id": "user-one"}
    policy = build_context_authority_policy(workflow_name="GuardSmoke", definitions={
        "generated_app_id": {
            "type": "string",
            "authority_class": "mutable_workflow_state",
            "authorization": False,
            "source": {"type": "state", "default": None},
        },
        "build_registry_id": {
            "type": "string",
            "authority_class": "mutable_workflow_state",
            "authorization": False,
            "source": {"type": "state", "default": None},
        },
        "chat_app_id": {
            "type": "string",
            "authority_class": "mutable_workflow_state",
            "authorization": False,
            "source": {"type": "state", "default": None},
        },
    })
    context = (create_context_container(initial, authority_policy=policy)
               if container_kind == "runtime" else detach(initial))
    persistence = SimpleNamespace(
        load_run_events=AsyncMock(return_value=[]),
        create_chat_session=AsyncMock(),
        get_or_assign_cache_seed=AsyncMock(return_value=1),
        fetch_chat_session_extra_context=AsyncMock(return_value={}),
        mark_chat_completed=AsyncMock(),
    )
    transport = SimpleNamespace(connections={}, send_event_to_ui=AsyncMock())
    agents_factory = AsyncMock(return_value={})
    network_phase = AsyncMock()
    manager = lifecycle.LifecycleToolManager("GuardSmoke")
    manager._emit_lifecycle_event = AsyncMock()

    monkeypatch.setattr(create_app_record, "_create_studio_app", AsyncMock(return_value={
        "success": True,
        "app": {"build_registry_id": "build-one", "app_id": "draft-build-target"},
    }))

    manager.tools[lifecycle.LifecycleTrigger.BEFORE_CHAT] = [lifecycle.LifecycleTool(
        trigger=lifecycle.LifecycleTrigger.BEFORE_CHAT, agent=None,
        file="create_app_record.py", function="create_app_record", description=None,
        callable=create_app_record.create_app_record, accepts_context=True,
    )]
    monkeypatch.setattr(orchestration, "AG2PersistenceManager", lambda: persistence)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(orchestration, "_run_ag2_network_phase", network_phase)
    monkeypatch.setattr(orchestration, "_load_workflow_config", lambda name: {
        "config": {"workflow_startup_mode": "AgentDriven"},
        "max_turns": 2, "workflow_startup_mode": "AgentDriven",
        "initial_agent_name": "PlannerAgent",
    })
    monkeypatch.setattr(task_batches, "load_task_batches_config", lambda name: None)
    monkeypatch.setattr(structured, "load_workflow_structured_outputs", lambda name: ({}, {}))
    monkeypatch.setattr(lifecycle, "get_lifecycle_manager", lambda name: manager)

    with pytest.raises(RuntimeError, match="No agents defined"):
        await orchestration.run_workflow_orchestration(
            workflow_name="GuardSmoke", app_id="factory-host", chat_id="chat-one",
            user_id="user-one", initial_message="Build an app",
            agents_factory=agents_factory, context_factory=lambda: context,
        )

    # The run made it past the identity guard into agent creation.
    agents_factory.assert_awaited()
    snapshot = context.to_dict() if hasattr(context, "to_dict") else dict(context)
    assert snapshot["app_id"] == "factory-host"
    assert snapshot["generated_app_id"] == "draft-build-target"
    assert snapshot["build_registry_id"] == "build-one"
