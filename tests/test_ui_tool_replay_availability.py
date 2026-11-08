"""Saved UI never recreates the server authority needed to answer a review."""
from __future__ import annotations

from copy import deepcopy
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.transport.run_replay import WorkflowRunReplayer
from mozaiksai.core.transport.ui_tools import project_ui_tool_response_state
from tests.test_ui_response_ownership import _http, _pending
from tests.test_ui_response_ownership import harness as harness


def _saved():
    return {
        "tool_call_id": "evt-owned", "tool_name": "ApprovalCard", "display": "artifact",
        "awaiting_response": True, "tool_call_completed": False, "tool_call_status": "pending",
        "payload": {"review_id": "exact-review", "interaction_type": "ui_tool"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["future", "input", "cold", "early", "done", "cancelled", "resolved", "foreign", "ambiguous"])
async def test_projection_describes_only_current_scoped_response_registration(harness, kind):
    transport = harness.transport
    if kind != "cold":
        _pending(harness, "input" if kind == "input" else "early" if kind == "early" else "future")
    if kind == "done":
        transport.pending_tool_call_responses["evt-owned"].set_result({"action": "approve"})
    elif kind == "cancelled":
        transport.pending_tool_call_responses["evt-owned"].cancel()
    elif kind == "resolved":
        transport._mark_tool_call_response_resolved("evt-owned")
    elif kind == "foreign":
        transport._ui_tool_metadata["evt-owned"]["chat_id"] = "another-chat"
    elif kind == "ambiguous":
        transport._input_request_registries["another-chat"] = {"evt-owned": object()}
    state = _saved()
    before = deepcopy(state)
    metadata = deepcopy(transport._ui_tool_metadata)
    result = project_ui_tool_response_state(state, chat_id="chat-owner", transport=transport)
    assert result == {**state, "response_available": kind in {"future", "input"}}
    assert state == before and transport._ui_tool_metadata == metadata
    assert transport._buffered_tool_call_responses == {}
    transport._finalize_tool_call_response.assert_not_awaited()
    transport.submit_user_input.assert_not_awaited()


def test_projection_without_runtime_is_unavailable_and_passive_surfaces_stay_passive():
    state = _saved()
    assert project_ui_tool_response_state(state, chat_id="chat-owner")["response_available"] is False
    state["payload"]["interaction_type"] = "ui_surface"
    assert project_ui_tool_response_state(state, chat_id="chat-owner") == state
    state["payload"]["interaction_type"] = "ui_tool"
    state["awaiting_response"] = False
    assert project_ui_tool_response_state(state, chat_id="chat-owner") == state
    assert project_ui_tool_response_state(None, chat_id="chat-owner") is None


@pytest.mark.asyncio
async def test_saved_review_projection_cannot_authorize_cold_or_foreign_submission(harness):
    state = _saved()
    project_ui_tool_response_state(state, chat_id="chat-owner", transport=harness.transport)
    assert (await _http(harness)).status_code == 404
    harness.transport._finalize_tool_call_response.assert_not_awaited()
    assert harness.transport._ui_tool_metadata == {}
    assert harness.transport.pending_tool_call_responses == {}
    _pending(harness)
    assert project_ui_tool_response_state(state, chat_id="chat-owner", transport=harness.transport)["response_available"] is True
    harness.principal.user_id = "foreign-owner"
    assert (await _http(harness)).status_code == 404
    assert not harness.transport.pending_tool_call_responses["evt-owned"].done()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_actual_replay_and_boundary_project_availability_without_rearming(harness, monkeypatch, live):
    if live:
        _pending(harness)
    state = {**_saved(), "message_index": 0}
    original = deepcopy(state)
    pm = SimpleNamespace(
        load_run_history=AsyncMock(return_value=[{"role": "assistant", "name": "Reviewer", "content": "Review this plan"}]),
        get_workflow_tool_call_states=AsyncMock(return_value=[state]),
        get_pending_input_request=AsyncMock(return_value=None),
    )
    replayer = WorkflowRunReplayer(project_ui_tool_response_state=partial(
        project_ui_tool_response_state, transport=harness.transport,
    ))
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: pm)
    monkeypatch.setattr(replayer, "_fetch_chat_doc", AsyncMock(return_value={"status": 0, "user_id": "owner"}))
    monkeypatch.setattr(replayer, "_load_resume_state", AsyncMock(return_value=None))
    emitted = AsyncMock()
    await replayer.handle_resume_request(chat_id="chat-owner", app_id="app-owner", last_client_index=-1, send_event=emitted)
    events = [call.args[0] for call in emitted.await_args_list]
    text = next(event for event in events if event["kind"] == "text")
    assert text["toolCall"]["response_available"] is live
    assert text["metadata"]["tool_call"]["response_available"] is live
    assert text["tool_call_completed"] is False
    assert text["toolCall"]["payload"]["review_id"] == "exact-review"
    assert state == original
    assert len(harness.transport.pending_tool_call_responses) == int(live)
    harness.transport._finalize_tool_call_response.assert_not_awaited()
    if live:
        assert (await _http(harness)).status_code == 200
    else:
        assert (await _http(harness)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("feedback", [False, True])
async def test_pending_composer_replay_preserves_identity_without_rearming(harness, monkeypatch, live, feedback):
    if live:
        _pending(harness, "input")
    raw_payload = {"review_id": "original-review", "choices": ["first", "second"]}
    if feedback:
        raw_payload["resume_ui_kind"] = "awaiting_reply"
    pending = {
        "request_id": "evt-owned", "agent": "Reviewer", "prompt": "Original prompt",
        "component_type": "AgentInput", "workflow_name": "ExampleWorkflow",
        "tool_name": "AgentInput", "display": "composer", "interaction_type": "input_request",
        "raw_payload": raw_payload,
    }
    original = deepcopy(pending)
    pm = SimpleNamespace(
        load_run_history=AsyncMock(return_value=[{"role": "assistant", "name": "Reviewer", "content": "Original prompt"}]),
        get_workflow_tool_call_states=AsyncMock(return_value=[]),
        get_pending_input_request=AsyncMock(return_value=pending),
    )
    replayer = WorkflowRunReplayer(project_ui_tool_response_state=partial(
        project_ui_tool_response_state, transport=harness.transport,
    ))
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: pm)
    monkeypatch.setattr(replayer, "_fetch_chat_doc", AsyncMock(return_value={"status": 0, "user_id": "owner"}))
    monkeypatch.setattr(replayer, "_load_resume_state", AsyncMock(return_value=None))
    emitted = AsyncMock()
    await replayer.handle_resume_request(chat_id="chat-owner", app_id="app-owner", last_client_index=-1, send_event=emitted)
    events = [call.args[0] for call in emitted.await_args_list]
    event = next(item for item in events if item["kind"] == "tool_call")
    assert event["response_available"] is live
    assert event["tool_call_id"] == event["payload"]["request_id"] == "evt-owned"
    assert event["display"] == "composer" and event["payload"]["prompt"] == "Original prompt"
    assert all(event["payload"][key] == value for key, value in raw_payload.items())
    assert len([item for item in events if item["kind"] == "awaiting_reply"]) == int(feedback)
    assert pending == original
    assert bool(harness.transport._input_request_registries.get("chat-owner")) is live
    harness.transport.submit_user_input.assert_not_awaited()
    harness.transport._finalize_tool_call_response.assert_not_awaited()
    if not live:
        assert (await _http(harness)).status_code == 404
