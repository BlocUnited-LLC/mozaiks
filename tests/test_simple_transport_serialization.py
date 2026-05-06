from mozaiksai.core.transport import simple_transport as _transport_mod
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.events import unified_event_dispatcher as _dispatcher_mod
def test_pre_connection_buffer_overflow_logs_once_per_chat(monkeypatch):
    transport = SimpleTransport()
    transport._max_pre_connection_buffer = 2
    warnings = []

    monkeypatch.setattr(
        _transport_mod.logger,
        "warning",
        lambda message, *args: warnings.append(message % args if args else message),
    )

    import asyncio

    async def _run() -> None:
        await transport._broadcast_to_websockets({"type": "chat.text", "data": {"content": "one"}}, "chat-1")
        await transport._broadcast_to_websockets({"type": "chat.text", "data": {"content": "two"}}, "chat-1")
        await transport._broadcast_to_websockets({"type": "chat.text", "data": {"content": "three"}}, "chat-1")
        await transport._broadcast_to_websockets({"type": "chat.text", "data": {"content": "four"}}, "chat-1")

    asyncio.run(_run())

    assert len(warnings) == 1
    assert "suppressing repeated overflow logs" in warnings[0]
    assert transport._pre_connection_buffer_overflow_counts["chat-1"] == 2


def test_chunk_text_for_stream_preserves_short_messages_word_level():
    transport = SimpleTransport()

    chunks = transport._chunk_text_for_stream("Short reply for user.")

    assert chunks == ["Short ", "reply ", "for ", "user."]


def test_chunk_text_for_stream_compacts_long_messages_without_losing_content():
    transport = SimpleTransport()
    content = (
        "This is a longer assistant message that should stream in larger chunks. "
        "It still needs to preserve punctuation boundaries where practical, while "
        "avoiding one websocket frame per word during long responses."
    )

    chunks = transport._chunk_text_for_stream(content)

    assert len(chunks) < len(content.split())
    assert "".join(chunks) == content
    assert all(chunk for chunk in chunks)


class RecursiveString:
    def __str__(self) -> str:
        return str(self)


def test_stringify_unknown_handles_recursive_str():
    transport = SimpleTransport()
    value = transport._stringify_unknown(RecursiveString())
    assert isinstance(value, str)
    assert "unserializable" in value.lower()


def test_serialize_ag2_events_handles_circular_refs():
    transport = SimpleTransport()
    payload = {}
    payload["self"] = payload

    serialized = transport._serialize_ag2_events(payload)

    assert isinstance(serialized, dict)
    assert "self" in serialized
    assert isinstance(serialized["self"], str)
    assert "circular_ref" in serialized["self"]


def test_background_run_summary_reports_active_tasks():
    transport = SimpleTransport()

    class _FakeTask:
        def __init__(self, name: str) -> None:
            self._name = name

        def get_name(self) -> str:
            return self._name

    transport._background_tasks = {
        "chat-1": _FakeTask("workflow:RuntimeSmoke:chat-1")
    }
    transport.connections = {
        "chat-1": {"workflow_name": "RuntimeSmoke", "user_id": "user-1"}
    }

    summary = transport.get_background_run_summary()

    assert summary == {
        "active_count": 1,
        "runs": [
            {
                "chat_id": "chat-1",
                "workflow_name": "RuntimeSmoke",
                "user_id": "user-1",
                "has_connection": True,
                "task_name": "workflow:RuntimeSmoke:chat-1",
            }
        ],
    }


async def _record_broadcast(events, event, chat_id):  # noqa: ANN001
    events.append((event, chat_id))


def test_send_event_to_ui_does_not_filter_run_complete_for_non_visual_agent(monkeypatch):
    transport = SimpleTransport()
    transport.connections = {"chat-1": {"workflow_name": "AppGenerator", "user_id": "user-1"}}
    sent = []

    class _FakeDispatcher:
        def build_outbound_event_envelope(self, *, raw_event, chat_id, get_sequence_cb, workflow_name):  # noqa: ANN001
            return {
                "type": "chat.run_complete",
                "data": {
                    "kind": "run_complete",
                    "agent": "AppGenerator",
                    "status": 1,
                },
            }

    monkeypatch.setattr(_dispatcher_mod, "get_event_dispatcher", lambda: _FakeDispatcher())
    monkeypatch.setattr(transport, "should_show_to_user", lambda agent_name, chat_id: False)
    monkeypatch.setattr(transport, "_broadcast_to_websockets", lambda event, chat_id=None: _record_broadcast(sent, event, chat_id))

    import asyncio

    asyncio.run(transport.send_event_to_ui({"kind": "run_complete", "agent": "AppGenerator"}, "chat-1"))

    assert sent == [
        (
            {
                "type": "chat.run_complete",
                "data": {
                    "kind": "run_complete",
                    "agent": "AppGenerator",
                    "status": 1,
                },
            },
            "chat-1",
        )
    ]
    assert transport.connections["chat-1"]["ui_run_complete_sent"] is True


def test_send_event_to_ui_does_not_filter_activity_for_non_visual_agent(monkeypatch):
    transport = SimpleTransport()
    transport.connections = {"chat-1": {"workflow_name": "AppGenerator", "user_id": "user-1"}}
    sent = []

    class _FakeDispatcher:
        def build_outbound_event_envelope(self, *, raw_event, chat_id, get_sequence_cb, workflow_name):  # noqa: ANN001
            return {
                "type": "chat.activity",
                "data": {
                    "kind": "activity",
                    "activity_type": "structured_output",
                    "agent": "AppGenerator",
                    "message": "AppGenerator produced validated plan output.",
                    "status": "validated",
                },
            }

    monkeypatch.setattr(_dispatcher_mod, "get_event_dispatcher", lambda: _FakeDispatcher())
    monkeypatch.setattr(transport, "should_show_to_user", lambda agent_name, chat_id: False)
    monkeypatch.setattr(transport, "_broadcast_to_websockets", lambda event, chat_id=None: _record_broadcast(sent, event, chat_id))

    import asyncio

    asyncio.run(
        transport.send_event_to_ui(
            {"kind": "activity", "agent": "AppGenerator", "message": "AppGenerator produced validated plan output."},
            "chat-1",
        )
    )

    assert sent == [
        (
            {
                "type": "chat.activity",
                "data": {
                    "kind": "activity",
                    "activity_type": "structured_output",
                    "agent": "AppGenerator",
                    "message": "AppGenerator produced validated plan output.",
                    "status": "validated",
                },
            },
            "chat-1",
        )
    ]
