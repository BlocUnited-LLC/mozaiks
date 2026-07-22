from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.hosts.routers import sessions as sessions_module


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _principal(user_id: str = "user-1") -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id,
        email=None,
        name=None,
        roles=[],
        scopes=["access_as_user"],
        raw_claims={},
        provider="none",
    )


@pytest.mark.asyncio
async def test_general_chat_list_endpoint_reads_persistence_manager(monkeypatch) -> None:
    created_at = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    expected_sessions = [
        {
            "chat_id": "generalchat-app-1-user-1-0001",
            "label": "General Chat #1",
            "sequence": 1,
            "status": 0,
            "created_at": created_at,
            "last_updated_at": created_at,
            "last_sequence": 3,
        }
    ]

    class DummyPersistenceManager:
        async def list_general_chats(self, *, app_id=None, user_id, limit=50):
            assert app_id == "app-1"
            assert user_id == "user-1"
            assert limit == 25
            return expected_sessions

    monkeypatch.setattr(sessions_module, "persistence_manager", DummyPersistenceManager())

    payload = await sessions_module.list_general_chats_fallback(
        "app-1",
        "user-1",
        limit=25,
        principal=_principal(),
    )

    assert payload["count"] == 1
    assert payload["source"] == "persistence"
    assert payload["sessions"][0]["chat_id"] == "generalchat-app-1-user-1-0001"
    assert payload["sessions"][0]["created_at"] == created_at.isoformat()


@pytest.mark.asyncio
async def test_general_chat_transcript_endpoint_enforces_user_scope(monkeypatch) -> None:
    class DummyPersistenceManager:
        async def fetch_general_chat_transcript(self, *, general_chat_id, app_id=None, after_sequence=-1, limit=200):
            assert general_chat_id == "generalchat-app-1-other-0001"
            assert app_id == "app-1"
            return {
                "chat_id": general_chat_id,
                "label": "General Chat #1",
                "sequence": 1,
                "status": 0,
                "app_id": app_id,
                "user_id": "other-user",
                "messages": [],
                "last_sequence": 0,
                "created_at": None,
                "last_updated_at": None,
            }

    monkeypatch.setattr(sessions_module, "persistence_manager", DummyPersistenceManager())

    with pytest.raises(HTTPException) as exc_info:
        await sessions_module.general_chat_transcript_fallback(
            "app-1",
            "generalchat-app-1-other-0001",
            principal=_principal(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_general_chat_endpoint_scopes_to_user(monkeypatch) -> None:
    class DummyPersistenceManager:
        async def delete_general_chat(self, *, general_chat_id, app_id=None, user_id):
            assert general_chat_id == "generalchat-app-1-user-1-0001"
            assert app_id == "app-1"
            assert user_id == "user-1"
            return True

    monkeypatch.setattr(sessions_module, "persistence_manager", DummyPersistenceManager())

    payload = await sessions_module.delete_general_chat(
        "app-1",
        "user-1",
        "generalchat-app-1-user-1-0001",
        principal=_principal(),
    )

    assert payload["success"] is True
    assert payload["deleted"] is True
    assert payload["general_chat_id"] == "generalchat-app-1-user-1-0001"


def test_ask_chat_restore_contracts_are_pinned_in_source() -> None:
    storage_source = _read("chat-ui/src/session/chatSessionStorage.js")
    context_source = _read("chat-ui/src/context/ChatUIContext.jsx")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    general_mode_source = _read("mozaiksai/core/transport/general_mode.py")
    input_handler_source = _read("mozaiksai/core/transport/handlers/input_handlers.py")
    mode_handler_source = _read("mozaiksai/core/transport/handlers/mode_handlers.py")

    assert "mozaiks.active_general_chat_id" in storage_source
    assert "getStoredActiveGeneralChatId" in storage_source
    assert "setStoredActiveGeneralChatId" in storage_source
    assert "useState(() => getStoredActiveGeneralChatId())" in context_source
    assert "setStoredActiveGeneralChatId(activeGeneralChatId)" in context_source
    assert "ensureGeneralMode()" in chat_page_source
    assert "general_chat_id: activeGeneralChatId || undefined" in chat_page_source
    assert "requested_general_chat_id" in general_mode_source
    assert 'ui_context_payload.get("general_chat_id")' in input_handler_source
    assert 'ui_context_payload["requested_general_chat_id"] = requested_general_chat_id' in input_handler_source
    assert 'data.get("general_chat_id")' in mode_handler_source


def test_ask_bootstrap_sessions_do_not_count_as_workflow_runs() -> None:
    sessions_source = _read("mozaiksai/hosts/routers/sessions.py")
    chat_router_source = _read("mozaiksai/hosts/routers/chat.py")
    runtime_source = _read("mozaiksai/hosts/runtime.py")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    api_source = _read("chat-ui/src/adapters/api.js")

    assert 'transport_purpose = str(data.get("transport_purpose") or "").strip().lower()' in runtime_source
    assert 'extra_fields["transport_purpose"] = "ask_carrier"' in runtime_source
    assert 'and not _is_ask_carrier_session(session)' in sessions_source
    assert 'if doc and _is_ask_carrier_session(doc):' in chat_router_source
    assert "body.transport_purpose = sessionOptions.transportPurpose.trim();" in api_source
    assert "askCarrierMode ? { transportPurpose: 'ask_carrier' } : {}" in chat_page_source


def test_workflow_mode_switch_resumes_before_starting_new_workflow_run() -> None:
    controller_source = _read("chat-ui/src/hooks/useConversationModeController.js")

    assert "const validateExistingWorkflowSession = async" in controller_source
    assert "/api/chats/exists/" in controller_source
    assert "const candidateWorkflowChatIds = [" in controller_source
    assert "activeChatId," in controller_source
    assert "getStoredActiveChatId()," in controller_source
    assert "currentChatId," in controller_source
    assert "resumeWorkflowSession(candidateChatId, entryWorkflow)" in controller_source

    resume_index = controller_source.index("resumeWorkflowSession(candidateChatId, entryWorkflow)")
    start_index = controller_source.index("api.startChat(")
    assert resume_index < start_index

