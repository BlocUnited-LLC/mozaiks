const SURFACE_MODES = {
  ASK: 'ASK',
  WORKFLOW: 'WORKFLOW',
  VIEW: 'VIEW',
};

const ARTIFACT_STATUS = {
  INACTIVE: 'inactive',
  ACTIVE: 'active',
  STALE: 'stale',
};

const LAYOUT_MODES = new Set(['full', 'split', 'minimized', 'view']);

const normalizeLayoutMode = (value) => {
  if (LAYOUT_MODES.has(value)) return value;
  return 'full';
};

const normalizeConversationMode = (value) => {
  return value === 'ask' ? 'ask' : 'workflow';
};

const normalizeDisplayMode = (value) => {
  if (!value) return null;
  const lowered = String(value).toLowerCase();
  if (['artifact', 'inline', 'view', 'fullscreen'].includes(lowered)) {
    return lowered;
  }
  return null;
};

const deriveSurfaceMode = (conversationMode, layoutMode) => {
  if (layoutMode === 'view') return SURFACE_MODES.VIEW;
  return conversationMode === 'ask' ? SURFACE_MODES.ASK : SURFACE_MODES.WORKFLOW;
};

const shouldOpenArtifactPanel = (layoutMode) => layoutMode !== 'full';

export const createInitialSurfaceState = (conversationMode = 'workflow') => {
  const normalizedConversation = normalizeConversationMode(conversationMode);
  const layoutMode = normalizedConversation === 'ask' ? 'full' : 'split';
  return {
    conversationMode: normalizedConversation,
    layoutMode,
    previousLayoutMode: 'full',
    surfaceMode: deriveSurfaceMode(normalizedConversation, layoutMode),
    workflowStatus: 'idle',
    artifact: {
      status: ARTIFACT_STATUS.INACTIVE,
      display: null,
      panelOpen: false,
      lastEventId: null,
    },
    widget: {
      isInWidgetMode: false,
      isWidgetVisible: true,
      isChatOverlayOpen: false,
      widgetOverlayOpen: false,
    },
  };
};

export const uiSurfaceReducer = (state, action) => {
  if (!action) return state;

  switch (action.type) {
    case 'SET_CONVERSATION_MODE': {
      const nextMode = normalizeConversationMode(action.mode);
      if (nextMode === state.conversationMode) return state;

      if (nextMode === 'ask') {
        const nextLayout = 'full';
        return {
          ...state,
          conversationMode: 'ask',
          layoutMode: nextLayout,
          surfaceMode: SURFACE_MODES.ASK,
          artifact: {
            ...state.artifact,
            panelOpen: false,
            status: state.artifact.status === ARTIFACT_STATUS.ACTIVE
              ? ARTIFACT_STATUS.STALE
              : state.artifact.status,
          },
        };
      }

      const restoredLayout = state.previousLayoutMode && state.previousLayoutMode !== 'full'
        ? state.previousLayoutMode
        : 'split';
      const normalizedLayout = normalizeLayoutMode(restoredLayout);
      return {
        ...state,
        conversationMode: 'workflow',
        layoutMode: normalizedLayout,
        surfaceMode: deriveSurfaceMode('workflow', normalizedLayout),
        artifact: {
          ...state.artifact,
          panelOpen: shouldOpenArtifactPanel(normalizedLayout),
        },
      };
    }
    case 'SET_LAYOUT_MODE': {
      const requested = normalizeLayoutMode(action.mode);
      if (state.conversationMode === 'ask' && requested !== 'full') {
        return {
          ...state,
          layoutMode: 'full',
          surfaceMode: SURFACE_MODES.ASK,
          artifact: {
            ...state.artifact,
            panelOpen: false,
          },
        };
      }

      if (requested === state.layoutMode) return state;

      return {
        ...state,
        layoutMode: requested,
        previousLayoutMode: requested === 'view' ? state.previousLayoutMode : requested,
        surfaceMode: deriveSurfaceMode(state.conversationMode, requested),
        artifact: {
          ...state.artifact,
          panelOpen: shouldOpenArtifactPanel(requested),
        },
      };
    }
    case 'SET_PREVIOUS_LAYOUT_MODE': {
      const nextValue = normalizeLayoutMode(action.mode);
      if (nextValue === state.previousLayoutMode) return state;
      return {
        ...state,
        previousLayoutMode: nextValue,
      };
    }
    case 'SET_WIDGET_MODE': {
      if (state.widget.isInWidgetMode === action.value) return state;
      return {
        ...state,
        widget: {
          ...state.widget,
          isInWidgetMode: Boolean(action.value),
        },
      };
    }
    case 'SET_WIDGET_VISIBILITY': {
      if (state.widget.isWidgetVisible === action.value) return state;
      return {
        ...state,
        widget: {
          ...state.widget,
          isWidgetVisible: Boolean(action.value),
        },
      };
    }
    case 'SET_CHAT_OVERLAY_OPEN': {
      if (state.widget.isChatOverlayOpen === action.value) return state;
      return {
        ...state,
        widget: {
          ...state.widget,
          isChatOverlayOpen: Boolean(action.value),
        },
      };
    }
    case 'SET_WIDGET_OVERLAY_OPEN': {
      if (state.widget.widgetOverlayOpen === action.value) return state;
      return {
        ...state,
        widget: {
          ...state.widget,
          widgetOverlayOpen: Boolean(action.value),
        },
      };
    }
    case 'SET_ARTIFACT_PANEL_OPEN': {
      const nextOpen = Boolean(action.open);
      if (nextOpen === state.artifact.panelOpen) return state;

      let nextLayout = state.layoutMode;
      let nextPanelOpen = nextOpen;
      if (state.conversationMode === 'ask') {
        nextLayout = 'full';
        nextPanelOpen = false;
      } else if (nextOpen) {
        nextLayout = state.layoutMode === 'full' ? 'split' : state.layoutMode;
      } else {
        nextLayout = 'full';
      }

      return {
        ...state,
        layoutMode: nextLayout,
        previousLayoutMode: nextLayout === 'view' ? state.previousLayoutMode : nextLayout,
        surfaceMode: deriveSurfaceMode(state.conversationMode, nextLayout),
        artifact: {
          ...state.artifact,
          panelOpen: nextPanelOpen,
          status: nextPanelOpen ? ARTIFACT_STATUS.ACTIVE : state.artifact.status,
        },
      };
    }
    case 'TOGGLE_ARTIFACT_PANEL': {
      return uiSurfaceReducer(state, {
        type: 'SET_ARTIFACT_PANEL_OPEN',
        open: !state.artifact.panelOpen,
      });
    }
    case 'ARTIFACT_EMITTED': {
      const display = normalizeDisplayMode(action.display) || 'artifact';
      const wantsView = display === 'view' || display === 'fullscreen';
      const nextConversation = 'workflow';
      const nextLayout = wantsView
        ? 'view'
        : (state.layoutMode === 'full' ? 'split' : state.layoutMode);
      return {
        ...state,
        conversationMode: nextConversation,
        layoutMode: nextLayout,
        surfaceMode: deriveSurfaceMode(nextConversation, nextLayout),
        previousLayoutMode: nextLayout === 'view' ? state.previousLayoutMode : nextLayout,
        artifact: {
          status: ARTIFACT_STATUS.ACTIVE,
          display,
          panelOpen: true,
          lastEventId: action.eventId || state.artifact.lastEventId,
        },
      };
    }
    case 'ARTIFACT_CLEARED': {
      const nextLayout = state.conversationMode === 'ask' ? 'full' : 'full';
      return {
        ...state,
        layoutMode: nextLayout,
        surfaceMode: deriveSurfaceMode(state.conversationMode, nextLayout),
        artifact: {
          ...state.artifact,
          status: ARTIFACT_STATUS.INACTIVE,
          display: null,
          panelOpen: false,
          lastEventId: null,
        },
      };
    }
    case 'WORKFLOW_STATUS': {
      if (state.workflowStatus === action.status) return state;
      return {
        ...state,
        workflowStatus: action.status || state.workflowStatus,
        artifact: {
          ...state.artifact,
          status: action.status === 'completed' || action.status === 'error'
            ? ARTIFACT_STATUS.STALE
            : state.artifact.status,
        },
      };
    }
    default:
      return state;
  }
};

export const mapSurfaceEventToAction = (event) => {
  if (!event || !event.type) return null;

  const eventType = String(event.type);

  if (eventType === 'ui_tool_event' || eventType === 'UI_TOOL_EVENT') {
    const detail = event.data || event.payload || {};
    const display = event.display
      || event.display_type
      || event.mode
      || detail.display
      || detail.display_type
      || detail.mode
      || detail?.payload?.display;
    const normalized = normalizeDisplayMode(display);
    if (normalized === 'artifact' || normalized === 'view' || normalized === 'fullscreen') {
      return {
        type: 'ARTIFACT_EMITTED',
        display: normalized,
        eventId: event.eventId
          || event.event_id
          || detail.eventId
          || detail.event_id
          || detail?.payload?.event_id
          || null,
      };
    }
    return null;
  }

  if (eventType === 'tool_call' || eventType === 'chat.tool_call') {
    const detail = event.data || {};
    const display = event.display || event.display_type || event.mode || detail.display || detail.display_type || detail.mode || detail?.payload?.display;
    const normalized = normalizeDisplayMode(display);
    if (normalized === 'artifact' || normalized === 'view' || normalized === 'fullscreen') {
      return {
        type: 'ARTIFACT_EMITTED',
        display: normalized,
        eventId: event.tool_call_id || event.corr || detail.tool_call_id || detail.corr || null,
      };
    }
  }

  if (eventType.startsWith('agui.lifecycle.')) {
    if (eventType === 'agui.lifecycle.RunStarted') {
      return { type: 'WORKFLOW_STATUS', status: 'running' };
    }
    if (eventType === 'agui.lifecycle.RunFinished') {
      return { type: 'WORKFLOW_STATUS', status: 'completed' };
    }
    if (eventType === 'agui.lifecycle.RunError') {
      return { type: 'WORKFLOW_STATUS', status: 'error' };
    }
  }

  return null;
};

export { SURFACE_MODES, ARTIFACT_STATUS };
