"""Invocation provenance must never be replaceable by caller writer metadata."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mozaiksai.core.adapters import ag2_network_runner as runner
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    CONTEXT_BRIDGE_WRITER,
    DETERMINISTIC_TOOL_WRITER,
    LIVE_USER_CONTEXT_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    ContextAuthorityError,
    ScopedContextWriter,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.schema import load_context_variables_config

RUN = ("ToolAuthorityFlow", "app-1", "chat-1")
READY = "review_scope_confirmed"


def _policy():
    plan = load_context_variables_config({"definitions": {
        READY: {
            "type": "boolean", "source": {"type": "state", "default": False},
            "authority_class": "closed_writer_routing_state", "routing": True,
            "writer_ids": ["deterministic_tool"],
        },
        "ui_approved": {
            "type": "boolean", "source": {"type": "state", "default": False},
            "authority_class": "closed_writer_routing_state", "routing": True,
            "writer_ids": ["ui_response_trigger"],
        },
        "app_id": {"type": "string", "source": {"type": "state", "default": "app-1"}},
        "note": {"type": "string", "source": {"type": "state", "default": ""}},
    }})
    return build_context_authority_policy(workflow_name=RUN[0], definitions=plan.definitions)


def _bridge(*, bind=True):
    policy = _policy()
    bridge = ContextVariablesBridge(
        {READY: False, "ui_approved": False, "app_id": RUN[1], "note": ""},
        authority_policy=policy,
    )
    if bind:
        bridge._bind_run(RUN, policy)
    return bridge, policy


def _set_ready(context_variables):
    context_variables.set(READY, True)


@pytest.mark.parametrize("scoped", [False, True])
def test_bound_tool_preserves_writer_until_authorized_projection(scoped):
    bridge, policy = _bridge()

    def tool(context_variables):
        if scoped:
            ScopedContextWriter(policy, DETERMINISTIC_TOOL_WRITER).set(context_variables, READY, True)
        else:
            _set_ready(context_variables)

    _wrap_tool_with_context(tool, bridge)()
    assert bridge.get(READY) is True
    updates = bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN)
    assert updates == {"set": {READY: True}, "delete": []}
    assert bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN) == {
        "set": {}, "delete": [],
    }


@pytest.mark.parametrize("operation", ["set", "delete"])
@pytest.mark.parametrize("key", [READY, "ui_approved", "app_id"])
def test_ordinary_bridge_cannot_claim_tool_or_server_authority(operation, key):
    bridge, _ = _bridge()
    before = bridge.snapshot()
    with pytest.raises(ContextAuthorityError):
        bridge.set(key, True) if operation == "set" else bridge.delete(key)
    assert bridge.snapshot() == before
    assert bridge.consume_context_updates() == {"set": {}, "delete": []}


@pytest.mark.parametrize("operation", ["set", "delete"])
@pytest.mark.parametrize("key", ["ui_approved", "app_id"])
def test_actual_tool_cannot_write_undeclared_mechanism_or_server_state(operation, key):
    bridge, _ = _bridge()
    before = bridge.snapshot()

    def tool(context_variables):
        context_variables.set(key, True) if operation == "set" else context_variables.delete(key)

    with pytest.raises(ContextAuthorityError):
        _wrap_tool_with_context(tool, bridge)()
    assert bridge.snapshot() == before
    assert bridge.consume_context_updates() == {"set": {}, "delete": []}


def test_tool_before_run_binding_cannot_create_adoptable_pending_authority():
    bridge, policy = _bridge(bind=False)
    with pytest.raises(ContextAuthorityError):
        _wrap_tool_with_context(_set_ready, bridge)()
    bridge._bind_run(RUN, policy)
    assert bridge.get(READY) is False
    assert bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN) == {
        "set": {}, "delete": [],
    }


def test_caller_writer_string_and_scoped_writer_cannot_elevate_ordinary_bridge():
    bridge, policy = _bridge()
    with pytest.raises(TypeError):
        ContextVariablesBridge({}, authority_policy=policy, writer_id="deterministic_tool")
    with pytest.raises(AttributeError):
        bridge.writer_id = "deterministic_tool"
    with pytest.raises(ContextAuthorityError):
        ScopedContextWriter(policy, DETERMINISTIC_TOOL_WRITER).set(bridge, READY, True)
    assert bridge.get(READY) is False


@pytest.mark.parametrize("writer", [
    CONTEXT_BRIDGE_WRITER, UI_RESPONSE_TRIGGER_WRITER, LIVE_USER_CONTEXT_WRITER, AGENT_TEXT_WRITER,
])
def test_untrusted_update_cannot_request_deterministic_attribution(writer):
    with pytest.raises(ContextAuthorityError, match="untrusted_writer_attribution"):
        runner._authorized_context_updates(
            {READY: True}, writer_id=writer, elevated_writer_id=DETERMINISTIC_TOOL_WRITER,
            context_authority_policy=_policy(),
        )


@pytest.mark.parametrize("replacement", ["keyword", "positional", "metadata"])
def test_tool_caller_cannot_replace_hidden_context_or_supply_writer_privilege(replacement):
    bridge, _ = _bridge()
    entered = []

    def tool(value, context_variables=None, **metadata):
        entered.append(value)
        assert context_variables is bridge
        return metadata

    wrapped = _wrap_tool_with_context(tool, bridge)
    assert "context_variables" not in inspect.signature(wrapped).parameters
    if replacement == "keyword":
        with pytest.raises(ContextAuthorityError, match="tool_context_override"):
            wrapped("value", context_variables={})
    elif replacement == "positional":
        with pytest.raises(TypeError):
            wrapped("value", {})
    else:
        assert wrapped("value", writer_id="deterministic_tool") == {"writer_id": "deterministic_tool"}
        with pytest.raises(ContextAuthorityError):
            bridge.set(READY, True)
    assert entered == (["value"] if replacement == "metadata" else [])
    assert bridge.get(READY) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_mode", ["return", "error", "cancel"])
async def test_child_task_inherits_revocation_after_tool_exit(exit_mode):
    bridge, _ = _bridge()
    release = asyncio.Event()
    started = asyncio.Event()
    children = []

    async def escaped(context_variables):
        await release.wait()
        context_variables.set(READY, True)

    async def tool(context_variables):
        children.append(asyncio.create_task(escaped(context_variables)))
        started.set()
        if exit_mode == "error":
            raise ValueError("tool failed")
        if exit_mode == "cancel":
            await asyncio.Event().wait()

    task = asyncio.create_task(_wrap_tool_with_context(tool, bridge)())
    await started.wait()
    if exit_mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif exit_mode == "error":
        with pytest.raises(ValueError, match="tool failed"):
            await task
    else:
        await task
    release.set()
    with pytest.raises(ContextAuthorityError):
        await children[0]
    assert bridge.get(READY) is False
    assert bridge.consume_context_updates() == {"set": {}, "delete": []}


@pytest.mark.asyncio
async def test_nested_tool_scopes_restore_only_their_own_bridge():
    first, policy = _bridge()
    second = ContextVariablesBridge({READY: False}, authority_policy=policy)
    second._bind_run((RUN[0], RUN[1], "chat-2"), policy)

    async def inner(context_variables):
        context_variables.set(READY, True)
        with pytest.raises(ContextAuthorityError):
            first.set(READY, True)

    async def outer(context_variables):
        with pytest.raises(ContextAuthorityError):
            second.set(READY, True)
        await _wrap_tool_with_context(inner, second)()
        context_variables.set(READY, True)

    await _wrap_tool_with_context(outer, first)()
    assert first.get(READY) is True
    assert second.get(READY) is True
    for bridge in (first, second):
        with pytest.raises(ContextAuthorityError):
            bridge.delete(READY)


@pytest.mark.parametrize("other_run", [
    ("OtherFlow", RUN[1], RUN[2]), (RUN[0], "app-2", RUN[2]), (RUN[0], RUN[1], "chat-2"),
])
def test_pending_mutation_cannot_be_projected_into_another_run(other_run):
    bridge, policy = _bridge()
    _wrap_tool_with_context(_set_ready, bridge)()
    with pytest.raises(ContextAuthorityError, match="bridge_(workflow|run)_mismatch"):
        bridge.consume_authorized_context_updates(policy=policy, run_identity=other_run)
    assert bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN)["set"] == {READY: True}


@pytest.mark.parametrize("missing_policy", [False, True])
def test_equal_declarations_or_missing_policy_cannot_replace_bound_policy(missing_policy):
    bridge, policy = _bridge()
    _wrap_tool_with_context(_set_ready, bridge)()
    with pytest.raises(ContextAuthorityError, match="bridge_policy_mismatch"):
        bridge.consume_authorized_context_updates(
            policy=None if missing_policy else _policy(), run_identity=RUN,
        )
    assert bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN)["set"] == {READY: True}


@pytest.mark.parametrize("last_operation", ["set", "delete"])
def test_final_operation_preserves_attribution_and_detaches_values(last_operation):
    bridge, policy = _bridge()

    def tool(context_variables):
        context_variables.set(READY, True)
        context_variables.delete(READY)
        if last_operation == "set":
            context_variables.set(READY, True)

    _wrap_tool_with_context(tool, bridge)()
    values = {"nested": ["safe"]}
    bridge.set("note", values)
    values["nested"].append("caller mutation")
    updates = bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN)
    assert updates["set"]["note"] == {"nested": ["safe"]}
    assert updates["delete"] == ([READY] if last_operation == "delete" else [])
    assert (READY in updates["set"]) == (last_operation == "set")
    updates["set"]["note"]["nested"].append("packet mutation")
    assert bridge.snapshot()["note"] == {"nested": ["safe"]}


def test_projection_revalidates_whole_pending_batch_before_consuming():
    bridge, policy = _bridge()
    _wrap_tool_with_context(_set_ready, bridge)()
    bridge.set("note", "allowed")
    original = policy.variables[READY]
    # Revocation after mutation must still be enforced at packet projection.
    policy.variables[READY] = replace(original, writer_ids=frozenset({UI_RESPONSE_TRIGGER_WRITER}))
    with pytest.raises(ContextAuthorityError):
        bridge.consume_authorized_context_updates(policy=policy, run_identity=RUN)
    assert bridge.consume_context_updates() == {"set": {READY: True, "note": "allowed"}, "delete": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_bridge", [False, True])
@pytest.mark.parametrize("operation,key", [("set", READY), ("delete", READY), ("delete", "app_id")])
async def test_raw_packet_metadata_never_inherits_tool_attribution(monkeypatch, with_bridge, operation, key):
    bridge, policy = _bridge()
    sent = []
    handlers = []

    async def send(envelope):
        sent.append(envelope)
        return "sent"

    client = SimpleNamespace(send_envelope=send, on_envelope=handlers.append)
    agent = SimpleNamespace(_mozaiks_context_bridge=bridge) if with_bridge else SimpleNamespace()
    updates = {"set": {key: True} if operation == "set" else {},
               "delete": [key] if operation == "delete" else [],
               "writer_id": "deterministic_tool"}
    outgoing = SimpleNamespace(event_type=runner.EV_PACKET, channel_id="channel-1", event_data={
        "context_updates": updates, "writer_id": "deterministic_tool",
    })

    async def default_handler(envelope, active_client):
        if with_bridge:
            _wrap_tool_with_context(_set_ready, bridge)()
        await active_client.send_envelope(outgoing)

    monkeypatch.setattr(runner, "default_handler", default_handler)
    runner._install_context_update_handler(
        agent=agent, client=client, agent_name="A", run_identity=RUN, context_authority_policy=policy,
    )
    with pytest.raises(ContextAuthorityError):
        await handlers[0](SimpleNamespace(channel_id="channel-1"))
    assert sent == []
    assert client.send_envelope is send
    assert bridge.consume_context_updates() == {"set": {}, "delete": []}
