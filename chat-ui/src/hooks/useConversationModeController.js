import { useCallback } from 'react';

import resolveWorkflow from '../utils/resolveWorkflow';
import {
  getStoredActiveChatId,
  getStoredActiveGeneralChatId,
  getStoredActiveWorkflowName,
  setStoredActiveChatId,
} from '../session/chatSessionStorage';

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
  workflowArtifactSnapshotRef,
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
  resolveResumePolicyOrder,
  resolveWorkflowSessionByStrategy,
  describeApiError,
  modeChangeInProgressRef,
  setModeChangePending,
  resumeOldestFromWidgetRef,
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
  queryMode,
  setConnectionInitialized,
  connectionInProgressRef,
}) {
  const ensureGeneralMode = useCallback((requestedGeneralChatId = null) => {
    const preferredGeneralChatId = requestedGeneralChatId || activeGeneralChatId || getStoredActiveGeneralChatId();
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
    console.log('🧠 [MODE_TOGGLE] Switching to ask mode (sending chat.enter_general_mode)');
    const sent = sendWsMessage({
      type: 'chat.enter_general_mode',
      general_chat_id: preferredGeneralChatId || undefined,
    });

    setConversationMode('ask');
    console.log('🧹 [MODE_TOGGLE] Caching workflow messages + artifact state, closing artifact panel, restoring ask-mode messages');
    workflowMessagesCacheRef.current = messagesRef.current;
    workflowArtifactSnapshotRef.current = {
      isOpen: isSidePanelOpen,
      layoutMode: layoutMode || 'split',
      messages: isSidePanelOpen ? [...currentArtifactMessages] : [],
    };
    console.log('📸 [ARTIFACT_SNAPSHOT] Saved artifact state before switching to Ask:', workflowArtifactSnapshotRef.current);
    setIsSidePanelOpen(false);
    setCurrentArtifactMessages([]);
    if (askMessages && askMessages.length > 0) {
      console.log(`📦 [MODE_TOGGLE] Restoring ${askMessages.length} cached ask-mode messages (shared)`);
      setMessagesWithLogging(askMessages);
    } else if (generalMessagesCacheRef.current && generalMessagesCacheRef.current.length > 0) {
      console.log(`📦 [MODE_TOGGLE] Restoring ${generalMessagesCacheRef.current.length} cached ask-mode messages`);
      setMessagesWithLogging(generalMessagesCacheRef.current);
    } else {
      console.log('📭 [MODE_TOGGLE] No cached ask-mode messages, starting fresh');
      setMessagesWithLogging([]);
    }

    refreshGeneralSessions();
    return sent;
  }, [
    activeGeneralChatId,
    askMessages,
    conversationMode,
    currentArtifactMessages,
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
    workflowArtifactSnapshotRef,
    workflowMessagesCacheRef,
    workflowReplayPendingRef,
  ]);

  const startNewGeneralSession = useCallback(() => {
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
    console.log(`🤖 [MODE_TOGGLE] Switching to workflow mode (${sendSwitch ? 'sending chat.switch_workflow' : 'local shell only'})`);
    const sent = sendSwitch && currentChatId
      ? sendWsMessage({ type: 'chat.switch_workflow', chat_id: currentChatId })
      : false;

    setConversationMode('workflow');
    console.log('🧹 [MODE_TOGGLE] Caching ask-mode messages, restoring workflow messages + artifact panel state');
    generalMessagesCacheRef.current = messagesRef.current;

    const sharedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessages);
    const cachedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessagesCacheRef.current);
    if (sharedWorkflowMessages.length > 0) {
      console.log(`📦 [MODE_TOGGLE] Restoring ${sharedWorkflowMessages.length} cached workflow messages (shared)`);
      setMessagesWithLogging(sharedWorkflowMessages);
    } else if (cachedWorkflowMessages.length > 0) {
      console.log(`📦 [MODE_TOGGLE] Restoring ${cachedWorkflowMessages.length} cached workflow messages`);
      setMessagesWithLogging(cachedWorkflowMessages);
    } else {
      console.log('📭 [MODE_TOGGLE] No cached workflow messages, starting fresh');
      setMessagesWithLogging([]);
    }

    setTimeout(() => {
      const surfaceSnapshot = surfaceStateRef.current;
      if (surfaceSnapshot?.layoutMode === 'view'
        || surfaceSnapshot?.artifact?.display === 'view'
        || surfaceSnapshot?.artifact?.display === 'fullscreen') {
        console.log('🎨 [ARTIFACT_RESTORE] Skipping snapshot restore; view mode active');
        return;
      }
      const snapshot = workflowArtifactSnapshotRef.current;
      console.log('🎨 [ARTIFACT_RESTORE] Checking snapshot:', snapshot);

      if (snapshot && typeof snapshot.isOpen === 'boolean') {
        if (snapshot.isOpen) {
          console.log('🎨 [ARTIFACT_SNAPSHOT_RESTORE] Restoring artifact panel OPEN from snapshot');
          setIsSidePanelOpen(true);
          if (snapshot.layoutMode && setLayoutMode) {
            setLayoutMode(snapshot.layoutMode);
          }
          if (snapshot.messages?.length) {
            setCurrentArtifactMessages(snapshot.messages);
            console.log(`📦 [ARTIFACT_SNAPSHOT_RESTORE] Restored ${snapshot.messages.length} artifact messages`);
          }
        } else {
          console.log('🎨 [ARTIFACT_SNAPSHOT_RESTORE] Restoring artifact panel CLOSED from snapshot');
          setIsSidePanelOpen(false);
          if (setLayoutMode) setLayoutMode('full');
        }
        workflowArtifactSnapshotRef.current = { isOpen: false, messages: [], layoutMode: 'split' };
        return;
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
        console.log('🎨 [ARTIFACT_AUTO_OPEN] Detected UI artifacts in workflow messages, opening artifact panel');
        setIsSidePanelOpen(true);
        if (setLayoutMode) setLayoutMode('split');
        const artifactMessages = cachedMessages.filter((message) => (
          message.ui_mode
          || (message.tool_calls && message.tool_calls.some((toolCall) => toolCall.function?.name === 'render_ui_component'))
        ));
        if (artifactMessages.length > 0) {
          setCurrentArtifactMessages(artifactMessages);
          console.log(`📦 [ARTIFACT_AUTO_OPEN] Restored ${artifactMessages.length} artifact messages`);
        }
      } else {
        console.log('📭 [ARTIFACT_AUTO_OPEN] No UI artifacts detected, keeping panel closed');
        setIsSidePanelOpen(false);
        if (setLayoutMode) setLayoutMode('full');
      }
    }, 100);

    return sendSwitch ? sent : true;
  }, [
    conversationMode,
    currentChatId,
    generalMessagesCacheRef,
    messagesRef,
    sanitizeVisibleWorkflowMessages,
    sendWsMessage,
    setConversationMode,
    setCurrentArtifactMessages,
    setIsSidePanelOpen,
    setLayoutMode,
    setMessagesWithLogging,
    surfaceStateRef,
    workflowArtifactSnapshotRef,
    workflowMessages,
    workflowMessagesCacheRef,
  ]);

  const resumeWorkflowSession = useCallback((targetChatId, targetWorkflow = null) => {
    if (!targetChatId) {
      console.warn('🔁 [WORKFLOW_RESUME] Missing chat_id, cannot resume');
      return false;
    }

    const resolvedWorkflow = targetWorkflow || currentWorkflowName || resolveWorkflow();
    console.log('🔁 [WORKFLOW_RESUME] Attempting resume for chat:', targetChatId, 'workflow:', resolvedWorkflow);

    setCurrentChatId(targetChatId);
    setActiveChatId(targetChatId);
    setActiveWorkflowName(resolvedWorkflow);
    currentWorkflowNameRef.current = resolvedWorkflow;
    setCurrentWorkflowName(resolvedWorkflow);

    workflowReplayPendingRef.current = true;
    setMessagesWithLogging([]);

    workflowMessagesSharedRef.current = [];
    workflowMessagesCacheRef.current = [];

    const sent = sendWsMessage({
      type: 'chat.switch_workflow',
      chat_id: targetChatId,
      replay_on_switch: true,
    });
    console.log('🔁 [WORKFLOW_RESUME] chat.switch_workflow sent:', sent);

    if (sent) {
      setConversationMode('workflow');
      generalMessagesCacheRef.current = messagesRef.current;
      console.log('🔁 [WORKFLOW_RESUME] Workflow mode restored, cached general messages count:', messagesRef.current.length);
      return true;
    }

    workflowReplayPendingRef.current = false;

    return false;
  }, [
    currentWorkflowName,
    currentWorkflowNameRef,
    generalMessagesCacheRef,
    messagesRef,
    sendWsMessage,
    setActiveChatId,
    setActiveWorkflowName,
    setConversationMode,
    setCurrentChatId,
    setCurrentWorkflowName,
    setMessagesWithLogging,
    workflowMessagesCacheRef,
    workflowMessagesSharedRef,
    workflowReplayPendingRef,
  ]);

  const handleSelectWorkflowSession = useCallback((chatId, workflowName = null) => {
    if (!chatId) {
      return;
    }
    const targetWorkflow =
      resolveKnownWorkflowName(workflowName)
      || resolveKnownWorkflowName(activeWorkflowName)
      || resolveKnownWorkflowName(currentWorkflowName)
      || resolveKnownWorkflowName(configuredEntryWorkflow)
      || resolveWorkflow();

    const resumed = resumeWorkflowSession(chatId, targetWorkflow);
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
      console.log('⏳ [MODE_CHANGE] Ignoring duplicate mode toggle while a transition is already in progress');
      return;
    }

    modeChangeInProgressRef.current = true;
    setModeChangePending(true);

    console.log('🔄 [MODE_CHANGE] handleConversationModeChange called with mode:', mode);
    console.log('🔄 [MODE_CHANGE] Current conversationMode:', conversationMode);
    console.log('🔄 [MODE_CHANGE] Current activeChatId (from context):', activeChatId);
    console.log('🔄 [MODE_CHANGE] Current activeWorkflowName (from context):', activeWorkflowName);
    console.log('🔄 [MODE_CHANGE] Current currentChatId (local state):', currentChatId);

    try {
      if (mode === 'ask') {
        console.log('🧠 [MODE_CHANGE] Switching to Ask mode');
        resumeOldestFromWidgetRef.current = false;
        queryResumeHandledRef.current = null;
        consumeNavigationQueryParams(['mode', 'resume', 'chat_id']);
        ensureGeneralMode();
        if (setLayoutMode) setLayoutMode('full');
        if (isMobileView) setMobileDrawerState('peek');
      } else {
        console.log('🤖 [MODE_CHANGE] Switching to workflow mode, resolving session with resume policy');
        console.log('🤖 [MODE_CHANGE] isInWidgetMode:', isInWidgetMode);
        console.log('🤖 [MODE_CHANGE] API available?', !!api);
        console.log('🤖 [MODE_CHANGE] App ID:', currentAppId);
        console.log('🤖 [MODE_CHANGE] User ID:', currentUserId);

        ensureWorkflowMode({ sendSwitch: false, forceRestore: true });

        if (!workflowConfigLoaded) {
          console.log('🤖 [MODE_CHANGE] Workflow registry not ready yet, waiting for fetch');
          await workflowConfig.fetchWorkflowConfigs();
          setWorkflowConfigLoaded(true);
        }

        const canonicalWorkflowName =
          resolveKnownWorkflowName(urlWorkflowName)
          || resolveKnownWorkflowName(configuredEntryWorkflow)
          || resolveKnownWorkflowName(currentWorkflowName)
          || resolveKnownWorkflowName(activeWorkflowName)
          || resolveKnownWorkflowName(getStoredActiveWorkflowName())
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
          console.log('🧭 [MODE_CHANGE] Leaving view mode -> restoring workflow surface');
          const restored = restoreViewSnapshot();
          if (!restored) {
            clearViewArtifacts();
          }
        }

        if (!api || typeof api.get !== 'function' || !currentAppId || !currentUserId) {
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
          console.log('🚀 [MODE_CHANGE] In widget mode - navigating to /chat');
          navigate('/chat');
          setIsInWidgetMode(false);
        }

        const startEntryWorkflowSession = async () => {
          console.log('📭 [MODE_CHANGE] No resumable workflow session — starting entry_point workflow');
          const entryWorkflow =
            canonicalWorkflowName
            || resolveKnownWorkflowName(configuredEntryWorkflow)
            || resolveWorkflow(currentWorkflowName)
            || resolveWorkflow(urlWorkflowName)
            || workflowConfig.getDefaultWorkflow();
          if (!entryWorkflow) {
            console.warn('⚠️ [MODE_CHANGE] No entry_point workflow available to start');
            return false;
          }

          try {
            const askCarrierMode = queryMode === 'ask' || conversationMode === 'ask';
            const result = await api.startChat(
              currentAppId,
              entryWorkflow,
              currentUserId,
              {},
              null,
              null,
              askCarrierMode ? { transportPurpose: 'ask_carrier' } : null,
            );
            if (result && (result.chat_id || result.id)) {
              const newChatId = result.chat_id || result.id;
              console.log(`🚀 [MODE_CHANGE] Created new session for ${entryWorkflow}: ${newChatId}`);
              setCurrentChatId(newChatId);
              setActiveChatId(newChatId);
              setActiveWorkflowName(entryWorkflow);
              setCurrentWorkflowName(entryWorkflow);
              setStoredActiveChatId(newChatId);

              setConnectionInitialized(false);
              connectionInProgressRef.current = false;

              setConversationMode('workflow');
              generalMessagesCacheRef.current = messagesRef.current;
              refreshWorkflowSessions();
              console.log('✅ [MODE_CHANGE] Entry_point workflow session started, WS will connect on re-render');
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
          const policyOrder = resolveResumePolicyOrder();
          console.log('🧭 [MODE_CHANGE] Resume policy order:', policyOrder.join(' -> '));

          let resumed = false;
          for (const strategy of policyOrder) {
            if (strategy === 'entry_point') {
              continue;
            }
            const target = await resolveWorkflowSessionByStrategy(strategy);
            if (!target?.chat_id) {
              continue;
            }
            const targetWorkflowName = resolveKnownWorkflowName(target.workflow_name)
              || canonicalWorkflowName
              || resolveWorkflow(currentWorkflowName)
              || resolveWorkflow(urlWorkflowName);
            console.log(`🎯 [MODE_CHANGE] Resuming workflow via ${strategy}: ${targetWorkflowName} (${target.chat_id})`);
            resumed = resumeWorkflowSession(target.chat_id, targetWorkflowName);
            if (resumed) {
              generalMessagesCacheRef.current = messagesRef.current;
              refreshWorkflowSessions();
              break;
            }
            console.warn(`⚠️ [MODE_CHANGE] Resume send failed for strategy ${strategy}; trying next option`);
          }

          if (!resumed) {
            await startEntryWorkflowSession();
          }
        } catch (err) {
          console.error('❌ [MODE_CHANGE] Error resolving workflow session:', describeApiError(err));
          console.log('🔄 [MODE_CHANGE] Staying in local workflow shell while backend is unavailable');
        }
      }

      console.log('✅ [MODE_CHANGE] handleConversationModeChange completed');
    } finally {
      modeChangeInProgressRef.current = false;
      setModeChangePending(false);
    }
  }, [
    activeChatId,
    activeWorkflowName,
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
    queryMode,
    queryResumeHandledRef,
    refreshWorkflowSessions,
    resolveKnownWorkflowName,
    resolveResumePolicyOrder,
    resolveWorkflowSessionByStrategy,
    restoreViewSnapshot,
    resumeOldestFromWidgetRef,
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