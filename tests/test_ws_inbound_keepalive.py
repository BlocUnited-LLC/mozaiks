"""The transport heartbeat contract must be satisfiable by clients.

The server sends `ping` events and enforces an idle timeout, but until
`client.pong` was whitelisted, every message type a client could send as a
keepalive was rejected with SCHEMA_VALIDATION_FAILED — so a builder session
idle on a long agent turn was disconnected by its own server (observed live:
WS_IDLE_TIMEOUT severing a paused AgentGenerator interview).
"""

from __future__ import annotations

from tests.import_utils import import_module_directly

_transport = import_module_directly("mozaiksai.core.transport.simple_transport")
_handlers = import_module_directly("mozaiksai.core.transport.handlers")

SimpleTransport = _transport.SimpleTransport


def _validate(message: dict) -> bool:
    return SimpleTransport._validate_inbound_message(None, message)


def test_client_pong_is_accepted_as_keepalive():
    assert _validate({"type": "client.pong", "chat_id": "chat-1"}) is True
    # chat_id is not required for a pure keepalive frame.
    assert _validate({"type": "client.pong"}) is True


def test_unknown_types_remain_rejected():
    assert _validate({"type": "client.ping"}) is False
    assert _validate({"type": "keepalive"}) is False
    assert _validate({}) is False


def test_client_pong_has_no_handler_and_is_ignored_silently():
    """Dispatch ignores handler-less types; a pong must not invoke behavior."""
    registry = getattr(_handlers, "MESSAGE_HANDLERS", None)
    if registry is None:
        get_handler = getattr(SimpleTransport, "_get_message_handler", None)
        assert get_handler is not None
        assert get_handler(None, "client.pong") is None
    else:
        assert "client.pong" not in registry
