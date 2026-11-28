"""WebSocket frontend context cannot become saved snapshot authority at entry."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import build_context_authority_policy


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["selected_input", "ui_selected_input"])
async def test_orchestration_rejects_frontend_snapshot_before_projection(monkeypatch, key):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import orchestration_patterns as orchestration
    from mozaiksai.core.workflow import task_batches
    from mozaiksai.core.workflow.outputs import structured

    definitions = {
        "ui_selected_input": {
            "type": "object", "authority_class": "closed_writer_routing_state",
            "persisted": True, "writer_ids": ["transition_router"],
            "source": {"type": "state", "default": None},
        },
        "design": {
            "type": "object", "source": {
                "type": "data_reference", "collection": "Designs",
                "snapshot": {"context_key": "ui_selected_input", "path": "context.design"},
            },
        },
    }
    context = create_context_container(
        {"ui_selected_input": None},
        authority_policy=build_context_authority_policy(
            workflow_name="SnapshotBoundary", definitions=definitions,
        ),
    )
    write_context = Mock(wraps=context.set)
    monkeypatch.setattr(context, "set", write_context)
    context_factory = Mock(return_value=context)
    persistence = SimpleNamespace(
        assert_chat_resumable=AsyncMock(return_value=None),
        get_or_assign_cache_seed=AsyncMock(return_value=1),
        mark_chat_failed=AsyncMock(),
        mark_chat_completed=AsyncMock(),
        persist_context_variables=AsyncMock(),
    )
    transport = SimpleNamespace(
        connections={"chat-one": {"frontend_context": {
            key: {"context": {"design": {"role": "forged"}}},
        }}},
        send_event_to_ui=AsyncMock(),
    )
    agents_factory = AsyncMock(return_value={})
    network_phase = AsyncMock()
    persist_context = AsyncMock()
    monkeypatch.setattr(orchestration, "AG2PersistenceManager", lambda: persistence)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(orchestration, "_load_workflow_config", lambda _: {
        "config": {"context_variables": {"definitions": definitions}},
        "max_turns": 2, "workflow_startup_mode": "AgentDriven",
        "initial_agent_name": "PlannerAgent",
    })
    monkeypatch.setattr(orchestration, "prepare_network_trigger", AsyncMock(return_value="Start"))
    monkeypatch.setattr(orchestration, "_fetch_chat_session_extra_context", AsyncMock(return_value={}))
    monkeypatch.setattr(orchestration, "get_platform_hooks", lambda: SimpleNamespace(
        call_chat_session_fields=AsyncMock(return_value={}),
    ))
    monkeypatch.setattr(task_batches, "load_task_batches_config", lambda _: None)
    monkeypatch.setattr(structured, "load_workflow_structured_outputs", lambda _: ({}, {}))
    monkeypatch.setattr(orchestration, "_run_ag2_network_phase", network_phase)
    monkeypatch.setattr(orchestration, "_persist_context_variables", persist_context)

    with pytest.raises(ValueError, match="snapshots cannot be supplied by frontend context"):
        await orchestration.run_workflow_orchestration(
            workflow_name="SnapshotBoundary", app_id="factory-host", chat_id="chat-one",
            user_id="user-one", agents_factory=agents_factory, context_factory=context_factory,
        )

    context_factory.assert_called_once_with()
    write_context.assert_not_called()
    assert context.get("ui_selected_input") is None
    agents_factory.assert_not_awaited()
    network_phase.assert_not_awaited()
    persist_context.assert_not_awaited()
    persistence.persist_context_variables.assert_not_awaited()
    persistence.mark_chat_completed.assert_not_awaited()
    persistence.mark_chat_failed.assert_awaited_once_with("chat-one", app_id="factory-host")
