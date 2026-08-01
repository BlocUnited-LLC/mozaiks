import { useEffect } from 'react';

import {
  clearStoredArtifactState,
  clearStoredChatCacheSeed,
  getStoredActiveChatId,
  getStoredActiveGeneralChatId,
  clearStoredWorkflowChatId,
  setStoredActiveChatId,
  getStoredActiveWorkflowName,
} from '../session/chatSessionStorage';
import {
  buildWorkflowResolutionCandidates,
  resolveWorkflowForChat,
} from '../session/workflowChatResolution';

export function useChatStartupEffects({
  api,
  currentAppId,
  currentUserId,
  refreshGeneralSessions,
  refreshWorkflowSessions,
  conversationBootstrapRef,
  queryMode,
  queryGeneralChatId = null,
  navigationLoading,
  configuredStartupMode,
  setConversationMode,
  consumeNavigationQueryParams,
  askMessages,
  generalMessagesCacheRef,
  setMessagesWithLogging,
  setIsSidePanelOpen,
  setCurrentArtifactMessages,
  setLayoutMode,
  queryChatId,
  workflowArtifactSnapshotRef,
  currentChatId,
  activeChatId,
  restoreStoredArtifactForChat,
  restoreStoredActivityForChat = null,
  upsertRestoredActivityFromArtifactMessages = null,
  urlWorkflowName,
  conversationMode,
  connectionStatus,
  askModeSyncedChatRef,
  wsRef,
  isSidePanelOpen,
  layoutMode,
  activeGeneralChatId,
  setActiveGeneralChatId,
  generalHydrationPendingRef,
  hydrateGeneralTranscript,
  workflowMessagesCacheRef,
  sanitizeVisibleWorkflowMessages,
  workflowMessages,
  messagesRef,
  workflowReplayPendingRef,
  isPrimaryChatRoute,
  isInWidgetMode,
  currentArtifactMessages,
  surfaceState,
  layoutModeForConversation,
  widgetOverlayOpen,
  setWidgetOverlayOpen,
  queryResumeHandledRef,
  currentWorkflowName,
  resumeWorkflowSession,
  resolveKnownWorkflowName,
  setIsInWidgetMode,
  setActiveChatId,
  rememberWorkflowChatSession = null,
  workflowConfig,
}) {
  useEffect(() => {
    if (!api || !currentAppId || !currentUserId) {
      return;
    }
    refreshGeneralSessions();
  }, [api, currentAppId, currentUserId, refreshGeneralSessions]);

  useEffect(() => {
    if (!api || !currentAppId || !currentUserId) {
      return;
    }
    if (conversationMode !== 'workflow') {
      return;
    }
    refreshWorkflowSessions();
  }, [api, currentAppId, currentUserId, conversationMode, refreshWorkflowSessions]);

  useEffect(() => {
    if (conversationBootstrapRef.current) {
      return;
    }
    if (!queryMode && navigationLoading) {
      return;
    }
    conversationBootstrapRef.current = true;

    if (queryMode === 'ask') {
      setConversationMode('ask');
      consumeNavigationQueryParams(['mode', 'resume', 'chat_id', 'force_ask', 'general_chat_id', 'generalChatId']);
      if (queryGeneralChatId) {
        setActiveGeneralChatId?.(queryGeneralChatId);
        generalHydrationPendingRef.current = true;
        Promise.resolve(hydrateGeneralTranscript(queryGeneralChatId)).finally(() => {
          generalHydrationPendingRef.current = false;
        });
      } else if (askMessages && askMessages.length > 0) {
        setMessagesWithLogging(askMessages);
      } else if (generalMessagesCacheRef.current && generalMessagesCacheRef.current.length > 0) {
        setMessagesWithLogging(generalMessagesCacheRef.current);
      }
      setTimeout(() => {
        setIsSidePanelOpen(false);
        setCurrentArtifactMessages([]);
        if (setLayoutMode) setLayoutMode('full');
      }, 50);
      return;
    }

    if (queryMode === 'workflow') {
      setConversationMode('workflow');
      consumeNavigationQueryParams(['mode', 'resume']);

      const snapshot = workflowArtifactSnapshotRef.current;
      const restoreChatId = queryChatId || currentChatId || activeChatId || getStoredActiveChatId();
      const snapshotMatchesChat = !snapshot?.chatId || !restoreChatId || snapshot.chatId === restoreChatId;
      const snapshotMatchesWorkflow = !snapshot?.workflowName || !currentWorkflowName || snapshot.workflowName === currentWorkflowName;
      if (snapshot?.isOpen && snapshot?.messages?.length > 0 && snapshotMatchesChat && snapshotMatchesWorkflow) {
        setTimeout(() => {
          setIsSidePanelOpen(true);
          setCurrentArtifactMessages(snapshot.messages);
          if (typeof upsertRestoredActivityFromArtifactMessages === 'function') {
            upsertRestoredActivityFromArtifactMessages(
              snapshot.messages,
              snapshot.workflowName || currentWorkflowName || urlWorkflowName,
            );
          }
          if (snapshot.layoutMode && setLayoutMode) {
            setLayoutMode(snapshot.layoutMode);
          }
        }, 100);
      } else {
        if (snapshot?.messages?.length > 0 && (!snapshotMatchesChat || !snapshotMatchesWorkflow)) {
          workflowArtifactSnapshotRef.current = { isOpen: false, messages: [], layoutMode: 'split' };
        }
        if (restoreChatId) {
          const restored = restoreStoredArtifactForChat(restoreChatId, urlWorkflowName);
          if (restored) {
            if (typeof restoreStoredActivityForChat === 'function') {
              restoreStoredActivityForChat(restoreChatId, urlWorkflowName);
            }
          } else if (typeof restoreStoredActivityForChat === 'function') {
            // Restore inline activity (e.g. AppIntelligenceProgressCard) even when no artifact exists yet
            restoreStoredActivityForChat(restoreChatId, urlWorkflowName);
          }
        }
      }
      return;
    }

    const startupDefault = configuredStartupMode;
    if (startupDefault === 'ask') {
      setConversationMode('ask');
      setTimeout(() => {
        setIsSidePanelOpen(false);
        setCurrentArtifactMessages([]);
        if (setLayoutMode) setLayoutMode('full');
      }, 50);
    } else if (conversationMode !== 'workflow') {
      setConversationMode('workflow');
    }
  }, [
    activeChatId,
    askMessages,
    configuredStartupMode,
    consumeNavigationQueryParams,
    conversationBootstrapRef,
    conversationMode,
    currentChatId,
    currentWorkflowName,
    generalMessagesCacheRef,
    navigationLoading,
    queryChatId,
    queryMode,
    restoreStoredActivityForChat,
    restoreStoredArtifactForChat,
    setConversationMode,
    setCurrentArtifactMessages,
    setIsSidePanelOpen,
    setLayoutMode,
    setMessagesWithLogging,
    urlWorkflowName,
    upsertRestoredActivityFromArtifactMessages,
    workflowArtifactSnapshotRef,
  ]);

  useEffect(() => {
    if (conversationMode !== 'ask') return;
    if (connectionStatus !== 'connected') return;
    if (!currentChatId) return;
    if (askModeSyncedChatRef.current === currentChatId) return;

    const activeWs = wsRef.current;
    if (!activeWs || typeof activeWs.send !== 'function') return;

    const preferredGeneralChatId = getStoredActiveGeneralChatId();
    const sent = activeWs.send({
      type: 'chat.enter_general_mode',
      chat_id: currentChatId,
      ...(preferredGeneralChatId ? { general_chat_id: preferredGeneralChatId } : {}),
    });
    if (sent) {
      askModeSyncedChatRef.current = currentChatId;
    }
  }, [askModeSyncedChatRef, connectionStatus, conversationMode, currentChatId, wsRef]);

  useEffect(() => {
    if (conversationMode === 'ask' && (isSidePanelOpen || layoutMode !== 'full')) {
      setIsSidePanelOpen(false);
      setCurrentArtifactMessages([]);
      if (setLayoutMode) setLayoutMode('full');
    }
  }, [conversationMode, isSidePanelOpen, layoutMode, setLayoutMode, setCurrentArtifactMessages, setIsSidePanelOpen]);

  useEffect(() => {
    if (conversationMode === 'workflow') {
      return;
    }
    if (conversationMode !== 'ask') {
      return;
    }
    if (generalMessagesCacheRef.current && generalMessagesCacheRef.current.length > 0) {
      setMessagesWithLogging(generalMessagesCacheRef.current);
      return;
    }
    if ((!generalMessagesCacheRef.current || generalMessagesCacheRef.current.length === 0) && messagesRef.current.length > 0) {
      setMessagesWithLogging([]);
    }
    if (!activeGeneralChatId || generalHydrationPendingRef.current) {
      return;
    }
    generalHydrationPendingRef.current = true;
    Promise.resolve(hydrateGeneralTranscript(activeGeneralChatId)).finally(() => {
      generalHydrationPendingRef.current = false;
    });
  }, [
    activeGeneralChatId,
    conversationMode,
    generalHydrationPendingRef,
    generalMessagesCacheRef,
    hydrateGeneralTranscript,
    messagesRef,
    sanitizeVisibleWorkflowMessages,
    setMessagesWithLogging,
    workflowMessagesCacheRef,
  ]);

  useEffect(() => {
    if (conversationMode !== 'workflow') return;
    // Workflow transcript restoration is server-owned via websocket replay.
    // Do not reseed workflow messages from client caches on startup/reconnect.
  }, [conversationMode]);

  useEffect(() => {
    if (!queryChatId) {
      return;
    }
    if (queryMode === 'ask') {
      return;
    }
    if (!isPrimaryChatRoute) {
      return;
    }
    if (connectionStatus !== 'connected') {
      return;
    }

    const workflowFromQuery = urlWorkflowName || currentWorkflowName;
    const cacheKey = `${queryChatId}:${workflowFromQuery || ''}`;
    if (queryResumeHandledRef.current === cacheKey) {
      return;
    }

    let cancelled = false;

    const attemptRouteResume = async () => {
      const storedWorkflow = workflowConfig.resolveKnownWorkflowName(getStoredActiveWorkflowName());
      const currentResolvedWorkflow = workflowConfig.resolveKnownWorkflowName(currentWorkflowName);
      const queryResolvedWorkflow = workflowConfig.resolveKnownWorkflowName(workflowFromQuery);
      const preferredWorkflow =
        storedWorkflow
        || currentResolvedWorkflow
        || queryResolvedWorkflow
        || workflowConfig.getDefaultWorkflow()
        || workflowFromQuery;
      const workflowCandidates = buildWorkflowResolutionCandidates({
        workflowConfig,
        candidates: [
          preferredWorkflow,
          storedWorkflow,
          currentResolvedWorkflow,
          queryResolvedWorkflow,
          workflowConfig.getDefaultWorkflow(),
          workflowFromQuery,
        ],
        includeAvailable: true,
      });

      let resolvedWorkflowForChat = null;
      let sawMissingWorkflow = false;
      if (api && typeof api.getHttpBaseUrl === 'function' && currentAppId && workflowCandidates.length > 0) {
        try {
          const resolution = await resolveWorkflowForChat({
            api,
            appId: currentAppId,
            chatId: queryChatId,
            workflowCandidates,
          });
          if (cancelled) {
            return;
          }
          resolvedWorkflowForChat = resolution.workflowName;
          sawMissingWorkflow = resolution.sawMissingWorkflow;

          if (resolution.validationIncomplete) {
            resolvedWorkflowForChat = preferredWorkflow;
            sawMissingWorkflow = false;
          }

          if (!resolvedWorkflowForChat && sawMissingWorkflow) {
            console.warn('🧹 [ROUTE_RESUME] Ignoring stale query chat_id after no matching workflow session was found:', queryChatId);
            clearStoredArtifactState(queryChatId);
            clearStoredChatCacheSeed(queryChatId);
            for (const workflowForCheck of workflowCandidates) {
              clearStoredWorkflowChatId({
                appId: currentAppId,
                userId: currentUserId,
                workflowName: workflowForCheck,
              });
            }
            if (getStoredActiveChatId() === queryChatId) {
              setStoredActiveChatId(null);
            }
            if (activeChatId === queryChatId) {
              setActiveChatId(null);
            }
            queryResumeHandledRef.current = cacheKey;
            consumeNavigationQueryParams(['chat_id']);
            return;
          }
        } catch (err) {
          console.warn('⚠️ [ROUTE_RESUME] Could not validate query chat_id, attempting resume anyway:', err);
        }
      }

      if (isInWidgetMode) {
        setIsInWidgetMode(false);
      }

      const resolvedWorkflowName = resolvedWorkflowForChat || preferredWorkflow || workflowFromQuery;
      const success = resumeWorkflowSession(queryChatId, resolvedWorkflowName);
      if (success) {
        if (typeof rememberWorkflowChatSession === 'function') {
          rememberWorkflowChatSession(queryChatId, resolvedWorkflowName);
        }
        queryResumeHandledRef.current = cacheKey;
      }
    };

    attemptRouteResume();

    return () => {
      cancelled = true;
    };
  }, [
    activeChatId,
    api,
    connectionStatus,
    consumeNavigationQueryParams,
    currentAppId,
    currentWorkflowName,
    currentUserId,
    isInWidgetMode,
    isPrimaryChatRoute,
    queryChatId,
    queryMode,
    queryResumeHandledRef,
    rememberWorkflowChatSession,
    resolveKnownWorkflowName,
    resumeWorkflowSession,
    setActiveChatId,
    setIsInWidgetMode,
    urlWorkflowName,
    workflowConfig,
  ]);

  useEffect(() => {
    if (layoutMode !== 'view') {
      return;
    }
    if (!isPrimaryChatRoute || isInWidgetMode) {
      return;
    }
    const hasArtifact = Array.isArray(currentArtifactMessages) && currentArtifactMessages.length > 0;
    const artifactActive = surfaceState?.artifact?.status === 'active';
    if (hasArtifact || artifactActive) {
      return;
    }
    setLayoutMode(layoutModeForConversation);
    if (widgetOverlayOpen) {
      setWidgetOverlayOpen(false);
    }
  }, [
    currentArtifactMessages,
    isInWidgetMode,
    isPrimaryChatRoute,
    layoutMode,
    layoutModeForConversation,
    setLayoutMode,
    setWidgetOverlayOpen,
    surfaceState,
    widgetOverlayOpen,
  ]);
}
