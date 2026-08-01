import { useCallback } from 'react';

import resolveWorkflow from '../utils/resolveWorkflow';
import {
  getStoredActiveChatId,
  getStoredActiveGeneralChatId,
  getStoredActiveWorkflowName,
  getStoredWorkflowChatId,
  setStoredActiveChatId,
  setStoredActiveWorkflowName,
  setStoredWorkflowChatId,
} from '../session/chatSessionStorage';
import {
  buildWorkflowResolutionCandidates,
  resolveWorkflowForChat,
} from '../session/workflowChatResolution';

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
  restoreStoredActivityForChat = null,
  upsertRestoredActivityFromArtifactMessages = null,
  queryGeneralChatId = null,
  setConnectionInitialized,
  connectionInProgressRef,
  applySessionRouterState = null,
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
      chatId: currentChatId || null,
      workflowName: currentWorkflowName || activeWorkflowName || null,
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
        const snapshotChatId = snapshot.chatId || null;
        const snapshotWorkflowName = snapshot.workflowName || null;
        const snapshotMatchesChat = !snapshotChatId || !currentChatId || snapshotChatId === currentChatId;
        const snapshotMatchesWorkflow = !snapshotWorkflowName || !currentWorkflowName || snapshotWorkflowName === currentWorkflowName;
        if (!snapshotMatchesChat || !snapshotMatchesWorkflow) {
          workflowArtifactSnapshotRef.current = { isOpen: false, messages: [], layoutMode: 'split' };
        } else {
          if (snapshot.isOpen) {
            setIsSidePanelOpen(true);
            if (snapshot.layoutMode && setLayoutMode) {
              setLayoutMode(snapshot.layoutMode);
            }
            if (snapshot.messages?.length) {
              setCurrentArtifactMessages(snapshot.messages);
              if (typeof upsertRestoredActivityFromArtifactMessages === 'function') {
                upsertRestoredActivityFromArtifactMessages(
                  snapshot.messages,
                  snapshotWorkflowName || currentWorkflowName,
                );
              }
            } else if (typeof restoreStoredArtifactForChat === 'function') {
              const restored = restoreStoredArtifactForChat(
                snapshotChatId || currentChatId,
                snapshotWorkflowName || currentWorkflowName,
              );
              if (restored && typeof restoreStoredActivityForChat === 'function') {
                restoreStoredActivityForChat(
                  snapshotChatId || currentChatId,
                  snapshotWorkflowName || currentWorkflowName,
                );
              }
              if (!restored) {
                setIsSidePanelOpen(false);
                if (setLayoutMode) setLayoutMode('full');
              }
            }
          } else {
            setIsSidePanelOpen(false);
            if (setLayoutMode) setLayoutMode('full');
          }
          workflowArtifactSnapshotRef.current = { isOpen: false, messages: [], layoutMode: 'split' };
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
          if (typeof upsertRestoredActivityFromArtifactMessages === 'function') {
            upsertRestoredActivityFromArtifactMessages(
              artifactMessages,
              currentWorkflowName,
            );
          }
        }
      } else if (typeof restoreStoredArtifactForChat === 'function'
        && restoreStoredArtifactForChat(currentChatId, currentWorkflowName)) {
        if (typeof restoreStoredActivityForChat === 'function') {
          restoreStoredActivityForChat(currentChatId, currentWorkflowName);
        }
        setIsSidePanelOpen(true);
        if (setLayoutMode) setLayoutMode('split');
      } else {
        // Still restore inline activity (e.g. AppIntelligenceProgressCard) even when no artifact exists yet
        if (typeof restoreStoredActivityForChat === 'function') {
          restoreStoredActivityForChat(currentChatId, currentWorkflowName);
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
    restoreStoredActivityForChat,
    surfaceStateRef,
    upsertRestoredActivityFromArtifactMessages,
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
    rememberWorkflowChat(targetChatId, resolvedWorkflow);

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
      if (typeof restoreStoredArtifactForChat === 'function') {
        setTimeout(() => {
          const restored = restoreStoredArtifactForChat(targetChatId, resolvedWorkflow);
          if (restored && typeof restoreStoredActivityForChat === 'function') {
            restoreStoredActivityForChat(targetChatId, resolvedWorkflow);
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
    sendWsMessage,
    setActiveChatId,
    setActiveWorkflowName,
    setConversationMode,
    setCurrentChatId,
    setCurrentWorkflowName,
    setMessagesWithLogging,
    rememberWorkflowChat,
    restoreStoredActivityForChat,
    restoreStoredArtifactForChat,
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
              const resumed = resumeWorkflowSession(routerChatId, workflowToResume);
              if (resumed) {
                refreshWorkflowSessions();
                return;
              }
            }
          }
        }

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
            const resumed = resumeWorkflowSession(candidateChatId, workflowToResume);
            if (resumed) {
              rememberWorkflowChat(candidateChatId, workflowToResume);
              refreshWorkflowSessions();
              return;
            }
          }
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
