from mozaiksai.core.transport.simple_transport import SimpleTransport


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
