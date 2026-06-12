from __future__ import annotations

import logging
from importlib import import_module
from unittest.mock import AsyncMock

import pytest

_persist_mod = import_module("mozaiksai.core.data.persistence.persistence_manager")
AG2PersistenceManager = _persist_mod.AG2PersistenceManager


@pytest.mark.asyncio
async def test_gather_latest_agent_jsons_ignores_user_messages(caplog) -> None:
    manager = AG2PersistenceManager.__new__(AG2PersistenceManager)
    manager.load_run_history = AsyncMock(return_value=[
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "agent_name": "PresenterAgent",
            "structured_output": {"summary": "done"},
        },
    ])

    with caplog.at_level(logging.WARNING):
        result = await manager.gather_latest_agent_jsons(chat_id="chat-1", app_id="app-1")

    assert result == {"PresenterAgent": {"summary": "done"}}
    assert "No JSON found in user message" not in caplog.text


@pytest.mark.asyncio
async def test_gather_latest_agent_jsons_falls_back_when_latest_message_is_malformed(caplog) -> None:
    manager = AG2PersistenceManager.__new__(AG2PersistenceManager)
    manager.load_run_history = AsyncMock(
        return_value=[
            {
                "role": "assistant",
                "agent_name": "AgentsAgent",
                "structured_output": {
                    "agents": [
                        {
                            "name": "PlannerAgent",
                            "prompt_sections": [],
                        }
                    ]
                },
            },
            {
                "role": "assistant",
                "agent_name": "AgentsAgent",
                "content": '{"agents": [',
            },
        ]
    )

    with caplog.at_level(logging.DEBUG):
        result = await manager.gather_latest_agent_jsons(chat_id="chat-1", app_id="app-1")

    assert result == {
        "AgentsAgent": {
            "agents": [
                {
                    "name": "PlannerAgent",
                    "prompt_sections": [],
                }
            ]
        }
    }
    assert "Extracted JSON from AgentsAgent (via structured_output field)" in caplog.text


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

