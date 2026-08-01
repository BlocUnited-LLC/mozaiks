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
    storage_source = _read("chat-ui/src/session/chatSessionStorage.js")
    controller_source = _read("chat-ui/src/hooks/useConversationModeController.js")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")

    assert "mozaiks.workflow_chat_id" in storage_source
    assert "getStoredWorkflowChatId" in storage_source
    assert "setStoredWorkflowChatId" in storage_source
    assert "restoreStoredArtifactForChat," in controller_source
    assert "snapshotChatId || currentChatId" in controller_source
    assert "restoreStoredArtifactForChat(targetChatId, resolvedWorkflow)" in controller_source
    assert "restoreStoredArtifactForChat," in chat_page_source
    assert "const resolveExistingWorkflowSession = async" in controller_source
    assert "buildWorkflowResolutionCandidates({" in controller_source
    assert "resolveWorkflowForChat({" in controller_source
    assert "/api/session/state?" in controller_source
    assert "pending_transition_id" in controller_source
    assert "applySessionRouterState(sessionState)" in controller_source
    assert "const candidateWorkflowChatIds = [" in controller_source
    assert "getStoredWorkflowChatId({" in controller_source
    assert "activeChatId," in controller_source
    assert "getStoredActiveChatId()," in controller_source
    assert "currentChatId," in controller_source
    assert "const workflowToResume = resolvedCandidateWorkflow || entryWorkflow" in controller_source
    assert "resumeWorkflowSession(candidateChatId, workflowToResume)" in controller_source
    assert "rememberWorkflowChat(candidateChatId, workflowToResume)" in controller_source
    assert "rememberWorkflowChat(newChatId, entryWorkflow)" in controller_source
    assert "rememberWorkflowChatSession(data.chat_id, resolvedWorkflowId)" in chat_page_source
    assert "setStoredActiveWorkflowName(resolvedWorkflowName)" in chat_page_source
    assert "setStoredActiveWorkflowName(resolvedWorkflow)" in controller_source
    assert "applySessionStatePendingTransition(data.session_state)" in chat_page_source

    router_state_index = controller_source.index("/api/session/state?")
    resume_index = controller_source.index("resumeWorkflowSession(candidateChatId, workflowToResume)")
    start_index = controller_source.index("api.startChat(")
    assert router_state_index < start_index
    assert resume_index < start_index


def test_activity_inline_progress_restores_with_artifact_panel() -> None:
    controller_source = _read("chat-ui/src/hooks/useConversationModeController.js")
    startup_source = _read("chat-ui/src/hooks/useChatStartupEffects.js")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    activity_helper_source = _read("chat-ui/src/components/chat/activityArtifacts.js")

    assert "export function upsertActivityMessage" in activity_helper_source
    assert "export function activityArtifactFromMessage" in activity_helper_source
    assert "const upsertRestoredActivityFromArtifactMessages" in chat_page_source
    assert "const restoreStoredActivityForChat" in chat_page_source
    assert "workflowMessagesCacheRef.current = applyRestoredProgress(workflowMessagesCacheRef.current)" in chat_page_source
    assert "workflowMessagesSharedRef.current = applyRestoredProgress(workflowMessagesSharedRef.current)" in chat_page_source
    assert "setWorkflowMessages((prev) => applyRestoredProgress(prev))" in chat_page_source

    assert "restoreStoredActivityForChat = null" in controller_source
    assert "upsertRestoredActivityFromArtifactMessages = null" in controller_source
    assert "upsertRestoredActivityFromArtifactMessages(" in controller_source
    assert "restoreStoredActivityForChat(currentChatId, currentWorkflowName)" in controller_source
    assert "restoreStoredActivityForChat(targetChatId, resolvedWorkflow)" in controller_source

    assert "restoreStoredActivityForChat = null" in startup_source
    assert "upsertRestoredActivityFromArtifactMessages = null" in startup_source
    assert "upsertRestoredActivityFromArtifactMessages(" in startup_source
    assert "restoreStoredActivityForChat(restoreChatId, urlWorkflowName)" in startup_source
