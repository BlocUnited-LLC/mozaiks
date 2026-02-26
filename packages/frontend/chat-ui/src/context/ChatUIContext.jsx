import React, { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { createInitialSurfaceState, mapSurfaceEventToAction, uiSurfaceReducer } from '../state/uiSurfaceReducer';

const ChatUIContext = createContext(null);

export const useChatUI = () => {
  const context = useContext(ChatUIContext);
  if (!context) {
    throw new Error('useChatUI must be used within a ChatUIProvider');
  }
  return context;
};

export const ChatUIProvider = ({ 
  children,
  authAdapter = null,
  apiAdapter = null,
  uiConfig = null,
  workflowInitializer = null,
  uiToolRenderer = null,
  onReady = () => {},
  agents = []
}) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);
  const [authAdapterInstance, setAuthAdapterInstance] = useState(authAdapter);
  const [apiAdapterInstance, setApiAdapterInstance] = useState(apiAdapter);
  const [agentSystemInitialized, setAgentSystemInitialized] = useState(false);
  const [workflowsInitialized, setWorkflowsInitialized] = useState(false);
  const WORKFLOW_INIT_TIMEOUT_MS = 8000; // guard against endless spinner

  // Use ref for onReady callback to avoid re-triggering the init effect
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // Persistent chat state (survives navigation)
  const [activeChatId, setActiveChatId] = useState(null);
  const [activeWorkflowName, setActiveWorkflowName] = useState(null);
  const [chatMinimized, setChatMinimized] = useState(false);
  const [unreadChatCount, setUnreadChatCount] = useState(0);
  
  // Surface/layout FSM (controls chat/artifact/widget state)
  const [surfaceState, surfaceDispatch] = useReducer(
    uiSurfaceReducer,
    null,
    () => {
      let initialMode = 'workflow';
      try {
        const stored = localStorage.getItem('mozaiks.conversation_mode');
        if (stored === 'ask' || stored === 'workflow') {
          initialMode = stored;
        }
      } catch (_) {
        /* ignore storage errors */
      }
      return createInitialSurfaceState(initialMode);
    },
  );
  const surfaceStateRef = useRef(surfaceState);
  useEffect(() => {
    surfaceStateRef.current = surfaceState;
  }, [surfaceState]);

  const layoutMode = surfaceState.layoutMode;
  const previousLayoutMode = surfaceState.previousLayoutMode;
  const isArtifactOpen = surfaceState.artifact.panelOpen;
  const isInWidgetMode = surfaceState.widget.isInWidgetMode;
  const isWidgetVisible = surfaceState.widget.isWidgetVisible;
  const isChatOverlayOpen = surfaceState.widget.isChatOverlayOpen;
  const widgetOverlayOpen = surfaceState.widget.widgetOverlayOpen;
  const conversationMode = surfaceState.conversationMode;
  const surfaceMode = surfaceState.surfaceMode;
  const workflowStatus = surfaceState.workflowStatus;

  const setLayoutMode = useCallback((mode) => {
    const current = surfaceStateRef.current?.layoutMode;
    const nextMode = typeof mode === 'function' ? mode(current) : mode;
    surfaceDispatch({ type: 'SET_LAYOUT_MODE', mode: nextMode });
  }, [surfaceDispatch]);

  const setPreviousLayoutMode = useCallback((mode) => {
    const current = surfaceStateRef.current?.previousLayoutMode;
    const nextMode = typeof mode === 'function' ? mode(current) : mode;
    surfaceDispatch({ type: 'SET_PREVIOUS_LAYOUT_MODE', mode: nextMode });
  }, [surfaceDispatch]);

  const setIsArtifactOpen = useCallback((value) => {
    const current = surfaceStateRef.current?.artifact?.panelOpen;
    const nextValue = typeof value === 'function' ? value(current) : value;
    surfaceDispatch({ type: 'SET_ARTIFACT_PANEL_OPEN', open: nextValue });
  }, [surfaceDispatch]);

  const setIsInWidgetMode = useCallback((value) => {
    const current = surfaceStateRef.current?.widget?.isInWidgetMode;
    const nextValue = typeof value === 'function' ? value(current) : value;
    surfaceDispatch({ type: 'SET_WIDGET_MODE', value: nextValue });
  }, [surfaceDispatch]);

  const setIsWidgetVisible = useCallback((value) => {
    const current = surfaceStateRef.current?.widget?.isWidgetVisible;
    const nextValue = typeof value === 'function' ? value(current) : value;
    surfaceDispatch({ type: 'SET_WIDGET_VISIBILITY', value: nextValue });
  }, [surfaceDispatch]);

  const setIsChatOverlayOpen = useCallback((value) => {
    const current = surfaceStateRef.current?.widget?.isChatOverlayOpen;
    const nextValue = typeof value === 'function' ? value(current) : value;
    surfaceDispatch({ type: 'SET_CHAT_OVERLAY_OPEN', value: nextValue });
  }, [surfaceDispatch]);

  const setWidgetOverlayOpen = useCallback((value) => {
    const current = surfaceStateRef.current?.widget?.widgetOverlayOpen;
    const nextValue = typeof value === 'function' ? value(current) : value;
    surfaceDispatch({ type: 'SET_WIDGET_OVERLAY_OPEN', value: nextValue });
  }, [surfaceDispatch]);

  const setConversationMode = useCallback((mode) => {
    const current = surfaceStateRef.current?.conversationMode;
    const nextMode = typeof mode === 'function' ? mode(current) : mode;
    surfaceDispatch({ type: 'SET_CONVERSATION_MODE', mode: nextMode });
  }, [surfaceDispatch]);

  const dispatchSurfaceAction = useCallback((action) => {
    if (action) {
      surfaceDispatch(action);
    }
  }, [surfaceDispatch]);

  const dispatchSurfaceEvent = useCallback((event) => {
    const action = mapSurfaceEventToAction(event);
    if (action) {
      surfaceDispatch(action);
    }
  }, [surfaceDispatch]);

  const [currentArtifactContext, setCurrentArtifactContext] = useState(null); // { type, payload, id }
  const [activeGeneralChatId, setActiveGeneralChatId] = useState(null);
  const [generalChatSummary, setGeneralChatSummary] = useState(null);
  const [generalChatSessions, setGeneralChatSessions] = useState([]);
  
  // Ask-mode and workflow-mode message caches (survive navigation)
  const [askMessages, setAskMessages] = useState([]);
  const [workflowMessages, setWorkflowMessages] = useState([]);
  const [pendingNavigationTrigger, setPendingNavigationTrigger] = useState(null);

  // Workflow sessions list for future dropdown (IN_PROGRESS workflows)
  const [workflowSessions, setWorkflowSessions] = useState([]);

  useEffect(() => {
    try {
      if (activeChatId) {
        localStorage.setItem('mozaiks.current_chat_id', activeChatId);
      }
    } catch (_) {
      /* ignore storage errors */
    }
  }, [activeChatId]);

  useEffect(() => {
    try {
      if (activeWorkflowName) {
        localStorage.setItem('mozaiks.current_workflow_name', activeWorkflowName);
      }
    } catch (_) {
      /* ignore storage errors */
    }
  }, [activeWorkflowName]);

  useEffect(() => {
    try {
      localStorage.setItem('mozaiks.conversation_mode', conversationMode);
    } catch (_) {
      /* ignore storage errors */
    }
  }, [conversationMode]);

  useEffect(() => {
    const initializeServices = async () => {
      try {
        // Optional: allow host to initialize workflow/tool registry without bundling
        // any workflow implementation into this package.
        if (typeof workflowInitializer === 'function') {
          console.log('🔧 Initializing workflow registry (host-provided)...');
          try {
            await Promise.race([
              Promise.resolve(workflowInitializer()),
              new Promise((_, reject) => setTimeout(() => reject(new Error('workflow_init_timeout')), WORKFLOW_INIT_TIMEOUT_MS))
            ]);
            setWorkflowsInitialized(true);
            console.log('✅ Workflow registry initialized');
          } catch (wfErr) {
            if (wfErr?.message === 'workflow_init_timeout') {
              console.warn('⚠️ Workflow initialization timed out – continuing with partial UI.');
            } else {
              console.warn('⚠️ Workflow registry init failed:', wfErr);
            }
          }
        }

        // Adapters are host-injected; keep local state to avoid undefined access.
        setAuthAdapterInstance(authAdapter);
        setApiAdapterInstance(apiAdapter);

        // Get initial user (optional)
        try {
          const currentUser = await authAdapter?.getCurrentUser?.();
          setUser(currentUser || null);
        } catch (_) {
          setUser(null);
        }

        // Listen for auth state changes (optional)
        if (authAdapter?.onAuthStateChange) {
          authAdapter.onAuthStateChange((newUser) => {
            setUser(newUser || null);
          });
        }

        // Agent system functionality piggybacks on workflow readiness where applicable.
        setAgentSystemInitialized(true);
        setInitialized(true);
        setLoading(false);
        onReadyRef.current();

      } catch (error) {
        console.error('Failed to initialize ChatUI:', error);
        setLoading(false);
      }
    };

    initializeServices();
  }, [authAdapter, apiAdapter, workflowInitializer]);

  useEffect(() => {
    // Agents are auto-discovered through the workflow system
    if (agents.length > 0) {
      console.warn('Custom agent registration via props is not supported. Agents are defined in the agents.json file.');
    }
  }, [agents]);

  const resolvedConfig = useMemo(() => {
    if (uiConfig && typeof uiConfig === 'object') return uiConfig;
    return {};
  }, [uiConfig]);

  const contextValue = {
    // User state
    user,
    setUser,
    loading,
    initialized,

    // System state
    agentSystemInitialized,
    workflowsInitialized,

    // Persistent chat state
    activeChatId,
    setActiveChatId,
    activeWorkflowName,
    setActiveWorkflowName,
    chatMinimized,
    setChatMinimized,
    unreadChatCount,
    setUnreadChatCount,
    
    // Fluid layout state
    layoutMode,
    setLayoutMode,
    previousLayoutMode,
    setPreviousLayoutMode,
    isArtifactOpen,
    setIsArtifactOpen,
    isInWidgetMode,
    setIsInWidgetMode,
    isWidgetVisible,
    setIsWidgetVisible,
    isChatOverlayOpen,
    setIsChatOverlayOpen,
    widgetOverlayOpen,
    setWidgetOverlayOpen,
    currentArtifactContext,
    setCurrentArtifactContext,
    conversationMode,
    setConversationMode,
    surfaceMode,
    workflowStatus,
    surfaceState,
    dispatchSurfaceAction,
    dispatchSurfaceEvent,
    activeGeneralChatId,
    setActiveGeneralChatId,
    generalChatSummary,
    setGeneralChatSummary,
    generalChatSessions,
    setGeneralChatSessions,
    workflowSessions,
    setWorkflowSessions,
    askMessages,
    setAskMessages,
    workflowMessages,
    setWorkflowMessages,
    pendingNavigationTrigger,
    setPendingNavigationTrigger,

    // Configuration (host-provided)
    config: resolvedConfig,

    // Services (use state instances to avoid initialization errors)
    auth: authAdapterInstance,
    api: apiAdapterInstance,

    // UI tool rendering (host-provided)
    uiToolRenderer: (typeof uiToolRenderer === 'function') ? uiToolRenderer : null,

    // Actions
    logout: async () => {
      if (authAdapterInstance) {
        await authAdapterInstance.logout();
        setUser(null);
      }
    },
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gradient-to-br from-gray-900 to-blue-900">
        <div className="text-white text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-white mx-auto mb-4"></div>
          <p className="techfont">Initializing ChatUI...</p>
        </div>
      </div>
    );
  }

  return (
    <ChatUIContext.Provider value={contextValue}>
      {children}
    </ChatUIContext.Provider>
  );
};
