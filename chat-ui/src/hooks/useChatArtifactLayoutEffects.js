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
  setLayoutMode,
  setIsMobileView,
  setForceOverlay,
  widgetOverlayOpen,
  setWidgetOverlayOpen,
  isSidePanelOpen,
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
      console.warn('[RESTORE] Failed to restore artifact:', error);
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

        if (mobile && layoutMode === 'split') {
          setLayoutMode('full');
        }
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
  }, [layoutMode, setForceOverlay, setIsMobileView, setLayoutMode]);

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
    if (!isMobileView) {
      if (mobileDrawerState !== 'peek') {
        setMobileDrawerState('peek');
      }
      return;
    }
  }, [isMobileView, mobileDrawerState, setMobileDrawerState]);

  useEffect(() => {
    if (!isSidePanelOpen) {
      setHasUnseenArtifact(false);
      return;
    }
    setHasUnseenArtifact(mobileDrawerState !== 'expanded');
  }, [isSidePanelOpen, mobileDrawerState, setHasUnseenArtifact]);

  useEffect(() => {
    if (mobileDrawerState !== 'expanded' && hasUnseenChat) {
      setHasUnseenChat(false);
    }
  }, [hasUnseenChat, mobileDrawerState, setHasUnseenChat]);

  useEffect(() => {
    if (isSidePanelOpen && forceOverlay) {
      const { overflow } = document.body.style;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = overflow;
      };
    }
  }, [forceOverlay, isSidePanelOpen]);

  useEffect(() => {
    if (!isInWidgetMode && widgetChatMinimized) {
      setWidgetChatMinimized(false);
    }
  }, [isInWidgetMode, setWidgetChatMinimized, widgetChatMinimized]);
}