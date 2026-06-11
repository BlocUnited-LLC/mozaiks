import { useCallback, useEffect } from 'react';

export function useEmbeddedViewController({
  conversationMode,
  isSidePanelOpen,
  layoutMode,
  currentArtifactMessages,
  viewArtifactSnapshotRef,
  handleConversationModeChange,
  emitLocalArtifactEvent,
  queryEmbeddedView,
  embeddedViewHandledRef,
  isInWidgetMode,
  setIsInWidgetMode,
  locationPathname,
  locationSearch,
  navigate,
  logout,
}) {
  const resolveEmbeddedViewId = useCallback((value) => {
    if (!value) return null;
    if (value === '1' || value === 'true') return null;
    return String(value).trim() || null;
  }, []);

  const openEmbeddedView = useCallback(async (source = 'header_action', viewId = null) => {
    try {
      const resolvedViewId = resolveEmbeddedViewId(viewId);
      if (!resolvedViewId) return;

      if (conversationMode === 'workflow') {
        viewArtifactSnapshotRef.current = {
          isOpen: isSidePanelOpen,
          layoutMode: layoutMode || 'split',
          messages: Array.isArray(currentArtifactMessages) ? [...currentArtifactMessages] : [],
        };
      } else {
        viewArtifactSnapshotRef.current = null;
      }

      if (conversationMode !== 'workflow') {
        await handleConversationModeChange('workflow');
      }

      const toolCallId = `embedded-view-${Date.now()}`;
      const payload = {
        embedded: true,
        presentation: 'artifact',
        page: resolvedViewId,
        source,
        workflow_name: 'core',
        component_type: resolvedViewId,
      };

      emitLocalArtifactEvent({
        type: 'tool_call',
        tool_name: resolvedViewId,
        tool_call_id: toolCallId,
        component_type: resolvedViewId,
        workflow_name: 'core',
        display: 'view',
        payload,
        agentName: 'System',
        agent_name: 'System',
      });
    } catch (error) {
      console.warn('Failed to open embedded view', error);
    }
  }, [
    conversationMode,
    currentArtifactMessages,
    emitLocalArtifactEvent,
    handleConversationModeChange,
    isSidePanelOpen,
    layoutMode,
    resolveEmbeddedViewId,
    viewArtifactSnapshotRef,
  ]);

  const handleEmbeddedViewClick = useCallback(async (viewOverride = null) => {
    try {
      if (isInWidgetMode) {
        setIsInWidgetMode(false);
      }
      const viewId = resolveEmbeddedViewId(viewOverride);
      await openEmbeddedView('header_action', viewId);
    } catch (error) {
      console.warn('Failed to open embedded view', error);
    }
  }, [isInWidgetMode, openEmbeddedView, resolveEmbeddedViewId, setIsInWidgetMode]);

  useEffect(() => {
    if (!queryEmbeddedView) {
      embeddedViewHandledRef.current = false;
      return;
    }
    if (embeddedViewHandledRef.current) {
      return;
    }
    embeddedViewHandledRef.current = true;
    if (isInWidgetMode) {
      setIsInWidgetMode(false);
    }
    openEmbeddedView('query_param', resolveEmbeddedViewId(queryEmbeddedView));
    try {
      const params = new URLSearchParams(locationSearch || '');
      params.delete('view');
      const nextSearch = params.toString();
      navigate(
        { pathname: locationPathname, search: nextSearch ? `?${nextSearch}` : '' },
        { replace: true }
      );
    } catch {
      // Ignore navigation cleanup failures.
    }
  }, [
    embeddedViewHandledRef,
    isInWidgetMode,
    locationPathname,
    locationSearch,
    navigate,
    openEmbeddedView,
    queryEmbeddedView,
    resolveEmbeddedViewId,
    setIsInWidgetMode,
  ]);

  const resolveHeaderActionViewId = useCallback((action = null) => {
    if (!action || typeof action !== 'object') return null;
    return (
      action.view
      || action.surface
      || action.target
      || action.component
      || action.component_type
      || action?.payload?.view
      || action?.payload?.surface
      || action?.payload?.component
      || action?.payload?.component_type
      || action?.payload?.page
      || null
    );
  }, []);

  const handleHeaderAction = useCallback((actionId, action = null) => {
    const explicitActionType = String(action?.action || action?.action_type || '').trim().toLowerCase();
    const viewId = resolveHeaderActionViewId(action);
    if (explicitActionType === 'open_view' || explicitActionType === 'open_surface' || viewId) {
      handleEmbeddedViewClick(viewId);
      return;
    }

    if (actionId === 'navigate' || action?.action === 'navigate') {
      const href = action?.href || action?.path;
      if (href) {
        if (href.startsWith('/')) {
          navigate(href);
        } else {
          window.location.href = href;
        }
      }
      return;
    }

    if (actionId === 'signout' || action?.action === 'signout') {
      logout();
      return;
    }

    if (action?.href) {
      if (action.href.startsWith('/')) {
        navigate(action.href);
      } else {
        window.location.href = action.href;
      }
    }
  }, [handleEmbeddedViewClick, logout, navigate, resolveHeaderActionViewId]);

  return {
    handleHeaderAction,
  };
}
