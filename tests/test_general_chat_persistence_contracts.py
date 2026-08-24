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


def test_ask_live_stream_keeps_general_agent_provenance() -> None:
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    transport_source = _read("mozaiksai/core/transport/simple_transport.py")

    assert "metadata=_chunk_metadata" in transport_source
    assert "const streamChunkMetadata = data.metadata || data.data?.metadata || null;" in chat_page_source
    assert "metadata: streamChunkMetadata || m.metadata || null" in chat_page_source
    assert "metadata: streamChunkMetadata" in chat_page_source


def test_workflow_mode_switch_resumes_before_starting_new_workflow_run() -> None:
    storage_source = _read("chat-ui/src/session/chatSessionStorage.js")
    controller_source = _read("chat-ui/src/hooks/useConversationModeController.js")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")

    assert "mozaiks.workflow_chat_id" in storage_source
    assert "mozaiks.artifact_workspace_snapshot" in storage_source
    assert "mozaiks.workflow_transcript_snapshot" in storage_source
    assert "readStoredArtifactWorkspaceSnapshot" in storage_source
    assert "writeStoredArtifactWorkspaceSnapshot" in storage_source
    assert "readStoredWorkflowTranscriptSnapshot" in storage_source
    assert "writeStoredWorkflowTranscriptSnapshot" in storage_source
    assert "getStoredWorkflowChatId" in storage_source
    assert "setStoredWorkflowChatId" in storage_source
    assert "writeStoredArtifactWorkspaceSnapshot(currentChatId, artifactWorkspaceSnapshotRef.current)" in controller_source
    assert "messages: filterArtifactPanelMessages(currentArtifactMessages)" in controller_source
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
    assert "resumeWorkflowSession(candidateChatId, workflowToResume, { replayOnSwitch: false })" in controller_source
    assert "rememberWorkflowChat(candidateChatId, workflowToResume)" in controller_source
    assert "rememberWorkflowChat(newChatId, entryWorkflow)" in controller_source
    assert "rememberWorkflowChatSession(data.chat_id, resolvedWorkflowId)" in chat_page_source
    assert "setStoredActiveWorkflowName(resolvedWorkflowName)" in chat_page_source
    assert "setStoredActiveWorkflowName(resolvedWorkflow)" in controller_source
    assert "applySessionStatePendingTransition(data.session_state)" in chat_page_source

    router_state_index = controller_source.index("/api/session/state?")
    resume_index = controller_source.index("resumeWorkflowSession(candidateChatId, workflowToResume, { replayOnSwitch: false })")
    start_index = controller_source.index("api.startChat(")
    assert router_state_index < start_index
    assert resume_index < start_index


def test_inline_ui_progress_restores_from_workflow_transcript_not_artifact_state() -> None:
    controller_source = _read("chat-ui/src/hooks/useConversationModeController.js")
    startup_source = _read("chat-ui/src/hooks/useChatStartupEffects.js")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    inline_helper_source = _read("chat-ui/src/components/chat/inlineSurfaces.js")

    assert not (_workspace() / "chat-ui/src/components/chat/activityArtifacts.js").exists()
    assert "export function buildInlineToolCallMessageFromEvent" in inline_helper_source
    assert "export function upsertInlineToolCallMessage" in inline_helper_source
    assert "display !== INLINE_DISPLAY" in inline_helper_source
    assert "metadata: {" in inline_helper_source
    assert "interaction_type: interactionType" in inline_helper_source
    assert "awaiting_response: awaitingResponse" in inline_helper_source
    assert "const cacheWorkflowTranscriptMessage" in chat_page_source
    assert "cacheWorkflowTranscriptMessage(inlineMessage" in chat_page_source
    assert "upsertInlineToolCallMessage(" in chat_page_source
    assert "persistWorkflowTranscriptSnapshot(nextCache" in chat_page_source
    assert "mergeWorkflowTranscriptMessages(" in chat_page_source
    assert "selectedWorkflowTranscriptSource" in controller_source
    assert "writeStoredWorkflowTranscriptSnapshot(targetChatId" in chat_page_source
    assert "cacheWorkflowTranscriptMessage({" in chat_page_source
    assert "conversationModeRef.current === 'ask' && streamEndMetadata?.source !== 'general_agent'" in chat_page_source
    assert "conversationMode === 'ask' && metadataSource?.source !== 'general_agent'" in chat_page_source

    assert "readStoredWorkflowTranscriptSnapshot" in controller_source
    assert "selectedWorkflowMessages.length > 0" in controller_source
    assert "restoreStoredActivityForChat" not in controller_source
    assert "upsertRestoredActivityFromArtifactMessages" not in controller_source

    assert "readStoredWorkflowTranscriptSnapshot" in startup_source
    assert "storedWorkflowMessages.length > 0 && messagesRef.current.length === 0" in startup_source
    assert "restoreStoredActivityForChat" not in startup_source
    assert "upsertRestoredActivityFromArtifactMessages" not in startup_source
    assert "case 'activity':" not in chat_page_source


def test_workflow_artifact_restore_uses_backend_metadata_and_durable_surfaces() -> None:
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    startup_source = _read("chat-ui/src/hooks/useChatStartupEffects.js")
    controller_source = _read("chat-ui/src/hooks/useConversationModeController.js")
    surface_reducer_source = _read("chat-ui/src/state/uiSurfaceReducer.js")

    assert "const hydrateServerArtifactForChat = useCallback(async" in chat_page_source
    assert "/api/chats/meta/" in chat_page_source
    assert "server_artifact_hydrate_requested" in chat_page_source
    assert "cacheServerLastArtifact(meta.last_artifact" in chat_page_source
    assert "chatMetaHydrationMissedAtRef" in chat_page_source
    assert "artifactAutoClearRef.current = false" in chat_page_source
    assert "currentArtifactMessages.length === 0" in chat_page_source
    assert "hadStoredArtifact = Boolean(" in chat_page_source
    assert "storedPanelOpen === false && hadStoredArtifact" in chat_page_source

    select_speaker_start = chat_page_source.index("case 'select_speaker':")
    select_speaker_end = chat_page_source.index("case 'tool_progress':", select_speaker_start)
    select_speaker_block = chat_page_source[select_speaker_start:select_speaker_end]
    assert "clearStoredArtifactState(currentChatId)" not in select_speaker_block
    assert "setCurrentArtifactMessages([])" not in select_speaker_block

    assert "hydrateServerArtifactForChat = null" in startup_source
    assert "reason: 'startup_workflow_artifact_restore_miss'" in startup_source
    assert "reason: 'workflow_mode_empty_artifact'" in startup_source
    assert "hydrateServerArtifactForChat = null" in controller_source
    assert "reason: 'ensure_workflow_mode_artifact_restore_miss'" in controller_source
    assert "reason: 'resume_workflow_session_artifact_restore_miss'" in controller_source
    assert "eventType === 'tool_call' || eventType === 'chat.tool_call' || eventType === 'ui.render'" in surface_reducer_source


def test_dynamic_ui_artifact_subscriber_derives_logged_fields_before_rendering() -> None:
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")

    subscriber_start = chat_page_source.index("// tool_call (InputRequestEvent path) and ui.render")
    render_start = chat_page_source.index("if (shouldRenderArtifact)", subscriber_start)
    subscriber_setup = chat_page_source[subscriber_start:render_start]
    render_block = chat_page_source[
        render_start:chat_page_source.index("// Don't inject artifact UIs into the chat feed", render_start)
    ]

    assert "const toolName =" in subscriber_setup
    assert "const toolCallId =" in subscriber_setup
    assert "const componentType =" in subscriber_setup
    assert "const payloadKeys = Object.keys(payload || {});" in subscriber_setup
    assert "toolName," in render_block
    assert "toolCallId," in render_block
    assert "componentType," in render_block
    assert "payloadKeys," in render_block
    assert "component_type: componentType" in render_block
    assert "setCurrentArtifactMessages([artifactMsg]);" in render_block


def test_workflow_replay_merge_prefers_server_identity_over_client_ids() -> None:
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")

    merge_start = chat_page_source.index("const mergeWorkflowTranscriptMessages = useCallback")
    merge_block = chat_page_source[
        merge_start:chat_page_source.index("const persistWorkflowTranscriptSnapshot", merge_start)
    ]

    replay_index_pos = merge_block.index("if (replayIndex !== null)")
    server_sequence_pos = merge_block.index("if (serverSequence !== null)")
    generated_id_pos = merge_block.index("if (message?.id && !isClientGeneratedMessageId(message.id))")

    assert "const stableNumber = (value) => {" in merge_block
    assert "const isClientGeneratedMessageId = (id) => {" in merge_block
    assert "return /^(text|activity|tool-call|tool-call-msg|tool-call-artifact|ui-restored|nav-cache)-/.test(value);" in merge_block
    assert replay_index_pos < generated_id_pos
    assert server_sequence_pos < generated_id_pos


def test_dynamic_ui_artifact_persistence_uses_resolved_tool_identity() -> None:
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")

    subscriber_start = chat_page_source.index("// tool_call (InputRequestEvent path) and ui.render")
    render_start = chat_page_source.index("if (shouldRenderArtifact)", subscriber_start)
    render_block = chat_page_source[
        render_start:chat_page_source.index("// Don't inject artifact UIs into the chat feed", render_start)
    ]

    assert "artifact_id: deriveArtifactId(payload, toolCallId || toolName || null)" in render_block
    assert "id: `tool-call-artifact-${toolCallId || artifactPayload.artifact_id || Date.now()}`" in render_block
    assert "lastArtifactEventRef.current = toolCallId || toolName || 'artifact';" in render_block
    assert "tool_name: toolName" in render_block
    assert "tool_call_id: toolCallId || null" in render_block
    assert "component_type: componentType || toolName || null" in render_block
