from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.import_utils import import_module_directly

_persist_mod = import_module_directly("mozaiksai.core.data.persistence.persistence_manager")
AG2PersistenceManager = _persist_mod.AG2PersistenceManager


@pytest.mark.asyncio
async def test_gather_latest_agent_jsons_ignores_user_messages(caplog) -> None:
    manager = AG2PersistenceManager.__new__(AG2PersistenceManager)
    manager.resume_chat = AsyncMock(return_value=[
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "agent_name": "PresenterAgent",
            "structured_output": {"summary": "done"},
        },
    ])
    manager._coll = AsyncMock(return_value=MagicMock())

    with caplog.at_level(logging.WARNING):
        result = await manager.gather_latest_agent_jsons(chat_id="chat-1", app_id="app-1")

    assert result == {"PresenterAgent": {"summary": "done"}}
    assert "No JSON found in user message" not in caplog.text