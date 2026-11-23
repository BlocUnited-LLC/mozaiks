import { useEffect } from 'react';

import { readStoredLastArtifact } from '../session/chatSessionStorage';

export function useChatArtifactLayoutEffects({
  connectionStatus,
  currentChatId,
  chatExists,
  artifactRestoredOnceRef,
  conversationMode,
  currentWorkflowName,
  restoreStoredArtifactForChat,
  layoutMode,
  setIsMobileView,
  setForceOverlay,
  widgetOverlayOpen,
  setWidgetOverlayOpen,
  isSidePanelOpen,
  hasArtifactContent,
  setIsSidePanelOpen,
  isMobileView,
  mobileDrawerState,
  setMobileDrawerState,
  setHasUnseenArtifact,
  hasUnseenChat,
  setHasUnseenChat,
  forceOverlay,
  isInWidgetMode,
  widgetChatMinimized,
  setWidgetChatMinimized,
}) {
  useEffect(() => {
    if (connectionStatus !== 'connected') return;
    if (!currentChatId) return;
    if (!chatExists) return;
    if (artifactRestoredOnceRef.current) return;
    if (conversationMode === 'ask') return;

    try {
      const cached = readStoredLastArtifact(currentChatId);
      if (!cached || !cached.tool_name) return;

      restoreStoredArtifactForChat(currentChatId, currentWorkflowName);
    } catch (error) {
      console.warn('💾 [RESTORE] Failed to restore artifact:', error);
    }
  }, [
    artifactRestoredOnceRef,
    chatExists,
    connectionStatus,
    conversationMode,
    currentChatId,
    currentWorkflowName,
    restoreStoredArtifactForChat,
  ]);

  useEffect(() => {
    const compute = () => {
      try {
        const width = window.innerWidth;
        const height = window.innerHeight;
        const mobile = width < 768;
        const shortViewport = height < 500;

        setIsMobileView(mobile);
        setForceOverlay(mobile || shortViewport);
      } catch {
        // Ignore window access failures during teardown or unusual embed contexts.
      }
    };

    compute();
    window.addEventListener('resize', compute);
    window.addEventListener('orientationchange', compute);

    return () => {
      window.removeEventListener('resize', compute);
      window.removeEventListener('orientationchange', compute);
    };
  }, [setForceOverlay, setIsMobileView]);

  useEffect(() => {
    if (layoutMode !== 'view' && widgetOverlayOpen) {
      setWidgetOverlayOpen(false);
    }
  }, [layoutMode, setWidgetOverlayOpen, widgetOverlayOpen]);

  useEffect(() => {
    if (layoutMode !== 'view') {
      return;
    }
    if (!isSidePanelOpen) {
      setIsSidePanelOpen(true);
    }
    if (isMobileView) {
      setMobileDrawerState('expanded');
    }
  }, [isMobileView, isSidePanelOpen, layoutMode, setIsSidePanelOpen, setMobileDrawerState]);

  useEffect(() => {
    setMobileDrawerState('peek');
  }, [currentChatId, conversationMode, setMobileDrawerState]);

  useEffect(() => {
    // A new workflow shows the empty canvas on desktop. Mobile starts on the
    // conversation and opens the drawer for output or an explicit logo click.
    if (!isSidePanelOpen) setMobileDrawerState('peek');
    else if (hasArtifactContent || layoutMode === 'view') setMobileDrawerState('expanded');
  }, [currentChatId, conversationMode, isMobileView, isSidePanelOpen, hasArtifactContent, layoutMode, setMobileDrawerState]);

  useEffect(() => {
    if (!isSidePanelOpen || !hasArtifactContent) {
      setHasUnseenArtifact(false);
      return;
    }
    setHasUnseenArtifact(mobileDrawerState !== 'expanded');
  }, [hasArtifactContent, isSidePanelOpen, mobileDrawerState, setHasUnseenArtifact]);

  useEffect(() => {
    if (mobileDrawerState !== 'expanded' && hasUnseenChat) {
      setHasUnseenChat(false);
    }
  }, [hasUnseenChat, mobileDrawerState, setHasUnseenChat]);

  useEffect(() => {
    if (isSidePanelOpen && forceOverlay && (!isMobileView || mobileDrawerState === 'expanded')) {
      const { overflow } = document.body.style;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = overflow;
      };
    }
  }, [forceOverlay, isSidePanelOpen, isMobileView, mobileDrawerState]);

  useEffect(() => {
    if (!isInWidgetMode && widgetChatMinimized) {
      setWidgetChatMinimized(false);
    }
  }, [isInWidgetMode, setWidgetChatMinimized, widgetChatMinimized]);
}
