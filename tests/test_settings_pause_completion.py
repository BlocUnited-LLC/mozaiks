import asyncio
from types import SimpleNamespace

import pytest

from mozaiksai.core.transport.workflow_bridge import WorkflowBridgeMixin


@pytest.mark.asyncio
async def test_confirmed_pause_waits_for_background_cleanup(monkeypatch):
    from mozaiksai.core.events import unified_event_dispatcher

    monkeypatch.setattr(unified_event_dispatcher, "get_event_dispatcher", lambda: None)
    started = asyncio.Event()
    cleaned = asyncio.Event()
    async def execution():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()
    task = asyncio.create_task(execution())
    await started.wait()
    transport = SimpleNamespace(_background_tasks={"owned-chat": task}, connections={})
    assert await WorkflowBridgeMixin.pause_background_workflow(transport, chat_id="owned-chat", wait_for_completion=True)
    assert task.done() and cleaned.is_set()
    assert not await WorkflowBridgeMixin.pause_background_workflow(transport, chat_id="owned-chat", wait_for_completion=True)
    assert not await WorkflowBridgeMixin.pause_background_workflow(transport, chat_id="missing", wait_for_completion=True)
