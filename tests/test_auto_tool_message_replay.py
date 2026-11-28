"""A validated auto-tool question survives reconnect without becoming model history."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from ag2.events import ModelResponse
from ag2.events.types import ModelMessage
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.adapters import ag2_stream_storage as stream_storage
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.events import auto_tool_handler
from mozaiksai.core.transport.run_replay import WorkflowRunReplayer
from mozaiksai.core.workflow.execution.run_bootstrap import prepare_network_trigger
from tests import test_factory_auto_tool_acceptance as factory_acceptance
from tests.test_ag2_stream_storage import _FakeEventsCollection, _FakeHeadsCollection
from tests.test_factory_auto_tool_acceptance import _PatternContext

factory_manager = factory_acceptance.factory_manager


class Events(_FakeEventsCollection):
    async def find_one(self, query):
        return next((deepcopy(doc) for doc in self.docs if all(doc.get(key) == value for key, value in query.items())), None)

    async def insert_one(self, doc):
        if any(existing["_id"] == doc["_id"] for existing in self.docs):
            raise DuplicateKeyError("duplicate _id")
        await super().insert_one(doc)


@pytest.fixture
def durable(monkeypatch):
    events, heads = Events(), _FakeHeadsCollection()
    real_storage = stream_storage.MongoAG2StreamStorage
    def storage(*, app_id):
        return real_storage(app_id=app_id, events_collection=events, heads_collection=heads)
    monkeypatch.setattr(stream_storage, "MongoAG2StreamStorage", storage)
    pm = AG2PersistenceManager()
    monkeypatch.setattr(pm, "persist_context_variables", AsyncMock())
    monkeypatch.setattr(pm, "get_workflow_tool_call_states", AsyncMock(return_value=[]))
    monkeypatch.setattr(pm, "get_pending_input_request", AsyncMock(return_value=None))
    monkeypatch.setattr(auto_tool_handler, "AG2PersistenceManager", lambda: pm)
    transport = SimpleNamespace(send_event_to_ui=AsyncMock())
    monkeypatch.setattr(auto_tool_handler, "_get_simple_transport", AsyncMock(return_value=transport))
    return SimpleNamespace(events=events, heads=heads, pm=pm, storage=storage, transport=transport)


def event(pattern, *, extra=None):
    return {"auto_tool_call": True, "agent_name": "ThemeInterviewAgent", "model_name": "ThemeInterviewResult",
            "structured_data": {"agent_message": "Keep the current logo and use a darker primary color?", "outcome": "needs_input", **(extra or {})},
            "context": {"workflow_name": "ThemeCapture", "chat_id": "chat", "app_id": "host", "user_id": "owner"},
            "turn_idempotency_key": "turn-one", "_pattern_context_ref": pattern}


@pytest.mark.asyncio
async def test_real_theme_question_is_durable_before_emit_and_replays_once(factory_manager, durable, monkeypatch):
    from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
    from mozaiksai.core.usage.middleware import MozaiksUsageMiddleware
    model_call, usage_call = AsyncMock(side_effect=AssertionError("No model call")), AsyncMock(side_effect=AssertionError("No usage call"))
    monkeypatch.setattr(AG2StructuredAgentRunner, "run", model_call)
    monkeypatch.setattr(MozaiksUsageMiddleware, "on_llm_call", usage_call)
    pattern = _PatternContext()
    pattern.set("interview_attempts", 0)
    packet = event(pattern)
    question = packet["structured_data"]["agent_message"]
    await durable.pm.append_run_assistant_message(chat_id="chat", app_id="host", content='{"outcome":"needs_input"}',
        agent_name="ThemeInterviewAgent", metadata={"source": "ag2_network_wal", "ui_visibility": "hidden"})
    await durable.pm.append_run_user_message(chat_id="chat", app_id="host", content="Keep the logo.")
    async def emit(payload, chat):
        if payload["kind"] == "tool_call":
            history = await durable.pm.load_run_history(chat_id=chat, app_id="host")
            assert history[-1]["content"] == question
    durable.transport.send_event_to_ui.side_effect = emit
    await auto_tool_handler.AutoToolEventHandler().handle_tool_dispatch(packet)
    assert pattern.get("interview_outcome") == "needs_input"
    # A fresh handler has no in-memory processed-turn cache. WAL identity owns deduplication.
    await auto_tool_handler.AutoToolEventHandler().handle_tool_dispatch(packet)
    history = await durable.pm.load_run_history(chat_id="chat", app_id="host")
    assert [message["content"] for message in history] == ["Keep the logo.", question]
    assert history[-1]["metadata"]["type"] == "tool_call_agent_message"
    assert history[-1]["metadata"]["tool_call_id"] == "turn-one"
    assert "structured_output" not in str(history)
    replay = WorkflowRunReplayer()
    monkeypatch.setattr(replay, "_get_persistence_manager", lambda: durable.pm)
    monkeypatch.setattr(replay, "_fetch_chat_doc", AsyncMock(return_value={"status": 0, "workflow_name": "ThemeCapture", "user_id": "owner"}))
    monkeypatch.setattr(replay, "_load_resume_state", AsyncMock(return_value=None))
    sent = []
    async def send(payload, chat):
        sent.append(payload)
    await replay.handle_resume_request(chat_id="chat", app_id="host", last_client_index=-1, send_event=send)
    assert [payload["content"] for payload in sent if payload["kind"] == "text"] == ["Keep the logo.", question]
    sent.clear()
    await replay.handle_resume_request(chat_id="chat", app_id="host", last_client_index=1, send_event=send)
    assert not [payload for payload in sent if payload["kind"] == "text"]
    # UI assistant projections cannot become native model history or a new model/usage call.
    trigger = await prepare_network_trigger(durable.pm, {"workflow_startup_mode": "AgentDriven"}, "chat", "host", "ThemeCapture", "owner", None, SimpleNamespace(debug=lambda *args: None))
    assert trigger == "Keep the logo."
    model_call.assert_not_awaited()
    usage_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_output_and_tool_owned_cards_do_not_append(factory_manager, durable, monkeypatch):
    pattern = _PatternContext()
    await auto_tool_handler.AutoToolEventHandler().handle_tool_dispatch(event(pattern, extra={"undeclared": "reject"}))
    assert durable.events.docs == []
    durable.transport.send_event_to_ui.assert_not_awaited()
    binding = auto_tool_handler.AutoToolBinding(model_name="Card", agent_name="CardAgent", tool_name="card", function=lambda: None,
        param_names=(), accepts_context=False, ui_config={}, model_cls=dict, self_emits_ui=True)
    await auto_tool_handler.AutoToolEventHandler()._emit_tool_call(binding, "CardAgent", "chat", {"agent_message": "Owned by the card"}, "turn", app_id="host", persist_agent_message=True)
    assert durable.events.docs == []
    assert [call.args[0]["kind"] for call in durable.transport.send_event_to_ui.await_args_list] == ["select_speaker"]
    component = replace(binding, self_emits_ui=False, ui_config={"component": "ExistingCard"})
    await auto_tool_handler.AutoToolEventHandler()._emit_tool_call(component, "CardAgent", "chat", {"agent_message": "Owned by the card"}, "turn", app_id="host", persist_agent_message=True)
    assert durable.events.docs == []


@pytest.mark.asyncio
async def test_two_bindings_share_the_actual_displayed_question_identity(factory_manager, durable, monkeypatch):
    handler = auto_tool_handler.AutoToolEventHandler()
    bindings = await handler._resolve_bindings("ThemeCapture", "ThemeInterviewResult", "ThemeInterviewAgent")
    assert len(bindings) == 1
    monkeypatch.setattr(handler, "_resolve_bindings", AsyncMock(return_value=[bindings[0], replace(bindings[0], tool_name="second_binding")]))
    pattern = _PatternContext()
    pattern.set("interview_attempts", 0)
    await handler.handle_tool_dispatch(event(pattern))
    assert len(durable.events.docs) == 1
    tool_events = [call.args[0] for call in durable.transport.send_event_to_ui.await_args_list if call.args[0]["kind"] == "tool_call"]
    assert len(tool_events) == 2
    assert {payload["tool_call_id"] for payload in tool_events} == {"turn-one"}
    assert list(await durable.pm.load_run_history(chat_id="chat", app_id="host"))[0]["metadata"]["tool_call_id"] == "turn-one"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_app", [False, True])
async def test_failed_durable_append_does_not_emit_question_or_invoke_tool(factory_manager, durable, monkeypatch, missing_app):
    packet = event(_PatternContext())
    if missing_app:
        packet["context"].pop("app_id")
    else:
        monkeypatch.setattr(durable.pm, "append_run_assistant_message", AsyncMock(side_effect=RuntimeError("store unavailable")))
    handler = auto_tool_handler.AutoToolEventHandler()
    invoke = AsyncMock()
    monkeypatch.setattr(handler, "_invoke_tool", invoke)
    with pytest.raises(ValueError if missing_app else RuntimeError, match="execution app" if missing_app else "store unavailable"):
        await handler.handle_tool_dispatch(packet)
    durable.transport.send_event_to_ui.assert_not_awaited()
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyed_message_roundtrip_conflict_and_scope(durable):
    async def append(*, app="host", chat="chat", content="Question?", key="turn-key"):
        await durable.pm.append_run_assistant_message(chat_id=chat, app_id=app, content=content, agent_name="Interviewer",
            metadata={"source": "auto_tool_agent_message", "presentation_only": True}, idempotency_key=key)
    await append()
    original = deepcopy(durable.events.docs)
    assert "turn-key" not in str(original)
    await append()
    assert durable.events.docs == original
    storage = durable.storage(app_id="host")
    stream_id = stream_storage.stream_id_for_run("host", "chat")
    restored = list(await storage.get_history(stream_id))
    await storage.set_history(stream_id, [*restored, *restored])
    assert len(durable.events.docs) == 1
    await append()
    assert len(durable.events.docs) == 1
    before = deepcopy(durable.events.docs)
    with pytest.raises(ValueError, match="different content"):
        await append(content="Changed question")
    assert durable.events.docs == before
    changed = list(await storage.get_history(stream_id))
    changed[0].message.content = "Changed question"
    with pytest.raises(ValueError, match="different content"):
        await storage.set_history(stream_id, changed)
    assert durable.events.docs == before
    malformed = list(await storage.get_history(stream_id))
    malformed[0].message.metadata["wal_message_key_sha256"] = "not-a-digest"
    with pytest.raises(ValueError, match="SHA-256"):
        await storage.set_history(stream_id, malformed)
    assert durable.events.docs == before
    # A replacement with two conflicting copies is rejected before deleting the
    # old stream, even when its opaque key does not exist in that old stream.
    new_message = ModelResponse(ModelMessage("First", metadata={"wal_message_key_sha256": "f" * 64}),
        model="mozaiks.runtime", provider="mozaiks.runtime", finish_reason="stop")
    conflict = ModelResponse(ModelMessage("Second", metadata={"wal_message_key_sha256": "f" * 64}),
        model="mozaiks.runtime", provider="mozaiks.runtime", finish_reason="stop")
    with pytest.raises(ValueError, match="different content"):
        await storage.set_history(stream_id, [new_message, conflict])
    assert durable.events.docs == before
    with pytest.raises(ValueError, match="requested identity"):
        await storage.save_event(new_message, SimpleNamespace(stream=SimpleNamespace(id=stream_id)), idempotency_key="different-key")
    assert durable.events.docs == before
    await append(app="other-app")
    await append(chat="other-chat")
    assert len(durable.events.docs) == 3
    for key in ("", "x" * 2049):
        with pytest.raises(ValueError, match="2048"):
            await append(key=key)
    assert len(durable.events.docs) == 3


@pytest.mark.asyncio
async def test_keyed_storage_scopes_same_explicit_stream_to_execution_app(durable):
    stream_id = uuid4()
    context = SimpleNamespace(stream=SimpleNamespace(id=stream_id))
    def message():
        return ModelResponse(ModelMessage("Question?"), model="mozaiks.runtime", provider="mozaiks.runtime", finish_reason="stop")
    first, second = durable.storage(app_id="first"), durable.storage(app_id="second")
    await first.save_event(message(), context, idempotency_key="same-key")
    await second.save_event(message(), context, idempotency_key="same-key")
    assert len(durable.events.docs) == 2
    assert len({doc["_id"] for doc in durable.events.docs}) == 2
    first_events = list(await first.get_history(stream_id))
    await first.set_history(stream_id, first_events)
    await first.save_event(message(), context, idempotency_key="same-key")
    await second.save_event(message(), context, idempotency_key="same-key")
    assert len(durable.events.docs) == 2
    assert len(list(await first.get_history(stream_id))) == 1
    assert len(list(await second.get_history(stream_id))) == 1


def test_actual_chat_renderer_merges_saved_question_on_same_ui_reconnect():
    root = Path(__file__).resolve().parents[1]
    source = (root / "chat-ui/src/pages/ChatPage.js").read_text(encoding="utf-8")
    text = source.split("case 'text': {", 1)[1].split("case 'tool_call': {", 1)[0]
    tool = source.split("case 'tool_call': {", 1)[1].split("case 'ui.render': {", 1)[0]
    callback = "data => { switch(data.kind) { case 'text': {" + text + "case 'tool_call': {" + tool + "} }"
    helper_names = ("sanitizeVisibleWorkflowMessages", "mergeWorkflowTranscriptMessages", "shouldSuppressConsecutiveAssistantDuplicate")
    helpers = "\n".join("const " + name + " = useCallback(" + source.split("const " + name + " = useCallback(", 1)[1].split("\n  }, [", 1)[0] + "\n});" for name in helper_names)
    script = r"""
const assert = require('node:assert/strict');
const { callback, helpers } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let messages = [];
const messagesRef = { current: messages };
const useCallback = fn => fn;
const shouldSuppressHiddenWorkflowMessage = () => false;
const {sanitizeVisibleWorkflowMessages, mergeWorkflowTranscriptMessages, shouldSuppressConsecutiveAssistantDuplicate} = eval('(() => {' + helpers + '; return {sanitizeVisibleWorkflowMessages, mergeWorkflowTranscriptMessages, shouldSuppressConsecutiveAssistantDuplicate};})()');
const setMessagesWithLogging = update => { messages = sanitizeVisibleWorkflowMessages(update(messages)); messagesRef.current = messages; };
const debugFlag = () => false;
const shouldSuppressHiddenSeedEvent = () => false;
const extractAgentName = data => data.agent || data.agent_name;
const conversationMode = 'workflow';
const workflowReplayPendingRef = { current: false };
const showInitSpinner = false, showSystemMessages = false, isMobileView = false;
const handle = eval('(' + callback + ')');
const live = {kind:'tool_call', tool_call_id:'turn-one', agent:'ThemeInterviewAgent',
  payload:{interaction_type:'auto_tool', agent_message:'Keep the logo?'}};
const replay = {kind:'text', replay:true, index:0, agent:'ThemeInterviewAgent', role:'assistant',
  content:'Keep the logo?', metadata:{source:'auto_tool_agent_message',presentation_only:true,
    type:'tool_call_agent_message',tool_call_id:'turn-one'}};
handle(live);
handle(replay);
handle(replay);
handle(live);
assert.equal(messages.length, 1);
assert.equal(messages[0].id, 'turn-one-agent-message');
assert.equal(messages[0].replayIndex, 0);
assert.equal(messages[0].metadata.tool_call_id, 'turn-one');
assert.equal(messages[0].toolCall, undefined);
assert.equal(messages[0].structuredOutput, undefined);
messages = []; messagesRef.current = messages;
handle(replay); handle(replay); handle(live);
assert.equal(messages.length, 1);
assert.equal(messages[0].content, 'Keep the logo?');
assert.equal(messages[0].toolCall, undefined);
handle({...live,tool_call_id:'turn-two'});
assert.equal(messages.length, 2);
handle({...replay,index:1,metadata:{...replay.metadata,tool_call_id:'turn-two'}});
handle(replay);
handle({...live,tool_call_id:'turn-two'});
assert.equal(messages.length, 2);
assert.deepEqual(messages.map(message => message.id), ['turn-one-agent-message','turn-two-agent-message']);
handle({...replay,content:'{"agent_message":"private"}',metadata:{ui_visibility:'hidden'}});
assert.equal(messages.length, 2);
const cachedLive = {id:'turn-one-agent-message',sender:'agent',agentName:'ThemeInterviewAgent',content:'Keep the logo?',
  metadata:{type:'tool_call_agent_message',tool_call_id:'turn-one'}};
const saved = {...cachedLive,id:'text-original',replayIndex:0,metadata:{...replay.metadata}};
const next = {...saved,id:'text-next',replayIndex:1,metadata:{...saved.metadata,tool_call_id:'turn-two'}};
assert.equal(mergeWorkflowTranscriptMessages([cachedLive],[saved]).length, 1);
assert.equal(mergeWorkflowTranscriptMessages([cachedLive],[saved,next]).length, 2);
const unmarked = {...cachedLive,metadata:{source:'agent'}};
assert.equal(sanitizeVisibleWorkflowMessages([unmarked,{...unmarked}]).length, 1);
"""
    result = subprocess.run(["node", "--eval", script], input=json.dumps({"callback": callback, "helpers": helpers}),
        cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_real_mongo_message_retries_share_one_durable_projection():
    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.getenv("MONGO_URI")
    if not uri:
        pytest.skip("MONGO_URI is not configured")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1500)
    database_name = "auto_tool_message_replay_" + uuid4().hex
    try:
        await client.admin.command("ping")
        database = client[database_name]
        print("AUTO_TOOL_MESSAGE_MONGO_DATABASE=" + database_name)
        def storage():
            return stream_storage.MongoAG2StreamStorage(app_id="host", events_collection=database.Events, heads_collection=database.Heads)
        stream_id = stream_storage.stream_id_for_run("host", "chat")
        def message(text="Question?"):
            return ModelResponse(ModelMessage(text, metadata={"source": "auto_tool_agent_message"}), model="mozaiks.runtime", provider="mozaiks.runtime", finish_reason="stop")
        context = SimpleNamespace(stream=SimpleNamespace(id=stream_id))
        await asyncio.gather(*(storage().save_event(message(), context, idempotency_key="turn-key") for _ in range(4)))
        assert await database.Events.count_documents({}) == 1
        persisted = list(await storage().get_history(stream_id))
        await storage().set_history(stream_id, persisted)
        await storage().save_event(message(), context, idempotency_key="turn-key")
        assert await database.Events.count_documents({}) == 1
        with pytest.raises(ValueError, match="different content"):
            await storage().save_event(message("Changed"), context, idempotency_key="turn-key")
        assert await database.Events.count_documents({}) == 1
        peer = stream_storage.MongoAG2StreamStorage(app_id="other-app", events_collection=database.Events, heads_collection=database.Heads)
        await peer.save_event(message(), context, idempotency_key="turn-key")
        assert await database.Events.count_documents({}) == 2
        peer_history = list(await peer.get_history(stream_id))
        await peer.set_history(stream_id, [*peer_history, *peer_history])
        await peer.save_event(message(), context, idempotency_key="turn-key")
        assert await database.Events.count_documents({}) == 2
        old = await database.Events.find({}).sort("_id", 1).to_list(length=10)
        peer_history[0].message.content = "Changed"
        with pytest.raises(ValueError, match="different content"):
            await peer.set_history(stream_id, peer_history)
        assert await database.Events.find({}).sort("_id", 1).to_list(length=10) == old
    finally:
        # Keep this isolated UUID test database for acceptance review.
        client.close()
