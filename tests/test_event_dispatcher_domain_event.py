from __future__ import annotations

import importlib
import asyncio

import pytest

_dispatcher_mod = importlib.import_module("mozaiksai.core.events.unified_event_dispatcher")
_ports_mod = importlib.import_module("mozaiksai.core.ports.orchestration")

UnifiedEventDispatcher = _dispatcher_mod.UnifiedEventDispatcher
DomainEvent = _ports_mod.DomainEvent


@pytest.mark.asyncio
async def test_emit_domain_event_notifies_registered_listeners() -> None:
    dispatcher = UnifiedEventDispatcher()
    seen = []

    async def _listener(payload):  # type: ignore[no-untyped-def]
        seen.append(payload)

    dispatcher.register_handler("runtime.test_event", _listener)

    event = DomainEvent(
        kind="runtime.test_event",
        payload={"status": "ok"},
        chat_id="chat-123",
        source="unit_test",
    )

    success = await dispatcher.emit_domain_event(event)
    await asyncio.sleep(0)

    assert success is True
    assert seen
    assert seen[0]["kind"] == "runtime.test_event"
    assert seen[0]["payload"] == {"status": "ok"}
    assert seen[0]["chat_id"] == "chat-123"
    assert seen[0]["source"] == "unit_test"


def test_build_outbound_event_envelope_supports_domain_event() -> None:
    dispatcher = UnifiedEventDispatcher()
    event = DomainEvent(
        kind="runtime.test_event",
        payload={"count": 1},
        chat_id="chat-456",
        source="unit_test",
    )

    envelope = dispatcher.build_outbound_event_envelope(
        raw_event=event,
        chat_id=event.chat_id,
    )

    assert envelope is not None
    assert envelope["type"] == "runtime.test_event"
    assert envelope["chat_id"] == "chat-456"
    assert envelope["data"]["payload"] == {"count": 1}
    assert envelope["data"]["source"] == "unit_test"
