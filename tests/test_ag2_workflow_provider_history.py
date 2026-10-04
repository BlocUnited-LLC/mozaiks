"""Provider-visible history through actual AG2 agents, channels, and reopen."""

from types import SimpleNamespace

import pytest
from ag2 import Agent
from ag2.config.openai.mappers import convert_messages, events_to_responses_input
from ag2.events import ModelMessage, ModelRequest, ModelResponse, ToolCallEvent, ToolResultEvent
from ag2.knowledge import DiskKnowledgeStore
from ag2.network import ChannelMetadata, ChannelState, Envelope, WorkflowAdapter
from ag2.network.channel import Participant, ParticipantRole

from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.adapters.ag2_workflow_view import WorkflowHistoryAdapter, _AssistantResponseView
from mozaiksai.core.ports.orchestration import RunStatus

QUESTION = "I found a private community app. Does that match your experience?"
CONFIRMATION = "Yes, that matches the app. Identity and experience are confirmed."


def _provider_payload(provider, events, prompt=(), serializer=None):
    if provider == "chat":
        return convert_messages(prompt, events, serializer)
    if provider == "responses":
        return events_to_responses_input(events, serializer)
    from ag2.config.anthropic.mappers import convert_messages as anthropic_messages

    return anthropic_messages(events, serializer)


def _has_assistant_question(messages):
    return any(row.get("role") == "assistant" and QUESTION in str(row.get("content")) for row in messages)


class _CaptureConfig:
    model = "offline-history-test"
    provider = "openai"

    def __init__(self, mapper, *, self_turn=False):
        self.mapper = mapper
        self.self_turn = self_turn
        self.calls = []

    def copy(self):
        return self

    def create(self):
        async def client(messages, context, *, tools, response_schema, serializer):
            payload = _provider_payload(self.mapper, messages, context.prompt, serializer)
            self.calls.append(payload)
            # A model-independent assertion of what the provider can remember.
            confirmed = _has_assistant_question(payload) or (self.self_turn and QUESTION in str(payload))
            answer = "NEXT" if confirmed else QUESTION
            return ModelResponse(ModelMessage(answer))

        return client


def _request(config, root, *, initial_message="Inspect the synthetic app.", self_edge=False):
    return AG2NetworkRunnerRequest(
        workflow_name="ProviderHistory", chat_id="provider-history", app_id="synthetic-app",
        agents={"Host": Agent("Host", prompt="Ask once, then accept the user's confirmation.", config=config)},
        initial_agent_name="Host", initial_message=initial_message,
        knowledge_store=DiskKnowledgeStore(root), max_turns=6, idle_timeout_seconds=3,
        agent_text_context_deriver=lambda _name, text: {"confirmed": text == "NEXT"},
        transition_rules=[
            {"source_agent": "user", "target_agent": "Host", "transition_type": "after_turn"},
            {"source_agent": "Host", "target_agent": "terminate", "transition_type": "condition",
             "condition_type": "context_equals", "condition_key": "confirmed", "condition_value": True,
             "termination_reason": "workflow_complete"},
            {"source_agent": "Host", "target_agent": "Host" if self_edge else "user", "transition_type": "after_turn"},
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("reopen", [False, True])
async def test_provider_keeps_assistant_reply_across_human_continuation(provider, reopen, tmp_path):
    if provider == "anthropic":
        pytest.importorskip("anthropic")
    config = _CaptureConfig(provider)
    first = await AG2NetworkRunner().run(_request(config, tmp_path))
    assert first.status is RunStatus.PAUSED, first.error
    assert first.live_run is not None
    live = first.live_run
    try:
        if reopen:
            await live.close()
            # A new Hub, agent, and DiskKnowledgeStore instance read saved WAL.
            resumed = await AG2NetworkRunner().run(_request(config, tmp_path, initial_message=CONFIRMATION))
        else:
            resumed = await live.continue_with_user_message(CONFIRMATION)
        assert resumed.status is RunStatus.COMPLETED, resumed.error
        assert resumed.channel_id == first.channel_id
        assert len(config.calls) == 2
        assert _has_assistant_question(config.calls[1])
        assert CONFIRMATION in str(config.calls[1])
        assert sum(row.get("role") == "assistant" for row in config.calls[1]) == 1
    finally:
        await live.close()


@pytest.mark.asyncio
async def test_provider_history_preserves_declared_self_edge(tmp_path):
    config = _CaptureConfig("chat", self_turn=True)
    result = await AG2NetworkRunner().run(_request(config, tmp_path, self_edge=True))
    assert result.status is RunStatus.COMPLETED, result.error
    assert len(config.calls) == 2
    assert any(row.get("role") == "user" and row.get("content") == QUESTION for row in config.calls[1])


def _metadata():
    return ChannelMetadata(
        channel_id="history", manifest=WorkflowAdapter().manifest, creator_id="user",
        participants=[Participant(name, ParticipantRole.PARTICIPANT, index) for index, name in enumerate(("user", "host", "peer"))],
        state=ChannelState.ACTIVE, created_at="2026-01-01T00:00:00Z",
    )


def _packet(sender, text, audience=None):
    return Envelope(channel_id="history", sender_id=sender, audience=audience,
                    event_type="ag2.packet", event_data={"body": text, "routing": {"kind": "text"}})


@pytest.mark.asyncio
async def test_view_retains_ag2_window_peer_labels_and_private_visibility():
    adapter = WorkflowHistoryAdapter()
    metadata = _metadata()
    # Native window is six substantive messages for three participants.
    wal = [_packet("user", f"old turn {index}") for index in range(8)] + [
        _packet("peer", "visible peer contribution"),
        _packet("peer", "PRIVATE_OTHER_PARTICIPANT", audience=["user"]),
        _packet("host", QUESTION),
    ]
    native = await WorkflowAdapter().default_view_policy(metadata, "host").project(
        wal, participant_id="host", channel=metadata, render_envelope=adapter.render_envelope,
    )
    projected = await adapter.default_view_policy(metadata, "host").project(
        wal, participant_id="host", channel=metadata, render_envelope=adapter.render_envelope,
    )
    assert len(projected) == len(native) == 7
    assert type(projected[0]).__name__ == "CompactionSummary"
    payload = _provider_payload("chat", projected)
    peer_rows = [row for row in payload if "[peer]: visible peer contribution" in str(row.get("content"))]
    assert len(peer_rows) == 1
    assert peer_rows[0]["role"] == "user"
    assert "PRIVATE_OTHER_PARTICIPANT" not in str(projected)
    assert "old turn 0" not in str(payload)
    assert _has_assistant_question(payload)
    assert sum(row.get("role") == "assistant" for row in payload) == 1


@pytest.mark.asyncio
async def test_view_preserves_existing_responses_tool_events_and_message_metadata():
    message = ModelMessage(QUESTION, metadata={"origin": "projected"})
    response = ModelResponse(ModelMessage("Existing complete response"))
    call = ToolCallEvent(id="call-1", name="read_record", arguments="{}")
    result = ToolResultEvent(parent_id="call-1", result="record found")
    request = ModelRequest("Continue")
    events = [request, response, call, result, message]

    async def project(*args, **kwargs):
        return events

    normalized = await _AssistantResponseView(SimpleNamespace(name="native", project=project)).project(
        [], participant_id="host", channel=_metadata(), render_envelope=WorkflowAdapter().render_envelope,
    )
    assert all(normalized[index] is event for index, event in enumerate(events[:-1]))
    assert normalized[-1].message is message
    assert normalized[-1].message.metadata == {"origin": "projected"}
    assert not normalized[-1].usage.total_tokens


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="AG2-WP-015: remove local view adapter when native provider history preserves assistant replies")
async def test_upstream_native_assistant_history_retirement_gate():
    adapter = WorkflowAdapter()
    metadata = _metadata()
    projected = await adapter.default_view_policy(metadata, "host").project(
        [_packet("host", QUESTION)], participant_id="host", channel=metadata,
        render_envelope=adapter.render_envelope,
    )
    # This event-shape requirement applies to every provider mapper, including
    # optional providers whose SDK is not installed in this test environment.
    assert not any(isinstance(event, ModelMessage) for event in projected)
    assert any(isinstance(event, ModelResponse) and event.message.content == QUESTION for event in projected)
    for provider in ("chat", "responses"):
        assert _has_assistant_question(_provider_payload(provider, projected))
