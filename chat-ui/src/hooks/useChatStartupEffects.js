import { useEffect } from 'react';

import {
  clearStoredArtifactState,
  clearStoredChatCacheSeed,
  getStoredActiveChatId,
  getStoredActiveGeneralChatId,
  setStoredActiveChatId,
} from '../session/chatSessionStorage';

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
      if (snapshot?.isOpen && snapshot?.messages?.length > 0) {
        setTimeout(() => {
          setIsSidePanelOpen(true);
          setCurrentArtifactMessages(snapshot.messages);
          if (snapshot.layoutMode && setLayoutMode) {
            setLayoutMode(snapshot.layoutMode);
          }
        }, 100);
      } else {
        const restoreChatId = queryChatId || currentChatId || activeChatId || getStoredActiveChatId();
        if (restoreChatId) {
          const restored = restoreStoredArtifactForChat(restoreChatId, urlWorkflowName);
          if (restored) {
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
    generalMessagesCacheRef,
    navigationLoading,
    queryChatId,
    queryMode,
    restoreStoredArtifactForChat,
    setConversationMode,
    setCurrentArtifactMessages,
    setIsSidePanelOpen,
    setLayoutMode,
    setMessagesWithLogging,
    urlWorkflowName,
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
      const workflowForCheck =
        resolveKnownWorkflowName(workflowFromQuery)
        || resolveKnownWorkflowName(currentWorkflowName)
        || workflowConfig.getDefaultWorkflow()
        || workflowFromQuery;

      if (api && typeof api.getHttpBaseUrl === 'function' && currentAppId && workflowForCheck) {
        try {
          const response = await fetch(
            `${api.getHttpBaseUrl()}/api/chats/exists/${currentAppId}/${workflowForCheck}/${queryChatId}`
          );
          if (cancelled) {
            return;
          }

          if (response.ok) {
            const result = await response.json();
            if (cancelled) {
              return;
            }
            if (result?.exists === false) {
              console.warn('🧹 [ROUTE_RESUME] Ignoring stale query chat_id after backend reset:', queryChatId);
              clearStoredArtifactState(queryChatId);
              clearStoredChatCacheSeed(queryChatId);
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
          }
        } catch (err) {
          console.warn('⚠️ [ROUTE_RESUME] Could not validate query chat_id, attempting resume anyway:', err);
        }
      }

      if (isInWidgetMode) {
        setIsInWidgetMode(false);
      }

      const success = resumeWorkflowSession(queryChatId, workflowFromQuery);
      if (success) {
        queryResumeHandledRef.current = cacheKey;
        consumeNavigationQueryParams(['chat_id']);
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
    isInWidgetMode,
    isPrimaryChatRoute,
    queryChatId,
    queryMode,
    queryResumeHandledRef,
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
