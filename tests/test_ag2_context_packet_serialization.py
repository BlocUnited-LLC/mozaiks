from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import mozaiksai.core.adapters.ag2_network_runner as runner
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge


@pytest.mark.asyncio
async def test_overlapping_deliveries_do_not_stack_packet_wrappers(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    sent = []
    processed = []
    projected = []
    handlers = []
    bridge = ContextVariablesBridge({})

    async def send(envelope):
        sent.append(envelope.event_data["context_updates"]["set"])
        return "sent"

    async def handle(envelope, client):
        processed.append(envelope.envelope_id)
        bridge.set("note", envelope.envelope_id)
        if envelope.envelope_id == "first":
            entered.set()
            await release.wait()
        await client.send_envelope(SimpleNamespace(
            event_type=runner.EV_PACKET, channel_id="channel", event_data={},
        ))

    async def project(agent_name, envelope):
        projected.append(agent_name)

    monkeypatch.setattr(runner, "default_handler", handle)
    client = SimpleNamespace(send_envelope=send, on_envelope=handlers.append)
    runner._install_context_update_handler(
        agent=SimpleNamespace(_mozaiks_context_bridge=bridge), client=client,
        agent_name="Worker", run_identity=("OverlapSmoke", "app", "chat"),
        agent_output_handler=project,
    )
    first = asyncio.create_task(handlers[0](SimpleNamespace(channel_id="channel", envelope_id="first")))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        second = asyncio.create_task(handlers[0](SimpleNamespace(channel_id="channel", envelope_id="second")))
        await asyncio.sleep(0)
        assert processed == ["first"]
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []))
    assert sent == [{"note": "first"}, {"note": "second"}]
    assert projected == ["Worker", "Worker"]
    assert client.send_envelope is send
    assert bridge.consume_context_updates() == {"set": {}, "delete": []}
