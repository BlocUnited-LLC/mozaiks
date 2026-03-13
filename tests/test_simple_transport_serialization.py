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
