from __future__ import annotations

import asyncio
import logging
import queue
import threading

import pytest

from mozaiksai.core.transport.ui_tools import UIToolsMixin
from mozaiksai.core.workflow.workflow_manager import workflow_manager


class _FakeTransport(UIToolsMixin):
    _instance = None

    def __init__(self) -> None:
        self.pending_tool_call_responses = {}
        self._buffered_tool_call_responses = {}
        self._ui_tool_metadata = {}
        self._resolved_tool_call_ids = {}
        self._derived_context_managers = {}
        self._input_request_registries = {}
        self.connections = {}
        self.sent_events = []

    @classmethod
    async def get_instance(cls):
        return cls._instance

    async def send_event_to_ui(self, event, chat_id=None):
        self.sent_events.append((event, chat_id))

    async def _persist_ui_tool_state(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_submit_tool_call_response_completes_future_on_owner_loop() -> None:
    transport = _FakeTransport()
    result_queue: queue.Queue[dict] = queue.Queue()
    ready = threading.Event()

    def _wait_on_foreign_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _runner() -> None:
            future = loop.create_future()
            transport.pending_tool_call_responses["evt-1"] = future
            ready.set()
            result = await future
            result_queue.put(result)

        loop.run_until_complete(_runner())
        loop.close()

    worker = threading.Thread(target=_wait_on_foreign_loop, daemon=True)
    worker.start()

    assert ready.wait(timeout=2.0) is True

    accepted = await transport.submit_tool_call_response("evt-1", {"status": "submitted", "text": "approved"})

    worker.join(timeout=2.0)

    assert accepted is True
    assert not worker.is_alive()
    assert result_queue.get_nowait() == {"status": "submitted", "text": "approved"}


@pytest.mark.asyncio
async def test_submit_tool_call_response_buffers_early_response_until_waiter_registers() -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["evt-early"] = {
        "chat_id": "chat-1",
        "tool_name": "UserInputRequest",
        "display": "inline",
    }

    accepted = await transport.submit_tool_call_response(
        "evt-early",
        {"status": "submitted", "text": "approved"},
    )
    result = await _FakeTransport.wait_for_tool_call_response("evt-early", timeout=0.1)

    assert accepted is True
    assert result == {"status": "submitted", "text": "approved"}
    assert "evt-early" not in transport._buffered_tool_call_responses


@pytest.mark.asyncio
async def test_submit_tool_call_response_ignores_duplicate_resolved_response(caplog: pytest.LogCaptureFixture) -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["evt-dup"] = {
        "chat_id": "chat-1",
        "tool_name": "ApprovalCard",
        "display": "inline",
    }

    with caplog.at_level(logging.WARNING, logger="simple_transport.ui_tools"):
        accepted_first = await transport.submit_tool_call_response(
            "evt-dup",
            {"status": "submitted", "action": "approve", "approved": True},
        )
        accepted_second = await transport.submit_tool_call_response(
            "evt-dup",
            {"status": "submitted", "action": "approve", "approved": True},
        )

    assert accepted_first is True
    assert accepted_second is True
    assert "evt-dup" in transport._resolved_tool_call_ids
    assert "[UI_TOOL] No pending event found for evt-dup" not in caplog.text


@pytest.mark.asyncio
async def test_send_tool_call_event_emits_canonical_tool_call_envelope(monkeypatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(workflow_manager, "_ui_registry", {
        "PlannerFlow.build_plan_card": {
            "workflow_name": "PlannerFlow", "tool_id": "build_plan_card",
            "ui_contract": {"actions_schema": [{"id": "approve"}]},
        },
    })

    await transport.send_tool_call_event(
        event_id="evt-ui-1",
        chat_id="chat-1",
        tool_name="build_plan_card",
        component_name="ApprovalCard",
        display_type="inline",
        payload={"workflow_name": "PlannerFlow", "title": "Review the plan"},
        awaiting_response=True,
        agent_name="PlannerAgent",
    )

    assert len(transport.sent_events) == 1
    event, chat_id = transport.sent_events[0]
    assert chat_id == "chat-1"
    assert event["kind"] == "tool_call"
    assert event["tool_call_id"] == "evt-ui-1"
    assert event["corr"] == "evt-ui-1"
    assert event["tool_name"] == "build_plan_card"
    assert event["component_type"] == "ApprovalCard"
    assert event["workflow_name"] == "PlannerFlow"
    assert event["display"] == "inline"
    assert event["display_type"] == "inline"
    assert event["interaction_type"] == "ui_tool"
    assert event["awaiting_response"] is True
    assert event["agent"] == "PlannerAgent"
    assert event["agent_name"] == "PlannerAgent"


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_name,tool_record", [
    ("OtherFlow", None),
    ("PlannerFlow", None),
    ("PlannerFlow", {"ui_contract": {}}),
])
async def test_interactive_event_requires_an_exact_registered_response_contract(
    monkeypatch, workflow_name, tool_record,
) -> None:
    transport = _FakeTransport()
    registry = {}
    if tool_record is not None:
        registry["PlannerFlow.ApprovalCard"] = {
            "workflow_name": "PlannerFlow", "tool_id": "ApprovalCard", **tool_record,
        }
    monkeypatch.setattr(workflow_manager, "_ui_registry", registry)

    with pytest.raises(ValueError, match="registered|actions_schema"):
        await transport.send_tool_call_event(
            event_id="evt-unregistered", chat_id="chat-1", tool_name="ApprovalCard",
            component_name="ApprovalCard", display_type="artifact",
            payload={"workflow_name": workflow_name, "actions": [{"id": "delete"}]},
        )
    assert transport.sent_events == []
    assert transport._ui_tool_metadata == {}


@pytest.mark.asyncio
async def test_registered_actionless_contract_can_receive_free_form_input(monkeypatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(workflow_manager, "_ui_registry", {
        "PlannerFlow.UserInputRequest": {
            "workflow_name": "PlannerFlow", "tool_id": "UserInputRequest",
            "ui_contract": {"actions_schema": []},
        },
    })
    await transport.send_tool_call_event(
        event_id="evt-input", chat_id="chat-1", tool_name="UserInputRequest",
        component_name="UserInputRequest", display_type="inline",
        payload={"workflow_name": "PlannerFlow", "interaction_type": "input_request"},
    )
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-input"] = future
    assert await transport.submit_tool_call_response("evt-input", {"text": "Plan my app"}) is True
    assert future.result() == {"text": "Plan my app"}


@pytest.mark.asyncio
async def test_tool_call_binds_actions_from_the_exact_workflow_not_the_render_payload(monkeypatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(workflow_manager, "_ui_registry", {
        "OtherFlow.ApprovalCard": {
            "workflow_name": "OtherFlow", "tool_id": "ApprovalCard", "fn": "review",
            "ui_contract": {"actions_schema": [{"id": "reject"}]},
        },
        "PlannerFlow.ApprovalCard": {
            "workflow_name": "PlannerFlow", "tool_id": "ApprovalCard", "fn": "review",
            "ui_contract": {"actions_schema": [{
                "id": "approve", "approved": True,
                "payload_schema": {
                    "type": "object", "required": ["action", "approved", "review_id"],
                    "properties": {
                        "action": {"const": "approve"},
                        "approved": {"const": True},
                        "review_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            }]},
        },
    })

    await transport.send_tool_call_event(
        event_id="evt-bound", chat_id="chat-1", tool_name="ApprovalCard",
        component_name="ApprovalCard", display_type="artifact",
        payload={
            "workflow_name": "PlannerFlow", "actions": [
                {"id": "approve", "approved": False}, {"id": "reject", "approved": False},
            ],
            "ui_contract": {"actions_schema": [{"id": "reject"}]},
        },
    )

    assert [action["id"] for action in transport._ui_tool_metadata["evt-bound"]["response_actions"]] == ["approve"]
    assert transport.sent_events[0][0]["payload"]["actions"][0]["approved"] is False


@pytest.mark.asyncio
async def test_event_limits_reply_to_the_declared_actions_offered_for_this_review(monkeypatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(workflow_manager, "_ui_registry", {
        "PlannerFlow.ApprovalCard": {
            "workflow_name": "PlannerFlow", "tool_id": "ApprovalCard", "fn": "review",
            "ui_contract": {"actions_schema": [
                {"id": "approve", "approved": True},
                {"id": "request_changes", "approved": False},
            ]},
        },
    })
    await transport.send_tool_call_event(
        event_id="evt-limited", chat_id="chat-1", tool_name="ApprovalCard",
        component_name="ApprovalCard", display_type="artifact",
        payload={"workflow_name": "PlannerFlow", "actions": [{"id": "request_changes"}]},
    )
    assert [action["id"] for action in transport._ui_tool_metadata["evt-limited"]["response_actions"]] == ["request_changes"]
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-limited"] = future
    assert await transport.submit_tool_call_response(
        "evt-limited", {"action": "approve", "approved": True},
    ) is False
    assert not future.done()
    assert await transport.submit_tool_call_response(
        "evt-limited", {"action": "request_changes", "approved": False},
    ) is True
    assert future.result()["action"] == "request_changes"


@pytest.mark.asyncio
async def test_event_refuses_an_action_projection_with_no_declared_action(monkeypatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(workflow_manager, "_ui_registry", {
        "PlannerFlow.ApprovalCard": {
            "workflow_name": "PlannerFlow", "tool_id": "ApprovalCard", "fn": "review",
            "ui_contract": {"actions_schema": [{"id": "approve", "approved": True}]},
        },
    })
    with pytest.raises(ValueError, match="declared action"):
        await transport.send_tool_call_event(
            event_id="evt-unknown", chat_id="chat-1", tool_name="ApprovalCard",
            component_name="ApprovalCard", display_type="artifact",
            payload={"workflow_name": "PlannerFlow", "actions": [{"id": "reject"}]},
        )
    assert "evt-unknown" not in transport._ui_tool_metadata
    assert transport.sent_events == []


def _review_response_actions() -> list[dict]:
    return [{
        "id": "approve", "approved": True,
        "payload_schema": {
            "type": "object", "required": ["action", "approved", "review_id"],
            "properties": {
                "action": {"const": "approve"},
                "approved": {"const": True},
                "review_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"action": "reject", "approved": False, "review_id": "review-1"},
    {"action": "approve", "approved": False, "review_id": "review-1"},
    {"action": "approve", "approved": True, "review_id": "review-1", "extra": "override"},
    {"action": "approve", "approved": True},
])
async def test_invalid_declared_action_cannot_complete_or_buffer_response(response: dict) -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["evt-decision"] = {
        "chat_id": "chat-1", "tool_name": "ApprovalCard", "display": "artifact",
        "interaction_type": "ui_tool", "response_actions": _review_response_actions(),
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-decision"] = future

    assert await transport.submit_tool_call_response("evt-decision", response) is False
    assert not future.done()
    assert transport._buffered_tool_call_responses == {}
    assert "evt-decision" not in transport._resolved_tool_call_ids


@pytest.mark.asyncio
async def test_valid_declared_action_waits_for_finalization_before_waking_tool() -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["evt-decision"] = {
        "chat_id": "chat-1", "tool_name": "ApprovalCard", "display": "inline",
        "interaction_type": "ui_tool", "response_actions": _review_response_actions(),
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-decision"] = future
    finalizing = asyncio.Event()
    release = asyncio.Event()

    async def finalize(**kwargs):
        finalizing.set()
        await release.wait()

    transport._finalize_tool_call_response = finalize
    response = {"action": "approve", "approved": True, "review_id": "review-1"}
    submission = asyncio.create_task(transport.submit_tool_call_response("evt-decision", response))
    try:
        await asyncio.wait_for(finalizing.wait(), timeout=2)
        assert not future.done()
    finally:
        release.set()
    assert await asyncio.wait_for(submission, timeout=2) is True
    assert await future == response


@pytest.mark.asyncio
async def test_conflicting_retry_is_rejected_while_the_first_decision_is_finalizing() -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["evt-decision"] = {
        "chat_id": "chat-1", "tool_name": "ApprovalCard", "display": "inline",
        "interaction_type": "ui_tool", "response_actions": [
            {"id": "approve", "approved": True},
            {"id": "delete", "approved": False},
        ],
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-decision"] = future
    finalizing = asyncio.Event()
    release = asyncio.Event()

    async def finalize(**kwargs):
        finalizing.set()
        await release.wait()

    transport._finalize_tool_call_response = finalize
    approve = {"action": "approve", "approved": True}
    first = asyncio.create_task(transport.submit_tool_call_response("evt-decision", approve))
    await asyncio.wait_for(finalizing.wait(), timeout=2)
    retry = asyncio.create_task(transport.submit_tool_call_response("evt-decision", approve))
    assert await transport.submit_tool_call_response(
        "evt-decision", {"action": "delete", "approved": False},
    ) is False
    assert not retry.done()
    assert not future.done()
    release.set()
    assert await asyncio.wait_for(first, timeout=2) is True
    assert await asyncio.wait_for(retry, timeout=2) is True
    assert future.result() == approve
    assert await transport.submit_tool_call_response("evt-decision", approve) is True
    assert await transport.submit_tool_call_response(
        "evt-decision", {"action": "delete", "approved": False},
    ) is False


@pytest.mark.asyncio
async def test_cancelled_submission_still_finishes_delivery_before_waking_tool() -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["evt-cancelled-submit"] = {
        "chat_id": "chat-1", "tool_name": "ApprovalCard", "display": "inline",
        "interaction_type": "ui_tool", "response_actions": _review_response_actions(),
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-cancelled-submit"] = future
    finalizing = asyncio.Event()
    release = asyncio.Event()

    async def finalize(**kwargs):
        finalizing.set()
        await release.wait()

    transport._finalize_tool_call_response = finalize
    response = {"action": "approve", "approved": True, "review_id": "review-1"}
    submission = asyncio.create_task(transport.submit_tool_call_response("evt-cancelled-submit", response))
    await asyncio.wait_for(finalizing.wait(), timeout=2)
    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission
    assert not future.done()
    release.set()
    assert await asyncio.wait_for(future, timeout=2) == response


@pytest.mark.asyncio
async def test_early_declared_response_waits_for_finalization_before_buffering() -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["evt-early-decision"] = {
        "chat_id": "chat-1", "tool_name": "ApprovalCard", "display": "inline",
        "interaction_type": "ui_tool", "response_actions": _review_response_actions(),
    }
    finalizing = asyncio.Event()
    release = asyncio.Event()

    async def finalize(**kwargs):
        finalizing.set()
        await release.wait()

    transport._finalize_tool_call_response = finalize
    response = {"action": "approve", "approved": True, "review_id": "review-1"}
    submission = asyncio.create_task(transport.submit_tool_call_response("evt-early-decision", response))
    try:
        await asyncio.wait_for(finalizing.wait(), timeout=2)
        waiter = asyncio.create_task(_FakeTransport.wait_for_tool_call_response("evt-early-decision", timeout=2))
        await asyncio.sleep(0)
        assert not waiter.done()
        assert transport._buffered_tool_call_responses == {}
    finally:
        release.set()
    assert await asyncio.wait_for(submission, timeout=2) is True
    assert await asyncio.wait_for(waiter, timeout=2) == response


@pytest.mark.asyncio
async def test_actionless_ui_tool_response_is_preserved() -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["evt-actionless"] = {
        "chat_id": "chat-1", "tool_name": "CustomForm", "display": "inline",
        "interaction_type": "ui_tool", "response_actions": [],
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-actionless"] = future

    assert await transport.submit_tool_call_response("evt-actionless", {"value": "hello"}) is True
    assert future.result() == {"value": "hello"}


@pytest.mark.asyncio
async def test_render_payload_cannot_disable_a_declared_action_contract() -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["evt-false-input"] = {
        "chat_id": "chat-1", "tool_name": "ApprovalCard", "display": "inline",
        "interaction_type": "input_request", "response_actions": _review_response_actions(),
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-false-input"] = future
    assert await transport.submit_tool_call_response("evt-false-input", {"text": "approve"}) is False
    assert not future.done()


@pytest.mark.asyncio
async def test_actionless_input_request_ui_tool_accepts_text_response() -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["evt-input-tool"] = {
        "chat_id": "chat-1", "tool_name": "UserInputRequest", "display": "inline",
        "interaction_type": "input_request", "response_actions": [],
    }
    future = asyncio.get_running_loop().create_future()
    transport.pending_tool_call_responses["evt-input-tool"] = future
    response = {"status": "submitted", "text": "Build the app"}
    assert await transport.submit_tool_call_response("evt-input-tool", response) is True
    assert future.result() == response

