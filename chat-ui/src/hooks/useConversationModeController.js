import { useCallback } from 'react';

import resolveWorkflow from '../utils/resolveWorkflow';
import {
  getStoredActiveChatId,
  getStoredActiveGeneralChatId,
  getStoredActiveWorkflowName,
  readStoredArtifactWorkspaceSnapshot,
  readStoredWorkflowTranscriptSnapshot,
  getStoredWorkflowChatId,
  logChatPersistence,
  summarizeArtifactWorkspaceSnapshot,
  summarizeWorkflowTranscriptSnapshot,
  summarizeWorkflowMessages,
  setStoredActiveChatId,
  setStoredActiveWorkflowName,
  writeStoredArtifactWorkspaceSnapshot,
  setStoredWorkflowChatId,
} from '../session/chatSessionStorage';
import {
  buildWorkflowResolutionCandidates,
  resolveWorkflowForChat,
} from '../session/workflowChatResolution';

const scoreWorkflowTranscriptMessages = (messageList) => {
  const summary = summarizeWorkflowMessages(messageList);
  return (
    summary.uiMessageCount * 100
    + summary.toolProgressCount * 50
    + summary.total
  );
};

const selectBestWorkflowTranscriptMessages = (candidates) => {
  const ranked = candidates
    .filter((candidate) => Array.isArray(candidate.messages) && candidate.messages.length > 0)
    .map((candidate) => ({
      ...candidate,
      score: scoreWorkflowTranscriptMessages(candidate.messages),
    }))
    .sort((left, right) => right.score - left.score);
  return ranked[0] || null;
};

const filterArtifactPanelMessages = (messages) => {
  if (!Array.isArray(messages)) return [];
  return messages.filter((msg) => {
    if (!msg || typeof msg !== 'object') return false;
    const meta = msg.metadata || {};
    if (msg.isStreaming) return false;
    return true;
  });
};

export function useConversationModeController({
  activeGeneralChatId,
  conversationMode,
  refreshGeneralSessions,
  sendWsMessage,
  setConversationMode,
  setMessagesWithLogging,
  currentArtifactMessages,
  isSidePanelOpen,
  layoutMode,
  askMessages,
  workflowReplayPendingRef,
  workflowMessagesCacheRef,
  artifactWorkspaceSnapshotRef,
  generalMessagesCacheRef,
  messagesRef,
  setIsSidePanelOpen,
  setCurrentArtifactMessages,
  currentChatId,
  setCurrentChatId,
  activeChatId,
  sanitizeVisibleWorkflowMessages,
  workflowMessages,
  surfaceStateRef,
  setLayoutMode,
  currentWorkflowName,
  setActiveChatId,
  setActiveWorkflowName,
  currentWorkflowNameRef,
  setCurrentWorkflowName,
  rememberWorkflowChatSession = null,
  workflowMessagesSharedRef,
  activeWorkflowName,
  configuredEntryWorkflow,
  resolveKnownWorkflowName,
  generalHydrationPendingRef,
  setActiveGeneralChatId,
  generalChatSessions,
  setGeneralChatSummary,
  hydrateGeneralTranscript,
  api,
  currentAppId,
  currentUserId,
  refreshWorkflowSessions,
  describeApiError,
  modeChangeInProgressRef,
  setModeChangePending,
  queryResumeHandledRef,
  consumeNavigationQueryParams,
  isMobileView,
  setMobileDrawerState,
  isInWidgetMode,
  navigate,
  setIsInWidgetMode,
  workflowConfigLoaded,
  setWorkflowConfigLoaded,
  workflowConfig,
  urlWorkflowName,
  restoreViewSnapshot,
  clearViewArtifacts,
  restoreStoredArtifactForChat,
  hydrateServerArtifactForChat = null,
  artifactCacheValidRef = null,
  artifactRestoredOnceRef = null,
  queryGeneralChatId = null,
  setConnectionInitialized,
  connectionInProgressRef,
  applySessionRouterState = null,
  workflowConnectReplayPolicyRef = null,
}) {
  const rememberWorkflowChat = useCallback((chatId, workflowName = null) => {
    const resolvedWorkflow =
      workflowName
      || currentWorkflowName
      || activeWorkflowName
      || getStoredActiveWorkflowName();
    if (!chatId || !resolvedWorkflow) {
      return false;
    }
    setStoredActiveChatId(chatId);
    setStoredActiveWorkflowName(resolvedWorkflow);
    if (typeof rememberWorkflowChatSession === 'function') {
      return rememberWorkflowChatSession(chatId, resolvedWorkflow);
    }
    if (!currentAppId || !currentUserId) {
      return false;
    }
    return setStoredWorkflowChatId({
      appId: currentAppId,
      userId: currentUserId,
      workflowName: resolvedWorkflow,
      chatId,
    });
  }, [
    activeWorkflowName,
    currentAppId,
    currentUserId,
    currentWorkflowName,
    rememberWorkflowChatSession,
  ]);

  const ensureGeneralMode = useCallback((requestedGeneralChatId = null) => {
    const preferredGeneralChatId = requestedGeneralChatId || activeGeneralChatId || getStoredActiveGeneralChatId();
    logChatPersistence('ensure_general_mode_requested', {
      requestedGeneralChatId: requestedGeneralChatId || null,
      preferredGeneralChatId: preferredGeneralChatId || null,
      currentChatId: currentChatId || null,
      currentWorkflowName: currentWorkflowName || null,
      activeWorkflowName: activeWorkflowName || null,
      isSidePanelOpen,
      layoutMode: layoutMode || 'split',
      currentArtifactMessages: summarizeWorkflowMessages(currentArtifactMessages),
      workflowMessagesCache: summarizeWorkflowMessages(workflowMessagesCacheRef.current),
      snapshot: summarizeArtifactWorkspaceSnapshot(artifactWorkspaceSnapshotRef.current),
    });
    if (workflowConnectReplayPolicyRef && typeof workflowConnectReplayPolicyRef === 'object') {
      workflowConnectReplayPolicyRef.current = null;
    }
    if (conversationMode === 'ask') {
      if (requestedGeneralChatId) {
        sendWsMessage({
          type: 'chat.enter_general_mode',
          general_chat_id: preferredGeneralChatId || undefined,
        });
      }
      return true;
    }
    workflowReplayPendingRef.current = false;
    const sent = sendWsMessage({
      type: 'chat.enter_general_mode',
      general_chat_id: preferredGeneralChatId || undefined,
    });

    setConversationMode('ask');
    workflowMessagesCacheRef.current = messagesRef.current;
    artifactWorkspaceSnapshotRef.current = {
      chatId: currentChatId || null,
      workflowName: currentWorkflowName || activeWorkflowName || null,
      isOpen: isSidePanelOpen,
      layoutMode: layoutMode || 'split',
      messages: filterArtifactPanelMessages(currentArtifactMessages),
    };
    if (currentChatId) {
      writeStoredArtifactWorkspaceSnapshot(currentChatId, artifactWorkspaceSnapshotRef.current);
    }
    logChatPersistence('workflow_artifact_snapshot_preserved_for_ask', {
      currentChatId: currentChatId || null,
      currentWorkflowName: currentWorkflowName || null,
      currentArtifactMessages: summarizeWorkflowMessages(currentArtifactMessages),
      snapshot: summarizeArtifactWorkspaceSnapshot(artifactWorkspaceSnapshotRef.current),
    });
    setIsSidePanelOpen(false);
    if (askMessages && askMessages.length > 0) {
      setMessagesWithLogging(askMessages);
    } else if (generalMessagesCacheRef.current && generalMessagesCacheRef.current.length > 0) {
      setMessagesWithLogging(generalMessagesCacheRef.current);
    } else {
      setMessagesWithLogging([]);
    }

    refreshGeneralSessions();
    return sent;
  }, [
    activeGeneralChatId,
    activeWorkflowName,
    askMessages,
    conversationMode,
    currentArtifactMessages,
    currentChatId,
    currentWorkflowName,
    generalMessagesCacheRef,
    isSidePanelOpen,
    layoutMode,
    messagesRef,
    refreshGeneralSessions,
    sendWsMessage,
    setConversationMode,
    setCurrentArtifactMessages,
    setIsSidePanelOpen,
    setMessagesWithLogging,
    artifactWorkspaceSnapshotRef,
    workflowMessagesCacheRef,
    workflowReplayPendingRef,
    workflowConnectReplayPolicyRef,
  ]);

  const startNewGeneralSession = useCallback(() => {
    if (workflowConnectReplayPolicyRef && typeof workflowConnectReplayPolicyRef === 'object') {
      workflowConnectReplayPolicyRef.current = null;
    }
    const sent = sendWsMessage({ type: 'chat.start_general_chat' });
    setConversationMode('ask');
    refreshGeneralSessions();
    generalMessagesCacheRef.current = [];
    setMessagesWithLogging([]);
    return sent;
  }, [
    generalMessagesCacheRef,
    refreshGeneralSessions,
    sendWsMessage,
    setConversationMode,
    setMessagesWithLogging,
    workflowConnectReplayPolicyRef,
  ]);

  const handleSelectGeneralChat = useCallback((chatId) => {
    if (!chatId || generalHydrationPendingRef.current) {
      return;
    }
    ensureGeneralMode(chatId);
    generalHydrationPendingRef.current = true;
    setActiveGeneralChatId(chatId);
    const session = (generalChatSessions || []).find((item) => item?.chat_id === chatId);
    if (session) {
      setGeneralChatSummary({
        chatId,
        label: session.label || session.chat_id,
        lastUpdatedAt: session.last_updated_at || session.updated_at,
        lastSequence: session.last_sequence ?? session.sequence,
      });
    }
    setMessagesWithLogging([]);
    Promise.resolve(hydrateGeneralTranscript(chatId)).finally(() => {
      generalHydrationPendingRef.current = false;
    });
  }, [
    ensureGeneralMode,
    generalChatSessions,
    generalHydrationPendingRef,
    hydrateGeneralTranscript,
    setActiveGeneralChatId,
    setGeneralChatSummary,
    setMessagesWithLogging,
  ]);

  const ensureWorkflowMode = useCallback((options = {}) => {
    const { sendSwitch = true, forceRestore = false } = options;
    if (conversationMode === 'workflow' && !forceRestore) {
      return true;
    }
    if (!currentChatId) {
      console.warn('⚠️ Cannot resume workflow mode without chat id, switching mode anyway');
    }
    const sent = sendSwitch && currentChatId
      ? sendWsMessage({ type: 'chat.switch_workflow', chat_id: currentChatId })
      : false;

    setConversationMode('workflow');
    generalMessagesCacheRef.current = messagesRef.current;

    const sharedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessages);
    const cachedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessagesCacheRef.current);
    const storedWorkflowTranscript = currentChatId ? readStoredWorkflowTranscriptSnapshot(currentChatId) : null;
    const storedWorkflowTranscriptMatches = Boolean(
      storedWorkflowTranscript
      && (
        !storedWorkflowTranscript.workflowName
        || !currentWorkflowName
        || storedWorkflowTranscript.workflowName === currentWorkflowName
      )
    );
    const storedWorkflowMessages = storedWorkflowTranscriptMatches
      ? sanitizeVisibleWorkflowMessages(storedWorkflowTranscript.messages)
      : [];
    const selectedWorkflowTranscript = selectBestWorkflowTranscriptMessages([
      { source: 'shared', messages: sharedWorkflowMessages },
      { source: 'cached', messages: cachedWorkflowMessages },
      { source: 'stored', messages: storedWorkflowMessages },
    ]);
    const selectedWorkflowMessages = selectedWorkflowTranscript?.messages || [];
    logChatPersistence('ensure_workflow_mode_requested', {
      currentChatId: currentChatId || null,
      currentWorkflowName: currentWorkflowName || null,
      activeWorkflowName: activeWorkflowName || null,
      sendSwitch,
      forceRestore,
      sharedWorkflowMessages: summarizeWorkflowMessages(sharedWorkflowMessages),
      cachedWorkflowMessages: summarizeWorkflowMessages(cachedWorkflowMessages),
      storedWorkflowTranscript: summarizeWorkflowTranscriptSnapshot(storedWorkflowTranscript),
      storedWorkflowTranscriptMatches,
      storedWorkflowMessages: summarizeWorkflowMessages(storedWorkflowMessages),
      selectedWorkflowTranscriptSource: selectedWorkflowTranscript?.source || null,
      selectedWorkflowMessages: summarizeWorkflowMessages(selectedWorkflowMessages),
      localSnapshot: summarizeArtifactWorkspaceSnapshot(artifactWorkspaceSnapshotRef.current),
      storedSnapshot: summarizeArtifactWorkspaceSnapshot(
        currentChatId ? readStoredArtifactWorkspaceSnapshot(currentChatId) : null,
      ),
      surfaceState: {
        layoutMode: surfaceStateRef.current?.layoutMode || null,
        artifactDisplay: surfaceStateRef.current?.artifact?.display || null,
      },
    });
    if (selectedWorkflowMessages.length > 0) {
      workflowMessagesCacheRef.current = selectedWorkflowMessages;
      workflowMessagesSharedRef.current = selectedWorkflowMessages;
      setMessagesWithLogging(selectedWorkflowMessages);
    } else {
      setMessagesWithLogging([]);
    }

    setTimeout(() => {
      const surfaceSnapshot = surfaceStateRef.current;
      if (surfaceSnapshot?.layoutMode === 'view'
        || surfaceSnapshot?.artifact?.display === 'view'
        || surfaceSnapshot?.artifact?.display === 'fullscreen') {
        return;
      }
      const localSnapshot = artifactWorkspaceSnapshotRef.current;
      const storedSnapshot = currentChatId ? readStoredArtifactWorkspaceSnapshot(currentChatId) : null;
      const hasLocalSnapshot = Boolean(
        localSnapshot
        && (
          localSnapshot.chatId
          || localSnapshot.workflowName
          || localSnapshot.isOpen
          || (Array.isArray(localSnapshot.messages) && localSnapshot.messages.length > 0)
        ),
      );
      const snapshotCandidates = [hasLocalSnapshot ? localSnapshot : null, storedSnapshot].filter(Boolean);
      const snapshot = snapshotCandidates.find((candidate) => {
        const snapshotChatId = candidate.chatId || null;
        const snapshotWorkflowName = candidate.workflowName || null;
        const snapshotMatchesChat = !snapshotChatId || !currentChatId || snapshotChatId === currentChatId;
        const snapshotMatchesWorkflow = !snapshotWorkflowName || !currentWorkflowName || snapshotWorkflowName === currentWorkflowName;
        return snapshotMatchesChat && snapshotMatchesWorkflow;
      }) || null;

      if (snapshot && typeof snapshot.isOpen === 'boolean') {
        const snapshotChatId = snapshot.chatId || null;
        const snapshotWorkflowName = snapshot.workflowName || null;
        const snapshotMatchesChat = !snapshotChatId || !currentChatId || snapshotChatId === currentChatId;
        const snapshotMatchesWorkflow = !snapshotWorkflowName || !currentWorkflowName || snapshotWorkflowName === currentWorkflowName;
        if (!snapshotMatchesChat || !snapshotMatchesWorkflow) {
          logChatPersistence('workflow_snapshot_rejected', {
            reason: 'chat_or_workflow_mismatch',
            snapshot: summarizeArtifactWorkspaceSnapshot(snapshot),
            currentChatId: currentChatId || null,
            currentWorkflowName: currentWorkflowName || null,
          });
          artifactWorkspaceSnapshotRef.current = { isOpen: false, messages: [], layoutMode: 'split' };
        } else {
          const snapshotMessages = Array.isArray(snapshot.messages) ? snapshot.messages : [];
          logChatPersistence('workflow_snapshot_selected', {
            currentChatId: currentChatId || null,
            currentWorkflowName: currentWorkflowName || null,
            snapshot: summarizeArtifactWorkspaceSnapshot(snapshot),
            selectedSource: snapshotMessages.length > 0 ? 'snapshot_messages' : 'stored_artifact_or_closed_panel',
          });
          if (snapshotMessages.length > 0) {
            setCurrentArtifactMessages(snapshotMessages);
            if (artifactCacheValidRef) {
              artifactCacheValidRef.current = true;
            }
            if (artifactRestoredOnceRef) {
              artifactRestoredOnceRef.current = true;
            }
          } else if (snapshot.isOpen && typeof restoreStoredArtifactForChat === 'function') {
            const restored = restoreStoredArtifactForChat(
              snapshotChatId || currentChatId,
              snapshotWorkflowName || currentWorkflowName,
            );
            logChatPersistence('workflow_snapshot_fallback_restore', {
              currentChatId: currentChatId || null,
              currentWorkflowName: currentWorkflowName || null,
              restored,
            });
            if (!restored) {
              setCurrentArtifactMessages([]);
            }
          }

          if (snapshot.isOpen) {
            setIsSidePanelOpen(true);
            if (snapshot.layoutMode && setLayoutMode) {
              setLayoutMode(snapshot.layoutMode);
            }
          } else {
            setIsSidePanelOpen(false);
            if (setLayoutMode) setLayoutMode('full');
          }
          artifactWorkspaceSnapshotRef.current = { isOpen: false, messages: [], layoutMode: 'split' };
          return;
        }
      }

      const cachedMessages = workflowMessagesCacheRef.current || [];
      const hasArtifacts = cachedMessages.some((message) => {
        if (message.ui_mode) return true;
        if (message.tool_calls && Array.isArray(message.tool_calls)) {
          return message.tool_calls.some((toolCall) => toolCall.function?.name === 'render_ui_component');
        }
        return false;
      });

      if (hasArtifacts) {
        setIsSidePanelOpen(true);
        if (setLayoutMode) setLayoutMode('split');
        const artifactMessages = cachedMessages.filter((message) => (
          message.ui_mode
          || (message.tool_calls && message.tool_calls.some((toolCall) => toolCall.function?.name === 'render_ui_component'))
        ));
        if (artifactMessages.length > 0) {
          setCurrentArtifactMessages(artifactMessages);
        }
      } else if (typeof restoreStoredArtifactForChat === 'function'
        && restoreStoredArtifactForChat(currentChatId, currentWorkflowName)) {
        setIsSidePanelOpen(true);
        if (setLayoutMode) setLayoutMode('split');
      } else {
        if (typeof hydrateServerArtifactForChat === 'function') {
          Promise.resolve(hydrateServerArtifactForChat({
            chatId: currentChatId,
            workflowName: currentWorkflowName,
            reason: 'ensure_workflow_mode_artifact_restore_miss',
            force: true,
          }));
        }
        setCurrentArtifactMessages([]);
        setIsSidePanelOpen(false);
        if (setLayoutMode) setLayoutMode('full');
      }
    }, 100);

    return sendSwitch ? sent : true;
  }, [
    conversationMode,
    currentChatId,
    currentWorkflowName,
    generalMessagesCacheRef,
    messagesRef,
    sanitizeVisibleWorkflowMessages,
    sendWsMessage,
    setConversationMode,
    setCurrentArtifactMessages,
    setIsSidePanelOpen,
    setLayoutMode,
    setMessagesWithLogging,
    restoreStoredArtifactForChat,
    hydrateServerArtifactForChat,
    surfaceStateRef,
    artifactWorkspaceSnapshotRef,
    workflowMessages,
    workflowMessagesCacheRef,
    workflowMessagesSharedRef,
  ]);

  const resumeWorkflowSession = useCallback((targetChatId, targetWorkflow = null, options = {}) => {
    if (!targetChatId) {
      console.warn('🔁 [WORKFLOW_RESUME] Missing chat_id, cannot resume');
      return false;
    }

    const resolvedWorkflow = targetWorkflow || currentWorkflowName || resolveWorkflow();
    const replayOnSwitch = options?.replayOnSwitch !== false;
    const suppressConnectReplay = !replayOnSwitch;

    setCurrentChatId(targetChatId);
    setActiveChatId(targetChatId);
    setActiveWorkflowName(resolvedWorkflow);
    currentWorkflowNameRef.current = resolvedWorkflow;
    setCurrentWorkflowName(resolvedWorkflow);
    rememberWorkflowChat(targetChatId, resolvedWorkflow);

    if (workflowConnectReplayPolicyRef && typeof workflowConnectReplayPolicyRef === 'object') {
      workflowConnectReplayPolicyRef.current = suppressConnectReplay
        ? {
            suppressHistoryReplay: true,
            reason: 'workflow_mode_toggle',
          }
        : null;
    }

    logChatPersistence('workflow_resume_requested', {
      chatId: targetChatId,
      workflowName: resolvedWorkflow,
      replayOnSwitch,
      suppressConnectReplay,
    });

    const sharedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessagesSharedRef.current);
    const cachedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessagesCacheRef.current);
    const storedWorkflowTranscript = readStoredWorkflowTranscriptSnapshot(targetChatId);
    const storedWorkflowTranscriptMatches = Boolean(
      storedWorkflowTranscript
      && (
        !storedWorkflowTranscript.workflowName
        || !resolvedWorkflow
        || storedWorkflowTranscript.workflowName === resolvedWorkflow
      )
    );
    const storedWorkflowMessages = storedWorkflowTranscriptMatches
      ? sanitizeVisibleWorkflowMessages(storedWorkflowTranscript.messages)
      : [];
    const selectedWorkflowTranscript = selectBestWorkflowTranscriptMessages([
      { source: 'shared', messages: sharedWorkflowMessages },
      { source: 'cached', messages: cachedWorkflowMessages },
      { source: 'stored', messages: storedWorkflowMessages },
    ]);
    const selectedWorkflowMessages = selectedWorkflowTranscript?.messages || [];

    const sent = sendWsMessage({
      type: 'chat.switch_workflow',
      chat_id: targetChatId,
      replay_on_switch: replayOnSwitch,
    });

    if (sent) {
      setConversationMode('workflow');
      generalMessagesCacheRef.current = messagesRef.current;
      if (suppressConnectReplay) {
        const restoredWorkflowMessages = [...selectedWorkflowMessages];
        workflowReplayPendingRef.current = false;
        workflowMessagesSharedRef.current = restoredWorkflowMessages;
        workflowMessagesCacheRef.current = restoredWorkflowMessages;
        setMessagesWithLogging(restoredWorkflowMessages);
        logChatPersistence('workflow_resume_restored_from_cache', {
          chatId: targetChatId,
          workflowName: resolvedWorkflow,
          sharedWorkflowMessages: summarizeWorkflowMessages(sharedWorkflowMessages),
          cachedWorkflowMessages: summarizeWorkflowMessages(cachedWorkflowMessages),
          storedWorkflowMessages: summarizeWorkflowMessages(storedWorkflowMessages),
          selectedWorkflowMessages: summarizeWorkflowMessages(restoredWorkflowMessages),
        });
      } else {
        workflowReplayPendingRef.current = true;
        setMessagesWithLogging([]);
        workflowMessagesSharedRef.current = [];
        workflowMessagesCacheRef.current = [];
      }
      if (typeof restoreStoredArtifactForChat === 'function') {
        setTimeout(() => {
          const restored = restoreStoredArtifactForChat(targetChatId, resolvedWorkflow);
          if (!restored && typeof hydrateServerArtifactForChat === 'function') {
            Promise.resolve(hydrateServerArtifactForChat({
              chatId: targetChatId,
              workflowName: resolvedWorkflow,
              reason: 'resume_workflow_session_artifact_restore_miss',
              force: true,
            }));
          }
        }, 100);
      }
      return true;
    }

    workflowReplayPendingRef.current = false;

    return false;
  }, [
    currentWorkflowName,
    currentWorkflowNameRef,
    generalMessagesCacheRef,
    messagesRef,
    sanitizeVisibleWorkflowMessages,
    sendWsMessage,
    setActiveChatId,
    setActiveWorkflowName,
    setConversationMode,
    setCurrentChatId,
    setCurrentWorkflowName,
    setMessagesWithLogging,
    rememberWorkflowChat,
    selectBestWorkflowTranscriptMessages,
    restoreStoredArtifactForChat,
    readStoredWorkflowTranscriptSnapshot,
    hydrateServerArtifactForChat,
    workflowMessagesCacheRef,
    workflowMessagesSharedRef,
    workflowReplayPendingRef,
    workflowConnectReplayPolicyRef,
  ]);

  const handleSelectWorkflowSession = useCallback((chatId, workflowName = null) => {
    if (!chatId) {
      return;
    }
    const targetWorkflow = workflowName
      ? (resolveKnownWorkflowName(workflowName) || workflowName)
      : (
          resolveKnownWorkflowName(activeWorkflowName)
          || resolveKnownWorkflowName(currentWorkflowName)
          || resolveKnownWorkflowName(configuredEntryWorkflow)
          || resolveWorkflow()
        );

    const resumed = resumeWorkflowSession(chatId, targetWorkflow, { replayOnSwitch: true });
    if (resumed) {
      refreshWorkflowSessions();
    }
  }, [
    activeWorkflowName,
    configuredEntryWorkflow,
    currentWorkflowName,
    refreshWorkflowSessions,
    resolveKnownWorkflowName,
    resumeWorkflowSession,
  ]);

  const handleConversationModeChange = useCallback(async (mode) => {
    if (modeChangeInProgressRef.current) {
      return;
    }

    modeChangeInProgressRef.current = true;
    setModeChangePending(true);
    logChatPersistence('handle_conversation_mode_change', {
      requestedMode: mode,
      currentConversationMode: conversationMode,
      currentChatId: currentChatId || null,
      currentWorkflowName: currentWorkflowName || null,
      activeChatId: activeChatId || null,
      activeWorkflowName: activeWorkflowName || null,
      layoutMode: layoutMode || 'split',
      currentMessages: summarizeWorkflowMessages(messagesRef.current),
      workflowCache: summarizeWorkflowMessages(workflowMessagesCacheRef.current),
    });


    try {
      if (mode === 'ask') {
        queryResumeHandledRef.current = null;
        consumeNavigationQueryParams(['mode', 'resume', 'chat_id', 'force_ask', 'general_chat_id', 'generalChatId']);
        ensureGeneralMode(queryGeneralChatId);
        if (queryGeneralChatId) {
          setActiveGeneralChatId(queryGeneralChatId);
          generalHydrationPendingRef.current = true;
          Promise.resolve(hydrateGeneralTranscript(queryGeneralChatId)).finally(() => {
            generalHydrationPendingRef.current = false;
          });
        }
        if (setLayoutMode) setLayoutMode('full');
        if (isMobileView) setMobileDrawerState('peek');
      } else {
        if (!workflowConfigLoaded) {
          await workflowConfig.fetchWorkflowConfigs();
          setWorkflowConfigLoaded(true);
        }

        const canonicalWorkflowName =
          workflowConfig.resolveKnownWorkflowName(currentWorkflowName)
          || workflowConfig.resolveKnownWorkflowName(activeWorkflowName)
          || workflowConfig.resolveKnownWorkflowName(getStoredActiveWorkflowName())
          || workflowConfig.resolveKnownWorkflowName(urlWorkflowName)
          || workflowConfig.resolveKnownWorkflowName(configuredEntryWorkflow)
          || resolveKnownWorkflowName(currentWorkflowName)
          || resolveKnownWorkflowName(activeWorkflowName)
          || resolveKnownWorkflowName(getStoredActiveWorkflowName())
          || resolveKnownWorkflowName(urlWorkflowName)
          || resolveKnownWorkflowName(configuredEntryWorkflow)
          || resolveWorkflow(currentWorkflowName)
          || resolveWorkflow(urlWorkflowName)
          || workflowConfig.getDefaultWorkflow();

        if (canonicalWorkflowName && canonicalWorkflowName !== currentWorkflowName) {
          setCurrentWorkflowName(canonicalWorkflowName);
        }
        if (canonicalWorkflowName && canonicalWorkflowName !== activeWorkflowName) {
          setActiveWorkflowName(canonicalWorkflowName);
        }

        if (layoutMode === 'view') {
          const restored = restoreViewSnapshot();
          if (!restored) {
            clearViewArtifacts();
          }
        }

        if (!api || typeof api.get !== 'function' || !currentAppId || !currentUserId) {
          ensureWorkflowMode({ sendSwitch: false, forceRestore: true });
          const storedChatId = activeChatId || currentChatId || getStoredActiveChatId();
          const storedWorkflowName = canonicalWorkflowName || resolveKnownWorkflowName(getStoredActiveWorkflowName());

          if (storedChatId) {
            setCurrentChatId(storedChatId);
            setActiveChatId(storedChatId);
          }
          if (storedWorkflowName) {
            setActiveWorkflowName(storedWorkflowName);
            setCurrentWorkflowName(storedWorkflowName);
          }

          return;
        }

        if (isInWidgetMode) {
          navigate('/chat');
          setIsInWidgetMode(false);
        }

        const resolveExistingWorkflowSession = async (candidateChatId, workflowNames, includeAvailable = false) => {
          const workflowCandidates = buildWorkflowResolutionCandidates({
            workflowConfig,
            candidates: workflowNames,
            includeAvailable,
          });
          const resolution = await resolveWorkflowForChat({
            api,
            appId: currentAppId,
            chatId: candidateChatId,
            workflowCandidates,
          });
          if (resolution.validationIncomplete) {
            if (resolution.error) {
              console.warn('⚠️ [MODE_CHANGE] Workflow chat validation failed; attempting resume:', resolution.error);
            } else {
              console.warn('⚠️ [MODE_CHANGE] Workflow chat validation incomplete; attempting resume.');
            }
            return {
              exists: null,
              workflowName: workflowCandidates[0] || null,
            };
          }
          return {
            exists: Boolean(resolution.workflowName),
            workflowName: resolution.workflowName,
          };
        };

        const readSessionRouterState = async () => {
          try {
            const params = new URLSearchParams();
            params.set('app_id', String(currentAppId));
            params.set('user_id', String(currentUserId));
            const response = await api.get(`/api/session/state?${params.toString()}`);
            return response?.session_state && typeof response.session_state === 'object'
              ? response.session_state
              : null;
          } catch (err) {
            console.warn('⚠️ [MODE_CHANGE] Session router state unavailable; falling back to stored chat:', err);
            return null;
          }
        };

        const entryWorkflow =
          canonicalWorkflowName
          || resolveKnownWorkflowName(configuredEntryWorkflow)
          || resolveWorkflow(currentWorkflowName)
          || resolveWorkflow(urlWorkflowName)
          || workflowConfig.getDefaultWorkflow();

        const sessionState = await readSessionRouterState();
        if (sessionState && typeof applySessionRouterState === 'function') {
          const pendingTransitionId = String(sessionState.pending_transition_id || '').trim();
          const routerChatId = String(sessionState.current_chat_id || '').trim();
          const rawRouterWorkflow = String(
            sessionState.current_workflow_id
            || sessionState.last_requested_workflow_id
            || ''
          ).trim();
          const routerWorkflowName = rawRouterWorkflow
            ? (resolveKnownWorkflowName(rawRouterWorkflow) || rawRouterWorkflow)
            : null;

          if (pendingTransitionId) {
            ensureWorkflowMode({ sendSwitch: false, forceRestore: true });
            applySessionRouterState(sessionState);
            setConversationMode('workflow');
            return;
          }

          if (routerChatId && routerWorkflowName) {
            const { exists, workflowName: resolvedRouterWorkflow } = await resolveExistingWorkflowSession(
              routerChatId,
              [routerWorkflowName, canonicalWorkflowName],
              true,
            );
            if (exists !== false) {
              const workflowToResume = resolvedRouterWorkflow || routerWorkflowName;
              applySessionRouterState({
                ...sessionState,
                current_workflow_id: workflowToResume,
              });
              const resumed = resumeWorkflowSession(routerChatId, workflowToResume, { replayOnSwitch: false });
              if (resumed) {
                refreshWorkflowSessions();
                return;
              }
              // Chat exists but WS send failed (e.g. dead connection during mode toggle).
              // Restore mode/messages from cache and let the WS reconnect naturally — do
              // NOT fall through to startEntryWorkflowSession which would create a new
              // chat and re-run the workflow from scratch.
              ensureWorkflowMode({ sendSwitch: false, forceRestore: true });
              refreshWorkflowSessions();
              return;
            }
          }
        }

        // Track whether we found any valid existing session during candidate resolution.
        // If so, do not start a new workflow even when the WS send failed.
        let foundExistingSessionChatId = null;
        let foundExistingSessionWorkflow = null;

        if (entryWorkflow) {
          const candidateWorkflowChatIds = [
            getStoredWorkflowChatId({
              appId: currentAppId,
              userId: currentUserId,
              workflowName: entryWorkflow,
            }),
            activeChatId,
            getStoredActiveChatId(),
            currentChatId,
          ].filter((candidate, index, all) => candidate && all.indexOf(candidate) === index);

          for (const candidateChatId of candidateWorkflowChatIds) {
            const { exists, workflowName: resolvedCandidateWorkflow } = await resolveExistingWorkflowSession(
              candidateChatId,
              [
                entryWorkflow,
                currentWorkflowName,
                activeWorkflowName,
                getStoredActiveWorkflowName(),
                urlWorkflowName,
              ],
              true,
            );
            if (exists === false) {
              if (getStoredActiveChatId() === candidateChatId) {
                setStoredActiveChatId(null);
              }
              if (activeChatId === candidateChatId) {
                setActiveChatId(null);
              }
              continue;
            }

            const workflowToResume = resolvedCandidateWorkflow || entryWorkflow;
            const resumed = resumeWorkflowSession(candidateChatId, workflowToResume, { replayOnSwitch: false });
            if (resumed) {
              rememberWorkflowChat(candidateChatId, workflowToResume);
              refreshWorkflowSessions();
              return;
            }

            // Chat exists but WS send failed — record the first valid candidate so we
            // can restore from it below without starting a new workflow.
            if (!foundExistingSessionChatId) {
              foundExistingSessionChatId = candidateChatId;
              foundExistingSessionWorkflow = workflowToResume;
            }
          }
        }

        // If we found a valid existing session but the WS was unavailable when we
        // tried to switch, restore the workflow UI from cache and return.  The WS
        // reconnect cycle (triggered by the connection retry nonce) will re-establish
        // the backend context with suppress_history_replay so messages are not
        // replayed on top of what is already cached.
        if (foundExistingSessionChatId) {
          rememberWorkflowChat(foundExistingSessionChatId, foundExistingSessionWorkflow);
          ensureWorkflowMode({ sendSwitch: false, forceRestore: true });
          refreshWorkflowSessions();
          return;
        }

        const startEntryWorkflowSession = async () => {
          if (!entryWorkflow) {
            console.warn('⚠️ [MODE_CHANGE] No entry_point workflow available to start');
            return false;
          }

          try {
            ensureWorkflowMode({ sendSwitch: false, forceRestore: true });
            const result = await api.startChat(
              currentAppId,
              entryWorkflow,
              currentUserId,
              {},
              null,
              null,
              null,
            );
            if (result && (result.chat_id || result.id)) {
              const newChatId = result.chat_id || result.id;
              setCurrentChatId(newChatId);
              setActiveChatId(newChatId);
              setActiveWorkflowName(entryWorkflow);
              setCurrentWorkflowName(entryWorkflow);
              rememberWorkflowChat(newChatId, entryWorkflow);

              setConnectionInitialized(false);
              connectionInProgressRef.current = false;

              setConversationMode('workflow');
              generalMessagesCacheRef.current = messagesRef.current;
              refreshWorkflowSessions();
              return true;
            }

            console.error('❌ [MODE_CHANGE] startChat returned no chat_id:', result);
            return false;
          } catch (startErr) {
            console.error('❌ [MODE_CHANGE] Failed to start entry_point workflow session:', describeApiError(startErr));
            return false;
          }
        };

        try {
          await startEntryWorkflowSession();
        } catch (err) {
          console.error('❌ [MODE_CHANGE] Error resolving workflow session:', describeApiError(err));
        }
      }

    } finally {
      modeChangeInProgressRef.current = false;
      setModeChangePending(false);
    }
  }, [
    activeChatId,
    activeWorkflowName,
    applySessionRouterState,
    api,
    clearViewArtifacts,
    configuredEntryWorkflow,
    consumeNavigationQueryParams,
    connectionInProgressRef,
    conversationMode,
    currentAppId,
    currentChatId,
    currentUserId,
    currentWorkflowName,
    describeApiError,
    ensureGeneralMode,
    ensureWorkflowMode,
    generalMessagesCacheRef,
    isInWidgetMode,
    isMobileView,
    layoutMode,
    messagesRef,
    modeChangeInProgressRef,
    navigate,
    queryResumeHandledRef,
    rememberWorkflowChat,
    refreshWorkflowSessions,
    resolveKnownWorkflowName,
    restoreViewSnapshot,
    resumeWorkflowSession,
    setActiveChatId,
    setActiveWorkflowName,
    setConnectionInitialized,
    setConversationMode,
    setCurrentChatId,
    setCurrentWorkflowName,
    setIsInWidgetMode,
    setLayoutMode,
    setMobileDrawerState,
    setModeChangePending,
    urlWorkflowName,
    workflowConfig,
    workflowConfigLoaded,
    setWorkflowConfigLoaded,
    workflowConnectReplayPolicyRef,
  ]);

  return {
    ensureGeneralMode,
    startNewGeneralSession,
    handleSelectGeneralChat,
    ensureWorkflowMode,
    resumeWorkflowSession,
    handleSelectWorkflowSession,
    handleConversationModeChange,
  };
}
