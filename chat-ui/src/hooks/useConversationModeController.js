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
  queryGeneralChatId = null,
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
    const sent = sendWsMessage({
      type: 'chat.enter_general_mode',
      general_chat_id: preferredGeneralChatId || undefined,
    });

    setConversationMode('ask');
    workflowMessagesCacheRef.current = messagesRef.current;
    workflowArtifactSnapshotRef.current = {
      isOpen: isSidePanelOpen,
      layoutMode: layoutMode || 'split',
      messages: isSidePanelOpen ? [...currentArtifactMessages] : [],
    };
    setIsSidePanelOpen(false);
    setCurrentArtifactMessages([]);
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
    const sent = sendSwitch && currentChatId
      ? sendWsMessage({ type: 'chat.switch_workflow', chat_id: currentChatId })
      : false;

    setConversationMode('workflow');
    generalMessagesCacheRef.current = messagesRef.current;

    const sharedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessages);
    const cachedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessagesCacheRef.current);
    if (sharedWorkflowMessages.length > 0) {
      setMessagesWithLogging(sharedWorkflowMessages);
    } else if (cachedWorkflowMessages.length > 0) {
      setMessagesWithLogging(cachedWorkflowMessages);
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
      const snapshot = workflowArtifactSnapshotRef.current;

      if (snapshot && typeof snapshot.isOpen === 'boolean') {
        if (snapshot.isOpen) {
          setIsSidePanelOpen(true);
          if (snapshot.layoutMode && setLayoutMode) {
            setLayoutMode(snapshot.layoutMode);
          }
          if (snapshot.messages?.length) {
            setCurrentArtifactMessages(snapshot.messages);
          }
        } else {
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
        setIsSidePanelOpen(true);
        if (setLayoutMode) setLayoutMode('split');
        const artifactMessages = cachedMessages.filter((message) => (
          message.ui_mode
          || (message.tool_calls && message.tool_calls.some((toolCall) => toolCall.function?.name === 'render_ui_component'))
        ));
        if (artifactMessages.length > 0) {
          setCurrentArtifactMessages(artifactMessages);
        }
      } else {
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

    if (sent) {
      setConversationMode('workflow');
      generalMessagesCacheRef.current = messagesRef.current;
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
      return;
    }

    modeChangeInProgressRef.current = true;
    setModeChangePending(true);


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

        ensureWorkflowMode({ sendSwitch: false, forceRestore: true });

        if (!workflowConfigLoaded) {
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
          navigate('/chat');
          setIsInWidgetMode(false);
        }

        const startEntryWorkflowSession = async () => {
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
              setStoredActiveChatId(newChatId);

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
