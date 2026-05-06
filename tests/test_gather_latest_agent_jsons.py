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


def test_extract_json_from_wrapped_text_event_content() -> None:
    wrapped = (
        "uuid=UUID('evt-1') "
        "content='{\\n"
        "  \"PatternSelection\": {\\n"
        "    \"is_multi_workflow\": false,\\n"
        "    \"pack_name\": \"Internal Helpdesk Ticket Resolution\"\\n"
        "  }\\n"
        "}' "
        "sender='PatternAgent' models_usage=None"
    )

    normalized = AG2PersistenceManager._normalize_wrapped_text_content(wrapped)
    parsed = AG2PersistenceManager._extract_json_from_text(wrapped, agent_name="PatternAgent")

    assert normalized == (
        '{"PatternSelection": {"is_multi_workflow": false, '
        '"pack_name": "Internal Helpdesk Ticket Resolution"}}'
    )
    assert parsed == {
        "PatternSelection": {
            "is_multi_workflow": False,
            "pack_name": "Internal Helpdesk Ticket Resolution",
        }
    }
