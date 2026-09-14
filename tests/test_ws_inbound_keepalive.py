from __future__ import annotations

from tests.import_utils import import_module_directly

_transport = import_module_directly("mozaiksai.core.transport.simple_transport")
_handlers = import_module_directly("mozaiksai.core.transport.handlers")

SimpleTransport = _transport.SimpleTransport


def _validate(message: dict) -> bool:
    return SimpleTransport._validate_inbound_message(None, message)


def test_client_pong_is_accepted_as_keepalive():
    assert _validate({"type": "client.pong", "chat_id": "chat-1"}) is True
    assert _validate({"type": "client.pong"}) is True


def test_unknown_types_remain_rejected():
    assert _validate({"type": "client.ping"}) is False
    assert _validate({"type": "keepalive"}) is False
    assert _validate({}) is False


def test_client_pong_has_no_handler():
    registry = getattr(_handlers, "MESSAGE_HANDLERS", None)
    if registry is None:
        get_handler = getattr(SimpleTransport, "_get_message_handler", None)
        assert get_handler is not None
        assert get_handler(None, "client.pong") is None
    else:
        assert "client.pong" not in registry
