import { useEffect, useState, useRef, useCallback } from "react";
import Header from "../components/layout/Header";
import Footer from "../components/layout/Footer";
import ChatInterface from '../components/chat/ChatInterface';
import ArtifactPanel from '../components/chat/ArtifactPanel';
import FluidChatLayout from '../components/chat/FluidChatLayout';
import MobileArtifactDrawer from '../components/chat/MobileArtifactDrawer';
import { TransitionScreen } from '../ui/screens/TransitionScreen';
import { applyArtifactUpdate, applyOptimisticUpdate, deriveArtifactId, interpolateParams } from '../core/actions/actionUtils';
import { useNavigate, useLocation } from "react-router-dom";
import { useContext } from "react";
import { useChatUI } from "../context/ChatUIContext";
import { NavigationContext } from "../providers/NavigationProvider";
import workflowConfig from '../config/workflowConfig';
import { getWorkflow } from '../@chat-workflows/index.js';
import resolveWorkflow from '../utils/resolveWorkflow';
import { dynamicUIHandler } from '../core/dynamicUIHandler';
import platform from '../platform/index.js';
import LoadingSpinner from '../utils/AgentChatLoadingSpinner';
import useTheme from "../styles/useTheme";
import {
  applyBrandImageFallback,
  getBrandLogoSrc,
  getChatBackgroundSrc,
} from "../styles/brandAssets";
import { readNavigationCache, writeNavigationCache } from '../navigation/navigationCache';
import ErrorBoundary, { ArtifactErrorFallback, ChatInterfaceErrorFallback } from '../components/ErrorBoundary';
import { useChatArtifactLayoutEffects } from '../hooks/useChatArtifactLayoutEffects';
import { useChatSessionHistory } from '../hooks/useChatSessionHistory';
import { useEmbeddedViewController } from '../hooks/useEmbeddedViewController';
import { useConversationModeController } from '../hooks/useConversationModeController';
import { useChatStartupEffects } from '../hooks/useChatStartupEffects';
import { useWorkflowStart } from '../hooks/useWorkflowStart';
import {
  clearStoredArtifactState,
  clearStoredChatCacheSeed,
  getStoredActiveChatId,
  getStoredActiveWorkflowName,
  getStoredArtifactPanelOpen,
  getStoredChatCacheSeed,
  readStoredCurrentArtifact,
  readStoredLastArtifact,
  setStoredActiveChatId,
  setStoredArtifactPanelOpen,
  setStoredChatCacheSeed,
  writeStoredLastArtifact,
  writeStoredCurrentArtifact,
} from '../session/chatSessionStorage';
import {
  AskHistorySidebar,
  MobileAskHistoryDrawer,
  WorkflowHistorySidebar,
} from './ChatPageHistoryPanels';
import { ChatPageViewWidget } from './ChatPageViewWidget';
import {
  debugFlag,
  getAccessToken,
  getUserIdFromToken,
} from './chatPageHelpers';
import {
  buildSupportConversationTranscript,
  buildSupportRequestPayload,
  buildUserSupportPath,
  resolveSupportRequestScope,
  shouldOfferHumanSupport,
} from '../utils/supportLinks';

// Extracted hooks for gradual migration
// Usage: const { messages, addMessage, ... } = useConversation({ chatId, conversationMode, ... });
// import { useConversation, useArtifacts, useChatWebSocket } from './hooks';

const ChatPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  // Navigation config (null-safe — dev shell may omit NavigationProvider)
  const navContext = useContext(NavigationContext);
  const configuredStartupMode = navContext?.chat_startup_mode || 'ask';  // "ask" or "workflow"
  const configuredEntryWorkflow = navContext?.entry_point || null;
  const navigationLoading = navContext?.loading ?? false;
  // Core state
  const [messages, setMessages] = useState([]);
  const [connectionRetryNonce, setConnectionRetryNonce] = useState(0);
  // Ref mirror to access latest messages inside callbacks without stale closure
  const messagesRef = useRef([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  const [ws, setWs] = useState(null);
  const wsRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const setMessagesWithLogging = useCallback(updater => setMessages(prev => typeof updater==='function'?updater(prev):updater), []);
  const [connectionStatus, setConnectionStatus] = useState('disconnected');
  const [transportType, setTransportType] = useState(null);
  const [modeChangePending, setModeChangePending] = useState(false);
  const [currentChatId, setCurrentChatId] = useState(null); // set via start/resume flow below
  const LOCAL_STORAGE_KEY = 'mozaiks.current_chat_id';
  const [, setConnectionInitialized] = useState(false);
  const [workflowConfigLoaded, setWorkflowConfigLoaded] = useState(false); // becomes true once workflow config resolved
  const [cacheSeed, setCacheSeed] = useState(null); // per-chat cache seed for unified backend/frontend caching
  const [chatExists, setChatExists] = useState(null); // tri-state: null=unknown, true=exists, false=new
  const connectionInProgressRef = useRef(false);
  // Guard to prevent overlapping start logic (used by preflight existence effect)
  const pendingStartRef = useRef(false);
  const conversationBootstrapRef = useRef(false);
  const modeChangeInProgressRef = useRef(false);
  // Track whether we've already injected an EscalationCard in the current ask session
  const escalationCardInjectedRef = useRef(false);
  const pathSegments = location.pathname.split('/').filter(Boolean);
  let pathAppId = null;
  let pathWorkflowName = null;
  const isPrimaryChatRoute = pathSegments.length === 0 || pathSegments[0] === 'chat' || pathSegments[0] === 'app';
  const lastPrimaryRouteRef = useRef(isPrimaryChatRoute);

  if (pathSegments[0] === 'chat') {
    pathAppId = pathSegments[1] || null;
    pathWorkflowName = pathSegments[2] || null;
  } else if (pathSegments[0] === 'app') {
    pathAppId = pathSegments[1] || null;
    pathWorkflowName = pathSegments[2] || null;
  }

  const searchParams = new URLSearchParams(location.search || '');
  const queryAppId = searchParams.get('appId') || searchParams.get('app_id');
  const queryWorkflowName = searchParams.get('workflow');
  const queryChatId = searchParams.get('chat_id');
  const queryGeneralChatId = searchParams.get('general_chat_id') || searchParams.get('generalChatId');
  const queryMode = searchParams.get('mode'); // 'ask' or 'workflow'
  const queryForceAsk = ['1', 'true', 'yes', 'on'].includes(
    String(searchParams.get('force_ask') || '').toLowerCase()
  );
  const queryFreshStart = ['1', 'true', 'yes', 'on'].includes(
    String(searchParams.get('new') || searchParams.get('fresh') || searchParams.get('force_new') || '').toLowerCase()
  );
  const queryDeferStart = ['1', 'true', 'yes', 'on'].includes(
    String(searchParams.get('defer_start') || '').toLowerCase()
  );
  const queryEmbeddedView = searchParams.get('view');
  // Gate / action / refinement context — set by useWorkflowStart
  // e.g. /chat?workflow=AppGenerator&context={"app_type":"new"}&trigger_source=gate
  const queryContextRaw = searchParams.get('context');
  const queryContext = (() => {
    if (!queryContextRaw) return null;
    try { return JSON.parse(queryContextRaw); } catch { return null; }
  })();
  const queryTriggerSource = searchParams.get('trigger_source') || 'chat';
  const queryActionId = searchParams.get('action_id') || null;
  const queryChangeClass = searchParams.get('change_class') || null;
  const queryArtifactVersionId = searchParams.get('artifact_version_id') || null;
  // Page context forwarded by the persistent widget when navigating from a non-chat route
  const queryPageContext = searchParams.get('page_context') || null;

  useEffect(() => {
    if (!queryWorkflowName) {
      return;
    }
    if (queryMode === 'workflow' || queryMode === 'ask') {
      return;
    }
    const nextParams = new URLSearchParams(location.search || '');
    nextParams.set('mode', 'workflow');
    navigate(`${location.pathname}?${nextParams.toString()}`, { replace: true });
  }, [location.pathname, location.search, navigate, queryMode, queryWorkflowName]);

  const appId = pathAppId || queryAppId;
  const urlWorkflowName = pathWorkflowName || queryWorkflowName;
  const {
    user,
    api,
    auth,
    config,
    logout,
    activeChatId,
    setActiveChatId,
    activeWorkflowName,
    setActiveWorkflowName,
    setChatMinimized,
    layoutMode,
    setLayoutMode,
    isArtifactOpen,
    setIsArtifactOpen,
    widgetOverlayOpen,
    setWidgetOverlayOpen,
    dispatchSurfaceEvent,
    dispatchSurfaceAction,
    currentArtifactContext,
    setCurrentArtifactContext,
    isInWidgetMode,
    setIsInWidgetMode,
    conversationMode,
    setConversationMode,
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
    surfaceState,
  } = useChatUI();
  const surfaceStateRef = useRef(surfaceState);
  useEffect(() => {
    surfaceStateRef.current = surfaceState;
  }, [surfaceState]);
  const conversationModeRef = useRef(conversationMode);
  useEffect(() => {
    conversationModeRef.current = conversationMode;
  }, [conversationMode]);
  const isSidePanelOpen = isArtifactOpen;
  const setIsSidePanelOpen = setIsArtifactOpen;
  const [forceOverlay, setForceOverlay] = useState(false);
  const [widgetChatMinimized, setWidgetChatMinimized] = useState(false);
  const [isAskHistoryDrawerOpen, setIsAskHistoryDrawerOpen] = useState(false);
  const [pendingTransitionId, _setPendingTransitionId] = useState(null);
  const pendingTransitionIdRef = useRef(null);
  const setPendingTransitionId = useCallback((id) => {
    pendingTransitionIdRef.current = id;
    _setPendingTransitionId(id);
  }, []);
  const [pendingTransitionContext, setPendingTransitionContext] = useState({});
  
  // Mobile-specific state
  const [isMobileView, setIsMobileView] = useState(false);
  const [mobileDrawerState, setMobileDrawerState] = useState('peek'); // 'hidden' | 'peek' | 'expanded'
  const [hasUnseenChat, setHasUnseenChat] = useState(false);
  const [hasUnseenArtifact, setHasUnseenArtifact] = useState(false);
  const [actionStatusMap, setActionStatusMap] = useState({});
  const [pendingWorkflowReply, setPendingWorkflowReply] = useState(null);
  const [workflowCompleted, setWorkflowCompleted] = useState(false);
  const [, setCompletionData] = useState(null);
  const [pendingHarnessDecision, setPendingHarnessDecision] = useState(null);
  const [pendingHarnessDecisionError, setPendingHarnessDecisionError] = useState(null);
  const optimisticSnapshotsRef = useRef(new Map());
  
  // Current artifact messages rendered inside ArtifactPanel (not in chat messages)
  const [currentArtifactMessages, setCurrentArtifactMessages] = useState([]);
  const currentArtifactMessagesRef = useRef([]);
  useEffect(() => {
    currentArtifactMessagesRef.current = currentArtifactMessages;
  }, [currentArtifactMessages]);
  const navTriggerRef = useRef(null);
  const navCacheContextRef = useRef(null);
  const embeddedViewHandledRef = useRef(false);
  const viewArtifactSnapshotRef = useRef(null);
  // Track the most recent artifact-mode UI event id to manage auto-collapse
  const lastArtifactEventRef = useRef(null);
  // Prevent duplicate restores per connection
  const artifactRestoredOnceRef = useRef(false);
  const artifactCacheValidRef = useRef(false);
  const lastErrorIdRef = useRef(null); // Track last error to prevent duplicates
  const workflowMessagesCacheRef = useRef([]);
  const workflowReplayPendingRef = useRef(false);
  const workflowMessagesSharedRef = useRef([]);
  const generalMessagesCacheRef = useRef([]);
  const layoutModeForConversation = conversationMode === 'ask' ? 'full' : layoutMode;
  // Respect explicit layout state in workflow mode; force full in ask mode.
  const effectiveLayoutMode = conversationMode === 'ask' ? 'full' : layoutMode;
  const isViewMode = effectiveLayoutMode === 'view';
  const mainPaddingClass = 'pt-14 md:pt-16';
  const mainContentStyle = isMobileView
    ? { paddingTop: 'calc(env(safe-area-inset-top, 0px) + 3.5rem)' }
    : undefined;
  const chatPageShellStyle = isMobileView
    ? { height: '100vh', minHeight: '100dvh' }
    : undefined;

  const defaultWorkflow = resolveWorkflow(urlWorkflowName) || '';
  const [currentWorkflowName, setCurrentWorkflowName] = useState(defaultWorkflow);
  const currentWorkflowNameRef = useRef(defaultWorkflow);

  useEffect(() => {
    currentWorkflowNameRef.current = currentWorkflowName || activeWorkflowName || urlWorkflowName || '';
  }, [activeWorkflowName, currentWorkflowName, urlWorkflowName]);

  const normalizeComparableText = (value) => String(value || '').replace(/\s+/g, ' ').trim();

  const shouldSuppressHiddenWorkflowMessage = useCallback((message) => {
    if (!message || typeof message !== 'object') {
      return false;
    }

    const directSeedKind = message._mozaiks_seed_kind || message.metadata?._mozaiks_seed_kind;
    if (directSeedKind === 'initial_message' || directSeedKind === 'userdriven_trigger') {
      return true;
    }

    const workflowName = String(
      currentWorkflowNameRef.current || currentWorkflowName || activeWorkflowName || urlWorkflowName || ''
    ).trim();
    if (!workflowName || !workflowConfig?.getWorkflowConfig) {
      return false;
    }

    const wfCfg = workflowConfig.getWorkflowConfig(workflowName);
    const startupMode = String(wfCfg?.startup_mode || '').trim().toLowerCase();
    if (startupMode === 'userdriven') {
      return false;
    }

    const hiddenInitialMessage = normalizeComparableText(wfCfg?.initial_message);
    const content = normalizeComparableText(message.content);
    if (!hiddenInitialMessage || !content || content !== hiddenInitialMessage) {
      return false;
    }

    const metadata = message.metadata && typeof message.metadata === 'object'
      ? message.metadata
      : null;
    const normalizedSender = String(
      message.sender
      || message.agent
      || message.agentName
      || message.agent_name
      || ''
    ).trim().toLowerCase();
    const normalizedRole = String(message.role || '').trim().toLowerCase();
    const source = String(metadata?.source || '').trim().toLowerCase();

    return normalizedSender === 'user'
      || normalizedRole === 'user'
      || source === 'workflow_user'
      || Boolean(metadata?.input_request_id);
  }, [activeWorkflowName, currentWorkflowName, urlWorkflowName, workflowConfig]);

  const sanitizeVisibleWorkflowMessages = useCallback((messageList) => {
    if (!Array.isArray(messageList) || messageList.length === 0) {
      return [];
    }
    return messageList.filter((message) => !shouldSuppressHiddenWorkflowMessage(message));
  }, [shouldSuppressHiddenWorkflowMessage]);

  const isAskModeWorkflowLeak = useCallback((event) => {
    if (conversationMode !== 'ask' || !event || typeof event !== 'object') {
      return false;
    }

    const metadata = event.metadata && typeof event.metadata === 'object'
      ? event.metadata
      : event.data?.metadata && typeof event.data?.metadata === 'object'
        ? event.data.metadata
        : {};
    const source = String(metadata?.source || '').trim().toLowerCase();

    if (source === 'general_agent') {
      return false;
    }

    const normalizedAgentName = String(
      event.agentName
      || event.agent
      || event.agent_name
      || ''
    ).trim().toLowerCase();

    const sender = String(
      event.sender
      || event.agent
      || event.agent_name
      || event.agentName
      || ''
    ).trim().toLowerCase();
    const role = String(event.role || '').trim().toLowerCase();
    const seedKind = String(
      event._mozaiks_seed_kind
      || metadata?._mozaiks_seed_kind
      || ''
    ).trim().toLowerCase();

    if (seedKind === 'initial_message' || seedKind === 'userdriven_trigger') {
      return true;
    }

    if (source === 'workflow_user') {
      return true;
    }

    if (Boolean(metadata?.input_request_id)) {
      return true;
    }

    if (source && source !== 'general_agent' && sender !== 'system' && role !== 'system') {
      return true;
    }

    if (!source && normalizedAgentName.endsWith('agent') && normalizedAgentName !== 'assistant') {
      return true;
    }

    return sender === 'user' || role === 'user';
  }, [conversationMode]);

  const sanitizeVisibleAskMessages = useCallback((messageList) => {
    if (!Array.isArray(messageList) || messageList.length === 0) {
      return [];
    }
    return messageList.filter((message) => !isAskModeWorkflowLeak(message));
  }, [isAskModeWorkflowLeak]);

  const resolveKnownWorkflowName = useCallback((preferredWorkflow = null) => {
    const explicitKnownWorkflow = workflowConfig.resolveKnownWorkflowName(preferredWorkflow);
    if (explicitKnownWorkflow) {
      return explicitKnownWorkflow;
    }
    return resolveWorkflow() || workflowConfig.getDefaultWorkflow() || null;
  }, []);

  const restoreStoredArtifactForChat = useCallback((chatId, fallbackWorkflowName = null) => {
    if (!chatId) {
      return false;
    }

    try {
      const cachedCurrent = readStoredCurrentArtifact(chatId);
      const cachedLast = readStoredLastArtifact(chatId);
      const cached = cachedCurrent || cachedLast;
      if (!cached?.tool_name) {
        return false;
      }

      const shouldOpen = getStoredArtifactPanelOpen(chatId);
        const restoredMsg = {
          id: `ui-restored-${Date.now()}`,
          sender: 'agent',
          agentName: cached.payload?.agentName || cached.payload?.agent_name || cached.agentName || cached.agent_name || 'Agent',
          content: cached.payload?.structured_output || cached.payload || {},
          isStreaming: false,
          toolCall: {
            tool_name: cached.tool_name,
            payload: cached.payload || {},
            tool_call_id: cached.tool_call_id || null,
            workflow_name: cached.workflow_name || fallbackWorkflowName || currentWorkflowName,
            onResponse: undefined,
            display: cached.display || 'artifact',
            restored: true,
          },
        };

      setCurrentArtifactMessages([restoredMsg]);
      lastArtifactEventRef.current = cached.tool_call_id || restoredMsg.id;
      artifactCacheValidRef.current = true;
      artifactRestoredOnceRef.current = true;

      if (shouldOpen !== false) {
        setIsSidePanelOpen(true);
        if (setLayoutMode) {
          setLayoutMode('split');
        }
      }

      return true;
    } catch (e) {
      console.warn('[RESTORE] Failed to restore artifact from stored session:', e);
      return false;
    }
  }, [currentWorkflowName, setIsSidePanelOpen, setLayoutMode]);

  useEffect(() => {
    if (typeof setCurrentArtifactContext !== 'function') {
      return;
    }
    if (!currentArtifactMessages || currentArtifactMessages.length === 0) {
      setCurrentArtifactContext(null);
      return;
    }
    const artifactMsg = currentArtifactMessages.find(m => m?.toolCall?.tool_name) || currentArtifactMessages[0];
    const toolCall = artifactMsg?.toolCall;
    if (!toolCall) {
      setCurrentArtifactContext(null);
      return;
    }
    setCurrentArtifactContext({
      id: toolCall.tool_call_id || artifactMsg.id || null,
      type: toolCall.tool_name,
      payload: toolCall.payload || null,
      artifact_id: deriveArtifactId(toolCall.payload, toolCall.tool_call_id || artifactMsg.id || null),
      chat_id: currentChatId,
      workflow_name: currentWorkflowName,
    });
  }, [currentArtifactMessages, currentChatId, currentWorkflowName, setCurrentArtifactContext]);

  useEffect(() => {
    if (!currentChatId || conversationMode !== 'workflow') {
      return;
    }
    setStoredArtifactPanelOpen(currentChatId, isSidePanelOpen);
  }, [currentChatId, conversationMode, isSidePanelOpen]);

  useEffect(() => {
    if (!currentChatId) return;
    if (!Array.isArray(currentArtifactMessages) || currentArtifactMessages.length === 0) return;
    const artifactMsg = currentArtifactMessages.find(m => m?.toolCall?.payload) || currentArtifactMessages[0];
    const toolCall = artifactMsg?.toolCall;
    if (!toolCall) return;

    const payload = toolCall.payload || {};
    const displayMode = toolCall.display || payload.display || payload.mode || 'artifact';
    if (displayMode !== 'artifact') return;

    const artifactPayload = {
      ...payload,
      artifact_id: deriveArtifactId(payload, toolCall.tool_call_id || artifactMsg?.id || null),
    };

    try {
      const serializableArtifact = {
        ...artifactMsg,
        toolCall: {
          ...toolCall,
          payload: artifactPayload,
          onResponse: null,
        },
      };
      writeStoredCurrentArtifact(currentChatId, serializableArtifact);

      writeStoredLastArtifact(currentChatId, {
        tool_name: toolCall.tool_name || 'core.state',
        tool_call_id: toolCall.tool_call_id || null,
        workflow_name: toolCall.workflow_name || currentWorkflowName,
        payload: artifactPayload,
        display: displayMode || 'artifact',
        ts: Date.now(),
      });
      artifactCacheValidRef.current = true;
    } catch (e) {
      console.warn('Failed to persist artifact cache', e);
    }

    try {
      const navCache = navCacheContextRef.current;
      if (navCache?.cache_ttl && artifactPayload) {
        const artifactWorkflow = toolCall.workflow_name || currentWorkflowName;
        if (!navCache.workflow || navCache.workflow === artifactWorkflow) {
          const cacheWorkflow = navCache.workflow || artifactWorkflow;
          if (cacheWorkflow) {
            writeNavigationCache(
              cacheWorkflow,
              navCache?.input ?? null,
              {
                tool_name: toolCall.tool_name || 'core.state',
                tool_call_id: toolCall.tool_call_id || null,
                workflow_name: cacheWorkflow,
                payload: artifactPayload,
                display: displayMode || 'artifact',
              },
              navCache.cache_ttl,
            );
          }
        }
      }
    } catch (e) {
      console.warn('Failed to update nav-trigger cache', e);
    }
  }, [currentArtifactMessages, currentChatId, currentWorkflowName]);
  const generalHydrationPendingRef = useRef(false);
  const askModeSyncedChatRef = useRef(null);
  const workflowArtifactSnapshotRef = useRef({ isOpen: false, messages: [], layoutMode: 'split' });
  const queryResumeHandledRef = useRef(null);
  const validatedChatIdRef = useRef(null);
  const validatingChatIdRef = useRef(false);

  useEffect(() => {
    const sanitizedWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessages);
    workflowMessagesSharedRef.current = sanitizedWorkflowMessages;
    if (Array.isArray(workflowMessages) && sanitizedWorkflowMessages.length !== workflowMessages.length) {
      setWorkflowMessages(sanitizedWorkflowMessages);
    }
  }, [sanitizeVisibleWorkflowMessages, setWorkflowMessages, workflowMessages]);
  
  useEffect(() => {
    if (conversationMode === 'workflow') {
      // Resume clears the visible transcript briefly while the backend replays.
      // Keep the last shared workflow snapshot intact during that window.
      if (!(workflowReplayPendingRef.current && messages.length === 0)) {
        const sanitizedMessages = sanitizeVisibleWorkflowMessages(messages);
        workflowMessagesCacheRef.current = sanitizedMessages;
        workflowMessagesSharedRef.current = sanitizedMessages;
        setWorkflowMessages(sanitizedMessages);
      }
    } else {
      generalMessagesCacheRef.current = sanitizeVisibleAskMessages(messages);
    }
    if (conversationMode === 'ask') {
      setAskMessages(sanitizeVisibleAskMessages(messages));
    }
  }, [messages, conversationMode, sanitizeVisibleAskMessages, sanitizeVisibleWorkflowMessages, setAskMessages, setWorkflowMessages]);

  useEffect(() => {
    if (conversationMode !== 'ask') {
      return;
    }
    if (!Array.isArray(messages) || messages.length === 0) {
      return;
    }

    const sanitizedMessages = sanitizeVisibleAskMessages(messages);
    if (sanitizedMessages.length === messages.length) {
      return;
    }

    generalMessagesCacheRef.current = sanitizedMessages;
    setAskMessages(sanitizedMessages);
    setMessagesWithLogging(sanitizedMessages);
  }, [conversationMode, messages, sanitizeVisibleAskMessages, setAskMessages, setMessagesWithLogging]);

  useEffect(() => {
    if (conversationMode !== 'workflow') {
      return;
    }
    if (!Array.isArray(messages) || messages.length === 0) {
      return;
    }

    const sanitizedMessages = sanitizeVisibleWorkflowMessages(messages);
    if (sanitizedMessages.length === messages.length) {
      return;
    }

    workflowMessagesCacheRef.current = sanitizedMessages;
    workflowMessagesSharedRef.current = sanitizedMessages;
    setWorkflowMessages(sanitizedMessages);
    setMessagesWithLogging(sanitizedMessages);
  }, [conversationMode, messages, sanitizeVisibleWorkflowMessages, setMessagesWithLogging, setWorkflowMessages]);
  
  // Seed a page-context system message when navigating from the widget on a known page
  useEffect(() => {
    if (!queryPageContext || queryMode !== 'ask') return;
    const seenKey = `mozaiks.page_ctx_seeded:${queryPageContext.slice(0, 40)}`;
    try { if (sessionStorage.getItem(seenKey)) return; } catch {}
    setMessagesWithLogging((prev) => {
      if (prev.length > 0) return prev; // already has messages; don't prepend
      return [{
        id: `page-ctx-${Date.now()}`,
        sender: 'system',
        agentName: 'System',
        content: `📍 **Page context:** ${queryPageContext}`,
        isStreaming: false,
        metadata: { hideInTranscript: false, source: 'page_context' },
      }];
    });
    try { sessionStorage.setItem(seenKey, '1'); } catch {}
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryPageContext, queryMode]);

  // Note: Artifact panel restoration is handled directly in ensureWorkflowMode with setTimeout
  const implicitDevAppId =
    process.env.NODE_ENV !== 'production' &&
    process.env.REACT_APP_ALLOW_IMPLICIT_APP_ID === 'true'
      ? 'local-dev'
      : null;

  const currentAppId =
    appId ||
    config?.chat?.defaultAppId ||
    navContext?.appId ||
    config?.appId ||
    config?.app_id ||
    process.env.REACT_APP_DEFAULT_APP_ID ||
    process.env.REACT_APP_DEFAULT_app_id ||
    implicitDevAppId ||
    null;
  const { theme: chatTheme, loading: themeLoading } = useTheme(currentAppId);
  const brandLogoSrc = getBrandLogoSrc(chatTheme);
  const chatBackgroundSrc = getChatBackgroundSrc(chatTheme);
  const resolvedAppDisplayName = String(
    navContext?.appName ||
    config?.appName ||
    config?.app_name ||
    chatTheme?.branding?.name ||
    'MozaiksAI'
  ).trim() || 'MozaiksAI';
  const currentUserId = user?.id || user?.user_id || user?.sub || getUserIdFromToken() || 'anonymous';
  const {
    startWorkflow: startPendingHarnessWorkflow,
    starting: pendingHarnessDecisionBusy,
    error: pendingHarnessWorkflowStartError,
  } = useWorkflowStart();
  const buildPendingHarnessDecision = useCallback((decision, fallback = null) => {
    const source = decision && typeof decision === 'object' ? decision : null;
    const base = fallback && typeof fallback === 'object' ? fallback : {};
    const decisionId = String(
      source?.decision_id
      || base.decision_id
      || base.change_request_id
      || base.revision_id
      || ''
    ).trim();
    const decisionType = String(source?.decision_type || base.decision_type || '').trim();
    const message = String(source?.message || base.message || '').trim();
    const rationale = String(source?.rationale || base.rationale || '').trim();
    if (!decisionId || !decisionType || !message || !rationale) {
      return null;
    }

    const normalizeAction = (action) => {
      if (!action || typeof action !== 'object') {
        return null;
      }
      const actionId = String(action.action_id || '').trim();
      const label = String(action.label || '').trim();
      if (!actionId || !label) {
        return null;
      }
      return {
        action_id: actionId,
        label,
        action_type: String(action.action_type || 'run_workflow').trim() || 'run_workflow',
        workflow_id: String(action.workflow_id || '').trim() || null,
        metadata: action.metadata && typeof action.metadata === 'object' ? { ...action.metadata } : {},
      };
    };

    const actions = (
      Array.isArray(source?.actions)
        ? source.actions
        : Array.isArray(base.actions)
          ? base.actions
          : []
    )
      .map(normalizeAction)
      .filter(Boolean);

    const selectedPaths = (
      Array.isArray(source?.selected_paths)
        ? source.selected_paths
        : Array.isArray(base.selected_paths)
          ? base.selected_paths
          : []
    )
      .map((path) => String(path || '').trim())
      .filter(Boolean);

    return {
      decision_id: decisionId,
      decision_type: decisionType,
      message,
      rationale,
      confidence: Number(source?.confidence ?? base.confidence ?? 0) || 0,
      recommended_workflow_id: String(
        source?.recommended_workflow_id
        || base.recommended_workflow_id
        || ''
      ).trim() || null,
      selected_paths: selectedPaths,
      clarification_question: String(
        source?.clarification_question
        || base.clarification_question
        || ''
      ).trim() || null,
      change_request_id: String(
        source?.change_request_id
        || base.change_request_id
        || ''
      ).trim() || null,
      revision_id: String(source?.revision_id || base.revision_id || '').trim() || null,
      requires_confirmation: Boolean(
        source?.requires_confirmation ?? base.requires_confirmation ?? false
      ),
      trigger_source: String(source?.trigger_source || base.trigger_source || 'refinement').trim() || 'refinement',
      requested_workflow_id: String(
        source?.requested_workflow_id
        || base.requested_workflow_id
        || ''
      ).trim() || null,
      journey_id: String(source?.journey_id || base.journey_id || '').trim() || null,
      context_variables: source?.context_variables && typeof source.context_variables === 'object'
        ? { ...source.context_variables }
        : base.context_variables && typeof base.context_variables === 'object'
          ? { ...base.context_variables }
          : {},
      trigger_payload: source?.trigger_payload && typeof source.trigger_payload === 'object'
        ? { ...source.trigger_payload }
        : base.trigger_payload && typeof base.trigger_payload === 'object'
          ? { ...base.trigger_payload }
          : {},
      actions,
      metadata: source?.metadata && typeof source.metadata === 'object'
        ? { ...source.metadata }
        : base.metadata && typeof base.metadata === 'object'
          ? { ...base.metadata }
          : {},
    };
  }, []);
  const applySessionStatePendingHarnessDecision = useCallback((sessionState) => {
    const nextDecision = buildPendingHarnessDecision(sessionState?.pending_harness_decision);
    setPendingHarnessDecision(nextDecision);
    if (!nextDecision) {
      setPendingHarnessDecisionError(null);
    }
  }, [buildPendingHarnessDecision]);
  const handlePendingHarnessDecisionAction = useCallback(async (action) => {
    if (!pendingHarnessDecision || !action) {
      return;
    }

    setPendingHarnessDecisionError(null);
    const workflowId = action.workflow_id
      || pendingHarnessDecision.recommended_workflow_id
      || pendingHarnessDecision.requested_workflow_id
      || currentWorkflowName
      || null;
    const contextVariables = {
      ...(pendingHarnessDecision.context_variables || {}),
    };
    const triggerPayload = {
      ...(pendingHarnessDecision.trigger_payload || {}),
      harness_action: {
        action_id: action.action_id,
      },
    };
    if (pendingHarnessDecision.change_request_id && !triggerPayload.change_request_id) {
      triggerPayload.change_request_id = pendingHarnessDecision.change_request_id;
    }
    if (pendingHarnessDecision.revision_id && !triggerPayload.revision_id) {
      triggerPayload.revision_id = pendingHarnessDecision.revision_id;
    }

    const result = await startPendingHarnessWorkflow(workflowId, contextVariables, {
      trigger_source: pendingHarnessDecision.trigger_source || 'refinement',
      journey_id: pendingHarnessDecision.journey_id || null,
      app_id: currentAppId || null,
      user_id: currentUserId || null,
      trigger_payload: triggerPayload,
    });

    if (!result) {
      setPendingHarnessDecisionError(
        pendingHarnessWorkflowStartError || 'Failed to continue this harness decision.'
      );
      return;
    }

    if (result.execution_mode === 'harness_decision') {
      const nextDecision = buildPendingHarnessDecision(
        result.harness_decision,
        {
          ...pendingHarnessDecision,
          trigger_source: pendingHarnessDecision.trigger_source || 'refinement',
          requested_workflow_id: workflowId,
          recommended_workflow_id: result.workflow_id || workflowId || pendingHarnessDecision.recommended_workflow_id,
          journey_id: pendingHarnessDecision.journey_id || null,
          context_variables: contextVariables,
          trigger_payload: triggerPayload,
          change_request_id: triggerPayload.change_request_id || pendingHarnessDecision.change_request_id,
          revision_id: triggerPayload.revision_id || pendingHarnessDecision.revision_id,
        },
      );
      setPendingHarnessDecision(nextDecision);
      setPendingHarnessDecisionError(null);
      return;
    }

    setPendingHarnessDecision(null);
    setPendingHarnessDecisionError(null);
  }, [
    buildPendingHarnessDecision,
    currentAppId,
    currentUserId,
    currentWorkflowName,
    pendingHarnessDecision,
    pendingHarnessWorkflowStartError,
    startPendingHarnessWorkflow,
  ]);
  useEffect(() => {
    if (pendingHarnessWorkflowStartError) {
      setPendingHarnessDecisionError(pendingHarnessWorkflowStartError);
    }
  }, [pendingHarnessWorkflowStartError]);
  const consumeNavigationQueryParams = useCallback((keys = []) => {
    if (!Array.isArray(keys) || keys.length === 0) {
      return;
    }

    const params = new URLSearchParams(location.search || '');
    let changed = false;
    for (const key of keys) {
      if (params.has(key)) {
        params.delete(key);
        changed = true;
      }
    }

    if (!changed) {
      return;
    }

    const nextSearch = params.toString();
    navigate(`${location.pathname}${nextSearch ? `?${nextSearch}` : ''}`, { replace: true });
  }, [location.pathname, location.search, navigate]);
  const resolveNavMode = (mode) => {
    const normalized = typeof mode === 'string' ? mode.toLowerCase() : 'workflow';
    if (normalized === 'view' || normalized === 'ask' || normalized === 'workflow') {
      return normalized;
    }
    return 'workflow';
  };
  const resolveNavLayoutMode = (mode) => {
    if (mode === 'view') return 'view';
    if (mode === 'ask') return 'full';
    return 'split';
  };
  // CRITICAL: Clear widget mode immediately when ChatPage mounts on a primary chat route.
  // This must happen regardless of connection status so the UI doesn't show stale widget state.
  useEffect(() => {
    if (isPrimaryChatRoute && isInWidgetMode) {
      setIsInWidgetMode(false);
    }
  }, [isPrimaryChatRoute, isInWidgetMode, setIsInWidgetMode]);

  useEffect(() => {
    if (urlWorkflowName && urlWorkflowName !== currentWorkflowName) {
      setCurrentWorkflowName(urlWorkflowName);
    }
  }, [urlWorkflowName, currentWorkflowName]);

  useEffect(() => {
    if (!workflowConfigLoaded) {
      return;
    }

    const nextWorkflowName =
      workflowConfig.resolveKnownWorkflowName(urlWorkflowName)
      || workflowConfig.resolveKnownWorkflowName(currentWorkflowName)
      || workflowConfig.resolveKnownWorkflowName(activeWorkflowName)
      || workflowConfig.resolveKnownWorkflowName(getStoredActiveWorkflowName())
      || resolveWorkflow()
      || workflowConfig.getDefaultWorkflow()
      || null;

    if (!nextWorkflowName) {
      return;
    }

    if (nextWorkflowName !== currentWorkflowName) {
      setCurrentWorkflowName(nextWorkflowName);
    }
    if (nextWorkflowName !== activeWorkflowName) {
      setActiveWorkflowName(nextWorkflowName);
    }
  }, [workflowConfigLoaded, urlWorkflowName, currentWorkflowName, activeWorkflowName, setActiveWorkflowName]);

  useEffect(() => {
    if (!pendingNavigationTrigger) return;
    const trigger = pendingNavigationTrigger;
    const triggerId = trigger?.id || `${trigger?.workflow || 'nav'}-${trigger?.requested_at || Date.now()}`;
    if (navTriggerRef.current === triggerId) return;
    navTriggerRef.current = triggerId;

    const resolvedMode = resolveNavMode(trigger?.mode);
    const nextLayoutMode = resolveNavLayoutMode(resolvedMode);
    if (layoutMode !== nextLayoutMode) {
      setLayoutMode(nextLayoutMode);
    }
    if (resolvedMode === 'ask' && conversationMode !== 'ask') {
      setConversationMode('ask');
    }
    if (resolvedMode !== 'ask' && conversationMode !== 'workflow') {
      setConversationMode('workflow');
    }

    const nextWorkflow = trigger?.workflow || resolveWorkflow() || currentWorkflowName;
    if (nextWorkflow && nextWorkflow !== currentWorkflowName) {
      setCurrentWorkflowName(nextWorkflow);
    }
    if (nextWorkflow) {
      setActiveWorkflowName(nextWorkflow);
    }

    try {
      localStorage.removeItem(LOCAL_STORAGE_KEY);
    } catch {}
    pendingStartRef.current = false;
    connectionInProgressRef.current = false;
    setConnectionInitialized(false);
    setConnectionStatus('disconnected');
    setLoading(true);
    if (wsRef.current && typeof wsRef.current.close === 'function') {
      wsRef.current.close();
    }
    wsRef.current = null;
    setWs(null);
    setCurrentChatId(null);
    setActiveChatId(null);

    setMessagesWithLogging([]);
    setCurrentArtifactMessages([]);
    setWorkflowCompleted(false);
    setCompletionData(null);
    lastArtifactEventRef.current = null;
    artifactRestoredOnceRef.current = false;
    artifactCacheValidRef.current = false;

    navCacheContextRef.current = null;
    if (trigger?.cache_ttl && nextWorkflow) {
      navCacheContextRef.current = {
        workflow: nextWorkflow,
        input: trigger?.input ?? null,
        cache_ttl: trigger.cache_ttl,
      };
      const cached = readNavigationCache(nextWorkflow, trigger?.input ?? null);
      if (cached?.artifact) {
        const cachedEvent = cached.artifact;
        const cachedPayload = {
          ...(cachedEvent?.payload || {}),
          artifact_id: deriveArtifactId(cachedEvent?.payload || {}, cachedEvent?.tool_call_id || null),
        };
        const artifactMsg = {
          id: `nav-cache-${Date.now()}`,
          sender: 'agent',
          agentName: cachedEvent?.agentName || cachedEvent?.agent_name || 'Agent',
          content: cachedPayload.structured_output || cachedPayload.content || cachedPayload || {},
          isStreaming: false,
          toolCall: {
            tool_name: cachedEvent?.tool_name || cachedEvent?.tool || 'core.cached',
            payload: cachedPayload,
            tool_call_id: cachedEvent?.tool_call_id || null,
            workflow_name: cachedEvent?.workflow_name || nextWorkflow,
            onResponse: null,
            display: cachedEvent?.display || 'artifact',
          }
        };
        setCurrentArtifactMessages([artifactMsg]);
        setIsSidePanelOpen(true);
        artifactCacheValidRef.current = true;
      }
    }

    if (typeof setPendingNavigationTrigger === 'function') {
      setPendingNavigationTrigger(null);
    }
  }, [
    pendingNavigationTrigger,
    currentWorkflowName,
    layoutMode,
    conversationMode,
    setLayoutMode,
    setConversationMode,
    setCurrentWorkflowName,
    setActiveWorkflowName,
    setConnectionInitialized,
    setConnectionStatus,
    setLoading,
    setWs,
    setCurrentChatId,
    setActiveChatId,
    setMessagesWithLogging,
    setCurrentArtifactMessages,
    setWorkflowCompleted,
    setCompletionData,
    setIsSidePanelOpen,
    setPendingNavigationTrigger,
  ]);

  // One-time initial spinner: show after websocket connects, hide after first agent chat.text message
  const [showInitSpinner, setShowInitSpinner] = useState(false);
  // Refs for race-free spinner control
  const initSpinnerShownRef = useRef(false);
  const initSpinnerHiddenOnceRef = useRef(false);
  // Removed dynamic UI accumulation & dedupe refs (no longer needed with chat.* events)


  // Helper function to extract agent name from nested message structure
  const extractAgentName = useCallback((data) => {
    const asStr = (v) => (v && typeof v === 'string' && v.trim() && v !== 'Unknown') ? v.trim() : null;
    try {
      return (
        asStr(data.agent) ||
        asStr(data.agentName) ||
        asStr(data.agent_name) ||
        (() => {
          if (data.content && typeof data.content === 'string') {
            const parsed = JSON.parse(data.content);
            return asStr(parsed?.data?.content?.sender) || asStr(parsed?.data?.agent);
          }
          return null;
        })() ||
        'Agent'
      );
    } catch {
      return asStr(data.agent) || asStr(data.agent_name) || 'Agent';
    }
  }, []);

  const shouldSuppressConsecutiveAssistantDuplicate = useCallback((messageList, { agentName, content, source } = {}) => {
    if (source !== 'general_agent') {
      return false;
    }

    const normalizedContent = String(content || '').trim();
    if (!normalizedContent || !Array.isArray(messageList) || messageList.length === 0) {
      return false;
    }

    for (let i = messageList.length - 1; i >= 0; i -= 1) {
      const candidate = messageList[i];
      if (!candidate || candidate.isThinking) {
        continue;
      }
      if (candidate.sender === 'system') {
        continue;
      }
      if (candidate.sender === 'user') {
        return false;
      }
      if (candidate.__streaming) {
        return false;
      }

      const candidateContent = String(candidate.content || '').trim();
      const candidateSource = candidate.metadata?.source || null;
      return candidate.sender === 'agent'
        && candidate.agentName === agentName
        && candidateContent === normalizedContent
        && candidateSource === source;
    }

    return false;
  }, []);

  const getSeedKindFromEvent = useCallback((eventData) => {
    if (!eventData || typeof eventData !== 'object') {
      return null;
    }
    const directSeedKind = eventData._mozaiks_seed_kind || eventData.data?._mozaiks_seed_kind;
    if (typeof directSeedKind === 'string' && directSeedKind.trim()) {
      return directSeedKind.trim();
    }
    const metadata = eventData.metadata && typeof eventData.metadata === 'object'
      ? eventData.metadata
      : eventData.data?.metadata && typeof eventData.data.metadata === 'object'
        ? eventData.data.metadata
        : null;
    const metadataSeedKind = metadata?._mozaiks_seed_kind;
    return typeof metadataSeedKind === 'string' && metadataSeedKind.trim()
      ? metadataSeedKind.trim()
      : null;
  }, []);

  const normalizeSeedComparableText = useCallback((value) => {
    return normalizeComparableText(value);
  }, []);

  const shouldSuppressLegacyHiddenInitialReplay = useCallback((eventData) => {
    if (!eventData || typeof eventData !== 'object') {
      return false;
    }

    if (!(eventData.replay || eventData.data?.replay)) {
      return false;
    }

    const workflowName = String(
      currentWorkflowNameRef.current || currentWorkflowName || activeWorkflowName || urlWorkflowName || ''
    ).trim();
    if (!workflowName || !workflowConfig?.getWorkflowConfig) {
      return false;
    }

    const wfCfg = workflowConfig.getWorkflowConfig(workflowName);
    const startupMode = String(wfCfg?.startup_mode || '').trim().toLowerCase();
    if (startupMode === 'userdriven') {
      return false;
    }

    const hiddenInitialMessage = normalizeSeedComparableText(wfCfg?.initial_message);
    if (!hiddenInitialMessage) {
      return false;
    }

    const content = normalizeSeedComparableText(eventData.content || eventData.data?.content);
    if (!content || content !== hiddenInitialMessage) {
      return false;
    }

    const metadata = eventData.metadata && typeof eventData.metadata === 'object'
      ? eventData.metadata
      : eventData.data?.metadata && typeof eventData.data.metadata === 'object'
        ? eventData.data.metadata
        : null;
    const normalizedAgent = String(
      eventData.agent
      || eventData.agent_name
      || eventData.sender
      || eventData.data?.agent
      || eventData.data?.agent_name
      || eventData.data?.sender
      || ''
    ).trim().toLowerCase();
    const normalizedRole = String(eventData.role || eventData.data?.role || '').trim().toLowerCase();
    const source = String(metadata?.source || '').trim().toLowerCase();

    return normalizedAgent === 'user'
      || normalizedRole === 'user'
      || source === 'workflow_user'
      || Boolean(metadata?.input_request_id);
  }, [activeWorkflowName, currentWorkflowName, normalizeSeedComparableText, urlWorkflowName, workflowConfig]);

  const shouldSuppressHiddenSeedEvent = useCallback((eventData) => {
    const seedKind = getSeedKindFromEvent(eventData);
    return seedKind === 'initial_message'
      || seedKind === 'userdriven_trigger'
      || shouldSuppressLegacyHiddenInitialReplay(eventData);
  }, [getSeedKindFromEvent, shouldSuppressLegacyHiddenInitialReplay]);

  const {
    generalSessionsLoading,
    workflowSessionsLoading,
    refreshGeneralSessions,
    refreshWorkflowSessions,
    handleRefreshGeneralSessions,
    handleClearGeneralSessions,
    handleDeleteGeneralSession,
    handleRefreshWorkflowSessions,
    handleClearWorkflowSessions,
  } = useChatSessionHistory({
    api,
    currentAppId,
    currentUserId,
    conversationMode,
    workflowSessions,
    generalMessagesCacheRef,
    setGeneralChatSessions,
    setWorkflowSessions,
    setActiveGeneralChatId,
    setGeneralChatSummary,
    setMessagesWithLogging,
    setCurrentArtifactMessages,
  });

  const mapGeneralMessage = useCallback((message) => {
    if (!message) {
      return null;
    }
    const timestamp = (() => {
      if (!message.timestamp) return Date.now();
      try {
        return new Date(message.timestamp).getTime();
      } catch (_) {
        return Date.now();
      }
    })();
    return {
      id: message.event_id || `general-${message.sequence || Date.now()}`,
      sender: message.role === 'assistant' ? 'agent' : 'user',
      agentName: message.role === 'assistant' ? 'Assistant' : 'You',
      content: message.content,
      isStreaming: false,
      timestamp,
      metadata: message.metadata || {},
    };
  }, []);

  const hydrateGeneralTranscript = useCallback(async (chatId, options = {}) => {
    if (!api || !chatId) {
      return;
    }
    try {
      const transcript = await api.fetchGeneralChatTranscript(currentAppId, chatId, options);
      if (!transcript) {
        return;
      }
      const normalized = (transcript.messages || [])
        .map(mapGeneralMessage)
        .filter(Boolean);
      generalMessagesCacheRef.current = normalized;
      if (conversationMode === 'ask') {
        setMessagesWithLogging(normalized);
      }
      setActiveGeneralChatId(transcript.chat_id);
      setGeneralChatSummary({
        chatId: transcript.chat_id,
        label: transcript.label || transcript.chat_id,
        lastUpdatedAt: transcript.last_updated_at,
        lastSequence: transcript.last_sequence,
      });
      setGeneralChatSessions((prev) => {
        if (!Array.isArray(prev) || !transcript.chat_id) {
          return prev;
        }
        const next = [...prev];
        const idx = next.findIndex((session) => session?.chat_id === transcript.chat_id);
        if (idx === -1) {
          return prev;
        }
        next[idx] = {
          ...next[idx],
          label: transcript.label || next[idx]?.label,
          last_updated_at: transcript.last_updated_at,
          lastSequence: transcript.last_sequence,
          sequence: transcript.sequence ?? next[idx]?.sequence,
        };
        return next;
      });
    } catch (err) {
      console.error('Failed to hydrate general chat transcript:', err);
    }
  }, [api, conversationMode, currentAppId, mapGeneralMessage, setActiveGeneralChatId, setGeneralChatSummary, setGeneralChatSessions, setMessagesWithLogging]);

  const updateArtifactPayload = useCallback((artifactId, updateFn) => {
    if (!updateFn) return;
    setCurrentArtifactMessages((prev) => {
      if (!Array.isArray(prev) || prev.length === 0) return prev;
      let updated = false;
      const next = prev.map((msg) => {
        const payload = msg?.toolCall?.payload;
        if (!msg?.toolCall) return msg;
        const resolvedId = deriveArtifactId(payload, msg?.toolCall?.tool_call_id || msg?.id);
        if (artifactId && resolvedId !== artifactId) return msg;
        updated = true;
        const nextPayload = updateFn(payload || {});
        return {
          ...msg,
          toolCall: {
            ...msg.toolCall,
            payload: nextPayload,
          },
        };
      });
      if (updated) return next;
      // Fallback: apply to most recent artifact if id mismatch
      const lastIdx = prev.length - 1;
      const last = prev[lastIdx];
      if (last?.toolCall) {
        const nextPayload = updateFn(last.toolCall.payload || {});
        const fallback = [...prev];
        fallback[lastIdx] = {
          ...last,
          toolCall: {
            ...last.toolCall,
            payload: nextPayload,
          },
        };
        return fallback;
      }
      return prev;
    });
  }, [setCurrentArtifactMessages]);

  const applyOptimisticForAction = useCallback((actionId, artifactId, optimistic) => {
    if (!optimistic) return;
    updateArtifactPayload(artifactId, (payload) => {
      if (!optimisticSnapshotsRef.current.has(actionId)) {
        let snapshotPayload = payload;
        try {
          if (payload && typeof payload === 'object') {
            snapshotPayload = JSON.parse(JSON.stringify(payload));
          }
        } catch {}
        optimisticSnapshotsRef.current.set(actionId, { artifactId, payload: snapshotPayload });
      }
      return applyOptimisticUpdate(payload, optimistic);
    });
  }, [updateArtifactPayload]);

  const applyArtifactUpdateForAction = useCallback((artifactId, update) => {
    if (!update) return;
    updateArtifactPayload(artifactId, (payload) => applyArtifactUpdate(payload, update));
  }, [updateArtifactPayload]);
  const handleIncomingRef = useRef(null);

  // Simplified incoming handler (namespaced chat.* only)
  const handleIncoming = useCallback((data) => {
    if (!data?.type) return;
    if (dispatchSurfaceEvent) {
      dispatchSurfaceEvent(data);
    }
    const showSystemMessages = debugFlag('mozaiks.show_system_messages') || debugFlag('mozaiks.debug_pipeline');
    // Robust spinner hide: only once
    try {
      if (initSpinnerShownRef.current && !initSpinnerHiddenOnceRef.current) {
        const outerType = data.type || '';
        const isText = outerType === 'chat.text';
        const isMeta = outerType === 'chat_meta' || outerType === 'chat.chat_meta';
        const isStreamChunk = outerType === 'chat.stream_chunk';
        const serializedText = !isText && typeof data.content === 'string' && data.content.includes('"type":"chat.text"');
        if (isText || isMeta || isStreamChunk || serializedText) {
          initSpinnerHiddenOnceRef.current = true;
          setShowInitSpinner(false);
        }
      }
    } catch {}
    if (debugFlag('mozaiks.debug_pipeline')) {
      const agentDbg = data.agent || data.agent_name;
      if ((data.type === 'chat.print' || data.type === 'chat.text') && data.is_visual === false) {
        console.warn('[PIPELINE] Non-visual agent message received (unexpected?)', agentDbg);
      }
    }

    // Handle chat_meta events (which may not have chat. prefix)
    if (data.type === 'chat_meta' || data.type === 'chat.chat_meta') {
      // Initial metadata handshake from backend
      applySessionStatePendingHarnessDecision(data.session_state);
      if (data.cache_seed !== undefined && data.cache_seed !== null) {
        setCacheSeed(data.cache_seed);
        if (currentChatId) {
          setStoredChatCacheSeed(currentChatId, data.cache_seed);
        }
      }
      if (data.chat_exists === false) {
        // Backend indicates this chat_id had no persisted session (fresh after client-side reuse)
        setChatExists(false);
        clearStoredArtifactState(currentChatId);
        // Reset any prior artifact state
        setCurrentArtifactMessages([]);
        lastArtifactEventRef.current = null;
        artifactCacheValidRef.current = false;
        artifactRestoredOnceRef.current = true; // prevent later restore effect
      } else if (data.chat_exists === true) {
        setChatExists(true);
        if (!data.last_artifact || !data.last_artifact.tool_name) {
          clearStoredArtifactState(currentChatId);
          setCurrentArtifactMessages([]);
          lastArtifactEventRef.current = null;
          artifactRestoredOnceRef.current = false;
          artifactCacheValidRef.current = false;
        }

        // If backend already sent last_artifact and we have not restored yet, cache it for restore effect
        if (!artifactRestoredOnceRef.current && data.last_artifact && data.last_artifact.tool_name) {
          try {
            writeStoredLastArtifact(currentChatId, {
              tool_name: data.last_artifact.tool_name,
              tool_call_id: data.last_artifact.tool_call_id || null,
              workflow_name: data.last_artifact.workflow_name || currentWorkflowName,
              payload: data.last_artifact.payload || {},
              display: data.last_artifact.display || 'artifact',
              ts: Date.now(),
            });
            artifactCacheValidRef.current = true;
          } catch (e) { console.warn('Failed to cache server last_artifact', e); }
        }
      }
      return;
    }
    
    // Some backends double-serialize the envelope: outer {type, content: JSON-stringified {type:"chat.text", data:{...}}}
    // Detect and unwrap once so downstream logic always works with a flat object.
    // Do this BEFORE checking for chat. prefix so we can unwrap "unknown" events that contain chat events.
    try {
      if (typeof data.content === 'string' && data.content.startsWith('{') && data.content.includes('"type":"')) {
        const inner = JSON.parse(data.content);
        if (inner && inner.type) {
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          // If this is chat_meta, handle it directly
          if (inner.type === 'chat_meta') {
            const metaData = inner.data || {};
            applySessionStatePendingHarnessDecision(metaData.session_state);
            if (metaData.cache_seed !== undefined && metaData.cache_seed !== null) {
              setCacheSeed(metaData.cache_seed);
              if (currentChatId) {
                setStoredChatCacheSeed(currentChatId, metaData.cache_seed);
              }
            }
            if (metaData.chat_exists === false) {
              setChatExists(false);
              clearStoredArtifactState(currentChatId);
              setCurrentArtifactMessages([]);
              lastArtifactEventRef.current = null;
              artifactRestoredOnceRef.current = true;
              artifactCacheValidRef.current = false;
            } else if (metaData.chat_exists === true) {
              setChatExists(true);
              if (!metaData.last_artifact || !metaData.last_artifact.tool_name) {
                clearStoredArtifactState(currentChatId);
                setCurrentArtifactMessages([]);
                lastArtifactEventRef.current = null;
                artifactRestoredOnceRef.current = false;
                artifactCacheValidRef.current = false;
              }
              if (!artifactRestoredOnceRef.current && metaData.last_artifact && metaData.last_artifact.tool_name) {
                try {
                  writeStoredLastArtifact(currentChatId, {
                    tool_name: metaData.last_artifact.tool_name,
                    tool_call_id: metaData.last_artifact.tool_call_id || null,
                    workflow_name: metaData.last_artifact.workflow_name || currentWorkflowName,
                    payload: metaData.last_artifact.payload || {},
                    display: metaData.last_artifact.display || 'artifact',
                    ts: Date.now(),
                  });
                  artifactCacheValidRef.current = true;
                } catch (e) { console.warn('Failed to cache server last_artifact', e); }
              }
            }
            return;
          }
          // For other events, unwrap and continue processing
          const innerData = inner.data || {};
          data.type = inner.type; // Use the inner type (could be chat.*, unknown, etc.)
          if (typeof innerData.content === 'string') data.content = innerData.content;
          if (innerData.agent) data.agent = innerData.agent;
          if (innerData.agent_name) data.agent_name = innerData.agent_name;
          if (innerData.sender && !innerData.agent && !innerData.agent_name) data.agent = innerData.sender;
          // Capability flags
          data.is_structured_capable = !!innerData.is_structured_capable;
          if (innerData.is_visual !== undefined) data.is_visual = innerData.is_visual;
          if (innerData.is_tool_agent !== undefined) data.is_tool_agent = innerData.is_tool_agent;
          if (innerData.ui_visibility !== undefined) data.ui_visibility = innerData.ui_visibility;
          if (innerData.trace_reason !== undefined) data.trace_reason = innerData.trace_reason;
          if (innerData.trace_agent !== undefined) data.trace_agent = innerData.trace_agent;
          if (innerData.sequence !== undefined) data.sequence = innerData.sequence;
          if (innerData.stream_id !== undefined) data.stream_id = innerData.stream_id;
          if (innerData.full_content !== undefined) data.full_content = innerData.full_content;
          if (innerData.metadata !== undefined) data.metadata = innerData.metadata;
          // Preserve structured output payloads if present
          if (innerData.structured_output !== undefined) data.structured_output = innerData.structured_output;
          if (innerData.structured_schema !== undefined) data.structured_schema = innerData.structured_schema;
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
        }
      }
    } catch (e) {
      if (debugFlag('mozaiks.debug_pipeline')) console.warn('[PIPELINE] failed to unwrap nested envelope', e);
    }
    
    if (!data.type.startsWith('chat.') && data.type !== 'unknown') return;

    // Some events may arrive already as { type:'chat.text', data:{ ...actualFields... } } (no double-serialization)
    // or after the above unwrap we can still retain an inner data object we need to promote.
    try {
      if (data.data && typeof data.data === 'object') {
        const inner = data.data;
        // Promote only if target field missing or clearly placeholder
        const promote = (k, aliasArr=[]) => {
          if (inner[k] === undefined) return;
          if (data[k] === undefined || data[k] === 'Unknown' || data[k] === null) data[k] = inner[k];
          // Apply aliases (e.g. sender -> agent) if primary absent
          aliasArr.forEach(alias => {
            if (data[alias] === undefined && inner[k] !== undefined) data[alias] = inner[k];
          });
        };
        promote('agent');
        promote('agent_name');
        // sender can act as agent fallback
        if (!data.agent && !data.agent_name && inner.sender) data.agent = inner.sender;
        // Core textual content (avoid overwriting if we already have a non-empty string)
        if (inner.content && (!data.content || !String(data.content).trim())) data.content = inner.content;
        // Capability / classification flags
        ['is_visual','is_structured_capable','is_tool_agent'].forEach(f => { if (inner[f] !== undefined && data[f] === undefined) data[f] = inner[f]; });
        // Structured output payload + schema
        if (inner.structured_output !== undefined && data.structured_output === undefined) data.structured_output = inner.structured_output;
        if (inner.structured_schema !== undefined && data.structured_schema === undefined) data.structured_schema = inner.structured_schema;
        // UI tool / component hints (input_request etc.) + error messages
        ['component_type','tool_name','tool_call_id','request_id','progress_percent','prompt','success','interaction_type','status','corr','call_id','payload','message','error_code','ui_visibility','trace_reason','trace_agent','sequence','stream_id','full_content','metadata','role','replay','index','timestamp'].forEach(f => {
          if (inner[f] !== undefined && data[f] === undefined) data[f] = inner[f];
        });
      }
    } catch (e) {
      if (debugFlag('mozaiks.debug_pipeline')) console.warn('[PIPELINE] failed to promote data.data fields', e);
    }

    // Final resolution / fallback normalization before dispatch
    try {
      if (typeof data.content === 'object' && data.content !== null) {
        // Some backends might leave content object like { content: 'text' }
        if (data.content.content && typeof data.content.content === 'string') {
          data.content = data.content.content;
        } else if (data.content.text && typeof data.content.text === 'string') {
          data.content = data.content.text;
        } else if (data.content.message && typeof data.content.message === 'string') {
          data.content = data.content.message;
        }
      }
      if (!data.agent && !data.agent_name && data.sender) data.agent = data.sender;
      if (debugFlag('mozaiks.debug_pipeline')) {
      }
    } catch {}

    const evt = data.type.startsWith('chat.') ? data.type.slice(5) : data.type;
    switch (evt) {
      case 'mode_changed': {
        const payload = data.data || {};
        const nextMode = payload.mode || payload.status;
        if (nextMode === 'general') {
          setConversationMode('ask');
          const generalId = payload.general_chat_id || payload.chat_id;
          if (generalId) {
            setActiveGeneralChatId(generalId);
            setGeneralChatSummary({
              chatId: generalId,
              label: payload.general_chat_label || payload.label || 'Ask',
              lastUpdatedAt: payload.last_updated_at || payload.timestamp || null,
              lastSequence: payload.general_chat_sequence,
            });
            if (!generalHydrationPendingRef.current) {
              generalHydrationPendingRef.current = true;
              Promise.resolve(hydrateGeneralTranscript(generalId)).finally(() => {
                generalHydrationPendingRef.current = false;
              });
            }
          }
          refreshGeneralSessions();
        } else if (nextMode === 'workflow') {
          setConversationMode('workflow');
        }
        if (payload.message && showSystemMessages) {
          setMessagesWithLogging((prev) => ([
            ...prev,
            {
              id: `mode-msg-${Date.now()}`,
              sender: 'system',
              agentName: 'System',
              content: payload.message,
              isStreaming: false,
            }
          ]));
        }
        return;
      }
      case 'general_session_created': {
        const payload = data.data || {};
        const generalId = payload.general_chat_id || payload.chat_id;
        if (generalId) {
          generalMessagesCacheRef.current = [];
          escalationCardInjectedRef.current = false; // reset per new ask session
          setActiveGeneralChatId(generalId);
          setGeneralChatSummary({
            chatId: generalId,
            label: payload.general_chat_label || payload.label || 'Ask',
            lastUpdatedAt: payload.last_updated_at || payload.timestamp || null,
            lastSequence: payload.general_chat_sequence,
          });
          setConversationMode('ask');
          generalHydrationPendingRef.current = true;
          Promise.resolve(hydrateGeneralTranscript(generalId)).finally(() => {
            generalHydrationPendingRef.current = false;
          });
        }
        refreshGeneralSessions();
        return;
      }
      case 'transition_requested': {
        const payload = data.data || {};
        if (payload.transition_id) {
          setPendingTransitionId(payload.transition_id);
          setPendingTransitionContext(payload.context_variables || {});
        }
        return;
      }
      case 'context_switched': {
        const payload = data.data || {};
        const targetChatId = payload.to_chat_id || payload.chat_id;
        if (targetChatId) {
          setCurrentChatId(targetChatId);
          setActiveChatId(targetChatId);
          try { localStorage.setItem(LOCAL_STORAGE_KEY, targetChatId); } catch {}
        }
        if (payload.workflow_name) {
          currentWorkflowNameRef.current = payload.workflow_name;
          setCurrentWorkflowName(payload.workflow_name);
          setActiveWorkflowName(payload.workflow_name);
        }
        setConversationMode('workflow');
        setWorkflowCompleted(false);
        setCompletionData(null);
        if (String(payload.workflow_name || '').toLowerCase() === 'designdocs') {
          setMessagesWithLogging((prev) => {
            const statusKey = 'designdocs-generating';
            const last = prev.length ? prev[prev.length - 1] : null;
            if (last?.metadata?.status_key === statusKey) return prev;
            return [
              ...prev,
              {
                id: `designdocs-status-${Date.now()}`,
                sender: 'system',
                agentName: 'System',
                content: 'Generating design...',
                isStreaming: false,
                metadata: {
                  event_type: 'workflow_status',
                  status_key: statusKey,
                },
              },
            ];
          });
        }
        if (payload.message && showSystemMessages) {
          setMessagesWithLogging((prev) => ([
            ...prev,
            {
              id: `ctx-msg-${Date.now()}`,
              sender: 'system',
              agentName: 'System',
              content: payload.message,
              isStreaming: false,
            }
          ]));
        }
        return;
      }
      case 'stream_chunk': {
        // Real-time token streaming from AG2/transport chunk pipeline.
        // Each chunk is one word (or small slice) of the agent response.
        const agentNameChunk = extractAgentName(data);
        const chunkContent = data.content || '';
        const streamId = data.stream_id || null;
        if (!chunkContent) return;
        // Hide init spinner on first streaming token
        if (showInitSpinner) setShowInitSpinner(false);
        initSpinnerHiddenOnceRef.current = true;
        setMessagesWithLogging(prev => {
          // Clear thinking bubbles when the first chunk of a new message arrives
          const withoutThinking = prev.filter(m => !m.isThinking);
          const updated = [...withoutThinking];
          // Append to an existing in-progress streaming message for this agent
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i];
            const sameStream = streamId && m.__streamId && m.__streamId === streamId;
            const sameAgentFallback = !streamId && !m.__streamId && m.agentName === agentNameChunk;
            if (m.__streaming && (sameStream || sameAgentFallback)) {
              updated[i] = {
                ...m,
                content: `${m.content || ''}${chunkContent}`,
              };
              return updated;
            }
          }
          // No existing streaming message — create one using event metadata
          updated.push({
            id: `stream-chunk-${Date.now()}`,
            sender: 'agent',
            agentName: agentNameChunk,
            content: chunkContent,
            isStreaming: true,
            __streaming: true,
            __streamId: streamId,
            isStructuredCapable: !!(data.is_structured_capable),
            isVisual: data.is_visual !== undefined ? !!data.is_visual : true,
            isToolAgent: !!(data.is_tool_agent),
          });
          return updated;
        });
        return;
      }
      case 'stream_end': {
        // End of a streaming turn — finalize the streamed message.
        // stream_end carries the authoritative full_content plus any capability
        // metadata (is_visual, structured_output, etc.) that were on chat.text.
        const agentNameEnd = extractAgentName(data);
        const finalContent = data.full_content || data.content || '';
        const streamEndMetadata = data.metadata || data.data?.metadata || {};
        const streamId = data.stream_id || data.data?.stream_id || null;
        setMessagesWithLogging(prev => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i];
            const sameStream = streamId && m.__streamId && m.__streamId === streamId;
            const sameAgentFallback = !streamId && !m.__streamId && m.agentName === agentNameEnd;
            if (m.__streaming && (sameStream || sameAgentFallback)) {
              const finalized = {
                ...m,
                content: finalContent || m.content,
                isStreaming: false,
                metadata: streamEndMetadata || m.metadata || null,
              };
              delete finalized.__streaming;
              delete finalized.__streamId;
              // Apply capability flags forwarded from the original chat.text data
              if (data.is_structured_capable !== undefined) finalized.isStructuredCapable = !!data.is_structured_capable;
              if (data.is_visual !== undefined) finalized.isVisual = !!data.is_visual;
              if (data.is_tool_agent !== undefined) finalized.isToolAgent = !!data.is_tool_agent;
              if (data.structured_output) finalized.structuredOutput = data.structured_output;
              if (data.structured_schema) finalized.structuredSchema = data.structured_schema;
              updated[i] = finalized;
              return updated;
            }
          }
          // No in-progress streaming message found — add a finished message directly
          if (finalContent) {
            if (shouldSuppressConsecutiveAssistantDuplicate(updated, {
              agentName: agentNameEnd,
              content: finalContent,
              source: streamEndMetadata?.source || null,
            })) {
              return updated;
            }
            updated.push({
              id: `stream-end-${Date.now()}`,
              sender: 'agent',
              agentName: agentNameEnd,
              content: finalContent,
              isStreaming: false,
              isStructuredCapable: !!(data.is_structured_capable),
              isVisual: data.is_visual !== undefined ? !!data.is_visual : true,
              isToolAgent: !!(data.is_tool_agent),
              structuredOutput: data.structured_output || null,
              structuredSchema: data.structured_schema || null,
              metadata: streamEndMetadata || null,
            });
          }
          return updated;
        });
        // Mobile badge: notify if the artifact drawer is covering the chat feed
        if (isMobileView && mobileDrawerState === 'expanded') {
          setHasUnseenChat(true);
        }
        // Escalation card: inject inline when the AI response mentions the ? / operator support path
        if (
          conversationMode === 'ask' &&
          finalContent &&
          !escalationCardInjectedRef.current
        ) {
          const ESCALATION_CUE = /\?.*button|\btap\s+the\s+\?|click\s+the\s+\?|reach\s+an?\s+operator|operator\s+directly|connect\s+with\s+an?\s+operator/i;
          if (ESCALATION_CUE.test(finalContent)) {
            escalationCardInjectedRef.current = true;
            setMessagesWithLogging(prev => [
              ...prev,
              {
                id: `escalation-${Date.now()}`,
                sender: 'agent',
                agentName: agentNameEnd,
                content: '',
                toolCall: { tool_name: 'EscalationCard', payload: {} },
                isStreaming: false,
              },
            ]);
          }
        }
        return;
      }
      case 'print': {
        const printVisibility = data.ui_visibility || data.data?.ui_visibility || data.metadata?.ui_visibility || null;
        const showTraceMessages = debugFlag('mozaiks.show_trace_messages') || debugFlag('mozaiks.debug_pipeline');
        if (printVisibility === 'hidden') {
          return;
        }
        if (printVisibility === 'trace' && !showTraceMessages) {
          return;
        }
        if (shouldSuppressHiddenSeedEvent(data)) {
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          if (showInitSpinner) setShowInitSpinner(false);
          return;
        }
        const printMetadata = data.metadata || data.data?.metadata || {};
        if (conversationMode === 'ask' && printMetadata?.source !== 'general_agent') {
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          return;
        }
        const agentName = extractAgentName(data);
        const chunk = data.content || '';
        if (!chunk) return;
        setMessagesWithLogging(prev => {
          const updated = [...prev];
            for (let i = updated.length -1; i>=0; i--) {
              const m = updated[i];
              if (m.__streaming && m.agentName === agentName) {
                m.content += chunk;
                if (debugFlag('mozaiks.debug_pipeline')) {
                }
                return updated;
              }
            }
          // Capability flags (new schema) from backend event data
          const isStructuredCapable = !!data.is_structured_capable;
          const isVisual = !!data.is_visual;
          const isToolAgent = !!data.is_tool_agent;
          updated.push({
            id:`stream-${Date.now()}`,
            sender:'agent',
            agentName,
            content:chunk,
            isStreaming:true,
            __streaming:true,
            isStructuredCapable,
            isVisual,
            isToolAgent
          });
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          return updated;
        });
        return;
      }
      case 'text': {
        const textVisibility = data.ui_visibility || data.data?.ui_visibility || data.metadata?.ui_visibility || null;
        const showTraceMessages = debugFlag('mozaiks.show_trace_messages') || debugFlag('mozaiks.debug_pipeline');
        if (textVisibility === 'hidden') {
          return;
        }
        if (textVisibility === 'trace' && !showTraceMessages) {
          return;
        }
        if (shouldSuppressHiddenSeedEvent(data)) {
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          if (showInitSpinner) {
            setShowInitSpinner(false);
          }
          return;
        }
        const content = data.content || '';
        const metadataSource = data.metadata || data.data?.metadata || {};
        const isReplayEvent = Boolean(data.replay);
        const replayIndexRaw = data.index;
        const replayIndex = Number.isFinite(replayIndexRaw)
          ? Number(replayIndexRaw)
          : Number.isFinite(Number(replayIndexRaw))
            ? Number(replayIndexRaw)
            : null;
        const serverSequenceRaw = data.sequence;
        const serverSequence = Number.isFinite(serverSequenceRaw)
          ? Number(serverSequenceRaw)
          : Number.isFinite(Number(serverSequenceRaw))
            ? Number(serverSequenceRaw)
            : null;
        const normalizedAgent = (data.agent || data.agent_name || data.sender || '').toLowerCase();
        const isGeneralUserEcho = metadataSource?.source === 'general_agent' && normalizedAgent === 'user';
        const isWorkflowUserMessage = !isGeneralUserEcho && (
          normalizedAgent === 'user' ||
          (data.role && String(data.role).toLowerCase() === 'user') ||
          metadataSource?.source === 'workflow_user' ||
          Boolean(metadataSource?.input_request_id)
        );
        if (conversationMode === 'ask' && metadataSource?.source !== 'general_agent') {
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          return;
        }
        try {
          const wfCfg = workflowConfig?.getWorkflowConfig(currentWorkflowName);
          const startupMode = String(wfCfg?.startup_mode || '').trim().toLowerCase();
          const isSyntheticUserDrivenReplay = (
            isReplayEvent &&
            isWorkflowUserMessage &&
            startupMode === 'userdriven' &&
            String(content).trim() === '.' &&
            replayIndex === 0
          );
          if (isSyntheticUserDrivenReplay) {
            if (debugFlag('mozaiks.debug_pipeline')) {
            }
            return;
          }
        } catch {}
        if (isReplayEvent && conversationMode !== 'workflow' && metadataSource?.source !== 'general_agent') {
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          return;
        }
        if (isGeneralUserEcho) {
          const recent = messagesRef.current[messagesRef.current.length - 1];
          if (recent && recent.sender === 'user') {
            const recentText = String(recent.content || '').trim();
            if (recentText && recentText === String(content).trim()) {
              return;
            }
          }
        }
        // Enhanced suppression for assistant parroting user input (including minor variation)
        try {
          if (content) {
            const msgs = messagesRef.current;
            if (msgs.length) {
              const lastUserIdx = [...msgs].reverse().findIndex(m => m && m.sender === 'user');
              if (lastUserIdx !== -1) {
                const actualIndex = msgs.length - 1 - lastUserIdx;
                const lastUser = msgs[actualIndex];
                if (lastUser) {
                  const normUser = String(lastUser.content||'').trim();
                  const normContent = String(content).trim();
                  // Simple fuzzy: treat as echo if one contains the other and length difference small
                  const shorter = normUser.length <= normContent.length ? normUser : normContent;
                  const longer = normUser.length > normContent.length ? normUser : normContent;
                  const lengthDiff = Math.abs(normUser.length - normContent.length);
                  const containsRel = longer.includes(shorter);
                  const smallDiff = lengthDiff <= 3; // allow small typos / spacing differences
                  const identical = normUser === normContent;
                  if (normUser && (identical || (containsRel && smallDiff))) {
                    return; // skip echo-like repetition
                  }
                }
              }
            }
          }
        } catch {}
        if (!content.trim()) return;
        if (workflowReplayPendingRef.current && conversationMode === 'workflow' && metadataSource?.source !== 'general_agent') {
          workflowReplayPendingRef.current = false;
        }
        const displayAsUser = isGeneralUserEcho || isWorkflowUserMessage;
        const agentName = displayAsUser ? 'You' : extractAgentName(data);
        const computedSender = displayAsUser ? 'user' : 'agent';
        setMessagesWithLogging(prev => {
          // Remove any thinking messages when agent actually speaks
          const thinkingMessages = prev.filter(m => m.isThinking);
          if (thinkingMessages.length > 0) {
          }
          const filtered = prev.filter(m => !m.isThinking);
          const updated = [...filtered];
          
          if (updated.length) {
            const last = updated[updated.length-1];
            if (last.__streaming && last.agentName === agentName) {
              last.isStreaming = false; delete last.__streaming; if(!last.content.endsWith(content)) last.content+=content; return updated;
            }
          }

          if (isReplayEvent) {
            const normalizedContent = String(content).trim();
            for (let i = updated.length - 1; i >= 0; i -= 1) {
              const candidate = updated[i];
              if (!candidate || candidate.sender !== computedSender || candidate.agentName !== agentName) {
                continue;
              }
              const candidateContent = String(candidate.content || '').trim();
              if (!candidateContent || candidateContent !== normalizedContent) {
                continue;
              }
              const candidateReplayIndex = Number.isFinite(candidate.replayIndex)
                ? Number(candidate.replayIndex)
                : null;
              if (replayIndex !== null && candidateReplayIndex !== null && candidateReplayIndex !== replayIndex) {
                continue;
              }
              updated[i] = {
                ...candidate,
                metadata: metadataSource,
                replayIndex: replayIndex ?? candidateReplayIndex,
                serverSequence: serverSequence ?? candidate.serverSequence ?? null,
              };
              return updated;
            }
          }
          
          // Capability + structured output payload (if any) from new unified dispatcher
          const isStructuredCapable = !!data.is_structured_capable;
          const structuredOutput = data.structured_output || null; // actual structured content (if produced)
          const structuredSchema = data.structured_schema || null; // schema describing structuredOutput
          const isVisual = !!data.is_visual;
          const isToolAgent = !!data.is_tool_agent;
          if (computedSender === 'agent' && shouldSuppressConsecutiveAssistantDuplicate(updated, {
            agentName,
            content,
            source: metadataSource?.source || null,
          })) {
            return updated;
          }
          updated.push({
            id:`text-${Date.now()}`,
            sender: computedSender,
            agentName,
            content,
            isStreaming:false,
            isStructuredCapable,
            structuredOutput,
            structuredSchema,
            isVisual,
            isToolAgent,
            metadata: metadataSource,
            replayIndex,
            serverSequence,
          });
          if (debugFlag('mozaiks.debug_pipeline')) {
          }
          return updated;
        });
        if (metadataSource?.general_chat_id) {
          setGeneralChatSummary((prev) => ({
            chatId: metadataSource.general_chat_id,
            label: metadataSource.general_chat_label || prev?.label || 'Ask',
            lastUpdatedAt: Date.now(),
            lastSequence: metadataSource.general_chat_sequence || metadataSource.sequence || prev?.lastSequence,
          }));
        }
        
        // Badge notification: Set unseen chat badge if artifact drawer is covering chat
        if (isMobileView && mobileDrawerState === 'expanded') {
          setHasUnseenChat(true);
        }
        
        // Hide the one-time initialization spinner after the first successfully rendered chat.text
        if (showInitSpinner) {
          setShowInitSpinner(false);
        }
        // Auto-open artifact panel for visual outputs with display === 'artifact'
        try {
          const hasStructuredOutput = data.structured_output && Object.keys(data.structured_output).length > 0;
          const displayMode = data.display || data.display_type || data.mode || 
                              (data.structured_output && data.structured_output.display);
          
          // Only auto-open if explicitly marked as artifact display (not inline)
          if ((hasStructuredOutput || data.is_visual) && displayMode === 'artifact') {
            setLayoutMode && setLayoutMode('split');
            setIsSidePanelOpen && setIsSidePanelOpen(true);
          }
        } catch (e) {}
        return;
      }
      case 'tool_call': {
        if (data.component_type || (data.data && data.data.component_type)) {
          const envelope = data || {};
          const detail = envelope.data || {};
          const toolName = envelope.tool_name || detail.tool_name || detail.tool || 'UnknownTool';
          const componentType = envelope.component_type || detail.component_type || detail.component || toolName;
          const basePayload = detail.payload || envelope.payload || {};
          const payloadKeys = Object.keys(basePayload);
          const derivedDisplay = envelope.display || envelope.display_type || envelope.mode || detail.display || detail.display_type || detail.mode || basePayload.display || basePayload.mode || null;
          const toolCallId = envelope.tool_call_id || envelope.corr || detail.tool_call_id || detail.corr || null;
          const awaiting = envelope.awaiting_response !== undefined ? envelope.awaiting_response : detail.awaiting_response;
          const resolvedWorkflowName = envelope.workflow_name || detail.workflow_name || basePayload.workflow_name || currentWorkflowName;
          const interactionType = envelope.interaction_type || detail.interaction_type || basePayload.interaction_type || (awaiting ? 'ui_tool' : 'ui_surface');
          const sendResponse = (responseData) => {
            const activeWs = wsRef.current;
            if (activeWs && activeWs.send) {
              return activeWs.send(responseData);
            }
            console.warn('���s������,? No WebSocket connection available for UI tool response (tool_call)');
            return false;
          };
          dynamicUIHandler.processUIEvent({
            type: 'tool_call',
            tool_name: toolName,
            tool_call_id: toolCallId || undefined,
            component_type: componentType,
            workflow_name: resolvedWorkflowName,
            display: derivedDisplay,
            agent: envelope.agent || detail.agent || envelope.agent_name || detail.agent_name,
            agentName: envelope.agent || detail.agent || envelope.agent_name || detail.agent_name,
            payload: {
              ...basePayload,
              tool_name: toolName,
              component_type: componentType,
              workflow_name: resolvedWorkflowName,
              awaiting_response: awaiting,
              interaction_type: interactionType,
              ...(derivedDisplay ? { display: derivedDisplay } : {})
            }
          }, sendResponse);
          // Auto-open artifact panel ONLY for display === 'artifact'
          try {
            if (derivedDisplay === 'artifact') {
              setLayoutMode && setLayoutMode('split');
              setIsSidePanelOpen && setIsSidePanelOpen(true);
            }
          } catch (e) {}
        } else {
          if (showSystemMessages) {
            setMessagesWithLogging(prev => [...prev, { id: data.tool_call_id || `tool-call-${Date.now()}`, sender:'system', agentName:'System', content:`🔧 Tool Call: ${data.tool_name}`, isStreaming:false }]);
          }
        }
        return;
      }
      // ── ui.* typed event contract (L2) ──────────────────────────────────
      case 'ui.render': {
        // Typed contract fields mirror UIRenderData from ui_events.py.
        const inner = data.data || {};
        const component  = inner.component || inner.component_type || '';
        const displayMode = inner.display_mode || inner.display || 'inline';
        const toolCallId  = inner.tool_call_id || null;
        const workflow    = inner.workflow || inner.workflow_name || currentWorkflowName;
        const agent       = inner.agent || inner.agent_name || null;
        const awaiting    = inner.awaiting_response !== false;
        const basePayload = inner.payload || {};
        const toolName    = inner.tool_name || component;
        const interactionType = inner.interaction_type || (awaiting ? 'ui_tool' : 'ui_surface');


        const sendResponse = (responseData) => {
          const activeWs = wsRef.current;
          if (activeWs && activeWs.send) return activeWs.send(responseData);
          console.warn('⚠️ No WebSocket available for ui.render response');
          return false;
        };

        dynamicUIHandler.processUIEvent({
          type: 'ui.render',
          tool_name: toolName,
          component: component,
          component_type: component,
          tool_call_id: toolCallId || undefined,
          workflow: workflow,
          workflow_name: workflow,
          display: displayMode,
          display_mode: displayMode,
          awaiting_response: awaiting,
          agent,
          agentName: agent,
          interaction_type: interactionType,
          payload: {
            ...basePayload,
            component_type: component,
            workflow_name: workflow,
            awaiting_response: awaiting,
            interaction_type: interactionType,
            display: displayMode,
          },
        }, sendResponse);

        try {
          if (displayMode === 'artifact') {
            setLayoutMode && setLayoutMode('split');
            setIsSidePanelOpen && setIsSidePanelOpen(true);
          }
        } catch (e) {}
        return;
      }

      case 'ui.update': {
        // Patch a live component's payload without re-mounting.
        const inner = data.data || {};
        const patchId = inner.tool_call_id || null;
        const patch   = inner.patch || {};
        if (patchId) {
          const applyPatch = (msg) => {
            if (msg?.toolCall?.tool_call_id === patchId || msg?.metadata?.toolCallId === patchId) {
              return {
                ...msg,
                toolCall: msg.toolCall
                  ? { ...msg.toolCall, payload: { ...(msg.toolCall.payload || {}), ...patch } }
                  : msg.toolCall,
              };
            }
            return msg;
          };
          setMessagesWithLogging(prev => prev.map(applyPatch));
          setCurrentArtifactMessages(prev => prev.map(applyPatch));
        }
        return;
      }

      case 'ui.dismiss': {
        // Tear down a rendered component — same logic as tool_call_dismiss.
        const inner = data.data || {};
        const dismissedId = inner.tool_call_id || null;
        if (dismissedId) {
          setMessagesWithLogging((prev) =>
            prev.filter(
              (msg) => !(
                msg?.metadata?.toolCallId === dismissedId
                && (
                  msg?.metadata?.type === 'tool_call_agent_message'
                  || msg?.metadata?.type === 'composer_tool_call'
                  || msg?.metadata?.hideInTranscript
                )
              )
            )
          );
        }
        if (lastArtifactEventRef.current && (!dismissedId || dismissedId === lastArtifactEventRef.current)) {
          setIsSidePanelOpen(false);
          lastArtifactEventRef.current = null;
          artifactCacheValidRef.current = false;
          setCurrentArtifactMessages([]);
          if (currentChatId) clearStoredArtifactState(currentChatId);
        }
        return;
      }
      // ── end ui.* ────────────────────────────────────────────────────────

      case 'tool_call_complete':
      case 'chat.tool_call_complete': {
        const envelope = data || {};
        const detail = envelope.data || {};
        const completedId = detail.tool_call_id || envelope.tool_call_id || null;
        const completedTool = detail.tool_name || envelope.tool_name || null;
        const status = detail.status || envelope.status || 'completed';
        

        if (completedId) {
          // Mark matching tool-call messages complete so composer requests do not
          // remain pending after the user has already answered them.
          setMessagesWithLogging((prev) =>
            prev.map((msg) => {
              if (msg?.metadata?.toolCallId !== completedId) {
                return msg;
              }
              const displayMode =
                msg?.metadata?.display
                || msg?.toolCall?.display
                || msg?.toolCall?.payload?.display
                || msg?.toolCall?.payload?.mode
                || null;
              if (displayMode === 'inline') {
                return {
                  ...msg,
                  tool_call_completed: true,
                  tool_call_status: status,
                  tool_call_summary: `${completedTool || 'Tool'} completed`
                };
              }
              if (displayMode === 'composer' || msg?.metadata?.hideInTranscript) {
                return {
                  ...msg,
                  tool_call_completed: true,
                  tool_call_status: status,
                };
              }
              return msg;
            })
          );
        }
        return;
      }
      case 'tool_call_dismiss':
      case 'chat.tool_call_dismiss': {
        const envelope = data || {};
        const detail = envelope.data || {};
        const dismissedId = detail.tool_call_id || envelope.tool_call_id || null;
        const dismissedTool = detail.tool_name || envelope.tool_name || null;

        if (dismissedId) {
          setMessagesWithLogging((prev) =>
            prev.filter(
              (msg) => !(
                msg?.metadata?.toolCallId === dismissedId
                && (
                  msg?.metadata?.type === 'tool_call_agent_message'
                  || msg?.metadata?.type === 'composer_tool_call'
                  || msg?.metadata?.hideInTranscript
                )
              )
            )
          );
        }

        if (lastArtifactEventRef.current && (!dismissedId || dismissedId === lastArtifactEventRef.current)) {
          setIsSidePanelOpen(false);
          lastArtifactEventRef.current = null;
          artifactCacheValidRef.current = false;
          setCurrentArtifactMessages([]);
          if (currentChatId) {
            clearStoredArtifactState(currentChatId);
          }
        }
        return;
      }
      case 'artifact.action.started': {
        const detail = data.data || {};
        const actionId = detail.action_id || data.action_id;
        const artifactId = detail.artifact_id || data.artifact_id;
        if (actionId) {
          setActionStatusMap((prev) => ({
            ...prev,
            [actionId]: {
              ...(prev[actionId] || {}),
              status: 'started',
              artifact_id: artifactId,
              tool: detail.tool || data.tool,
            }
          }));
        }
        return;
      }
      case 'artifact.action.completed': {
        const detail = data.data || {};
        const actionId = detail.action_id || data.action_id;
        const artifactId = detail.artifact_id || data.artifact_id;
        const result = detail.result || data.result || {};
        const artifactUpdate = detail.artifact_update || data.artifact_update || result?.artifact_update;
        if (actionId) {
          setActionStatusMap((prev) => ({
            ...prev,
            [actionId]: {
              ...(prev[actionId] || {}),
              status: 'completed',
              result,
            }
          }));
          optimisticSnapshotsRef.current.delete(actionId);
        }
        if (artifactUpdate) {
          applyArtifactUpdateForAction(artifactId, artifactUpdate);
        }
        return;
      }
      case 'artifact.action.failed': {
        const detail = data.data || {};
        const actionId = detail.action_id || data.action_id;
        const artifactId = detail.artifact_id || data.artifact_id;
        const error = detail.error || data.error || 'Action failed';
        const rollback = detail.rollback || data.rollback;
        if (actionId) {
          setActionStatusMap((prev) => ({
            ...prev,
            [actionId]: {
              ...(prev[actionId] || {}),
              status: 'failed',
              error,
            }
          }));
        }
        if (rollback && actionId) {
          const snapshot = optimisticSnapshotsRef.current.get(actionId);
          if (snapshot?.payload) {
            updateArtifactPayload(snapshot.artifactId || artifactId, () => snapshot.payload);
          }
          optimisticSnapshotsRef.current.delete(actionId);
        }
        return;
      }
      case 'tool_response': {
        // Suppress intermediate auto-tool responses (already handled by dynamicUIHandler)
        // Only show failures or non-auto-tool responses
        if (data.interaction_type === 'auto_tool' && data.success) {
          return;
        }
        if (showSystemMessages) {
          const responseContent = data.success ? `✅ Tool Response: ${data.content || 'Success'}` : `❌ Tool Failed: ${data.content || 'Error'}`;
          setMessagesWithLogging(prev => [...prev, { id: data.tool_call_id || `tool-response-${Date.now()}`, sender:'system', agentName:'System', content: responseContent, isStreaming:false }]);
        }
        return;
      }
      case 'usage_summary': {
        if (showSystemMessages) {
          setMessagesWithLogging(prev => [...prev, { id:`usage-${Date.now()}`, sender:'system', agentName:'System', content:`📊 Usage: tokens=${data.total_tokens} prompt=${data.prompt_tokens} completion=${data.completion_tokens}${data.cost?` cost=$${data.cost}`:''}`, isStreaming:false }]);
        }
        return;
      }
      case 'workflow_batch_started': {
        const payload = data.data || {};
        const count = Number(data.count ?? payload.count ?? 0);
        const label = count > 0 ? `Running ${count} parallel workflow task${count === 1 ? '' : 's'}...` : 'Running parallel workflow tasks...';
        setMessagesWithLogging(prev => [...prev, {
          id: `system-batch-${Date.now()}`,
          sender: 'system',
          agentName: 'System',
          content: `⚙️ ${label}`,
          isStreaming: false,
          metadata: {
            event_type: 'workflow_batch_started',
            trigger_id: data.trigger_id || payload.trigger_id || null,
            count: count || null,
          },
        }]);
        return;
      }
      case 'workflow_child_completed': {
        if (!showSystemMessages) {
          return;
        }
        const payload = data.data || {};
        const idx = Number(data.child_index ?? payload.child_index ?? 0);
        const total = Number(data.child_total ?? payload.child_total ?? 0);
        const ok = data.success ?? payload.success;
        const status = String(data.status || payload.status || (ok ? 'completed' : 'failed'));
        const progressText = idx > 0 && total > 0 ? ` (${idx}/${total})` : '';
        const icon = ok ? '✅' : '⚠️';
        setMessagesWithLogging(prev => [...prev, {
          id: `system-child-${Date.now()}`,
          sender: 'system',
          agentName: 'System',
          content: `${icon} Parallel child${progressText}: ${status}`,
          isStreaming: false,
          metadata: {
            event_type: 'workflow_child_completed',
            child_chat_id: data.child_chat_id || payload.child_chat_id || null,
            trigger_id: data.trigger_id || payload.trigger_id || null,
          },
        }]);
        return;
      }
      case 'select_speaker': {
        // Speaker selection marks a new agent taking over - inject thinking state
        const nextAgentName = data.agent || data.agent_name || data.selected_speaker || 'Agent';
        
        
        // Add a temporary "thinking" message that will be removed when next agent speaks
        setMessagesWithLogging(prev => {
          // Remove any existing thinking messages first
          const existingThinking = prev.filter(m => m.isThinking);
          if (existingThinking.length > 0) {
          }
          
          const filtered = prev.filter(m => !m.isThinking);
          return [
            ...filtered,
            {
              id: `thinking-${Date.now()}`,
              sender: 'agent',
              agentName: nextAgentName,
              content: '', // Empty content - will show thinking indicator
              isThinking: true,
              timestamp: Date.now()
            }
          ];
        });
        
        // Speaker selection often marks a new turn/run start. If we have an open artifact
        // from a prior sequence, collapse it now and clear the cache.
        if (lastArtifactEventRef.current && isSidePanelOpen) {
          try {
          } catch {}
          setIsSidePanelOpen(false);
          lastArtifactEventRef.current = null;
          setCurrentArtifactMessages([]);
          // Clear artifact cache on new conversation turn
          if (currentChatId) {
            clearStoredArtifactState(currentChatId);
          }
        }
        return;
      }
      case 'activity': {
        const activityType = data.activity_type || data.status || 'background';
        const activityAgent = data.agent || data.agent_name || 'System';
        const activityStatus = data.status || 'working';
        const activityMessage = data.message
          || `${activityAgent} is working in the background.`;
        const activityKey = `${activityType}:${activityAgent}`;
        if (showInitSpinner) {
          setShowInitSpinner(false);
          initSpinnerHiddenOnceRef.current = true;
        }
        setMessagesWithLogging(prev => {
          const updated = [...prev];
          const last = updated.length ? updated[updated.length - 1] : null;
          const nextEntry = {
            id: last?.metadata?.event_type === 'activity' && last?.metadata?.activity_key === activityKey
              ? last.id
              : `activity-${Date.now()}`,
            sender: 'system',
            agentName: 'System',
            content: `⏳ ${activityMessage}`,
            isStreaming: false,
            metadata: {
              event_type: 'activity',
              activity_type: activityType,
              activity_status: activityStatus,
              activity_agent: activityAgent,
              activity_key: activityKey,
              workflow_name: data.workflow_name || currentWorkflowName || null,
            },
          };
          if (last?.metadata?.event_type === 'activity' && last?.metadata?.activity_key === activityKey) {
            updated[updated.length - 1] = nextEntry;
            return updated;
          }
          if (last?.metadata?.event_type === 'activity' && last?.content === nextEntry.content) {
            return updated;
          }
          updated.push(nextEntry);
          return updated;
        });
        return;
      }
      case 'tool_progress': {
        // Update or append progress for a long-running tool
        const progress = data.progress_percent;
        const tool = data.tool_name || 'tool';
        if (!showSystemMessages) {
          return;
        }
        setMessagesWithLogging(prev => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >=0; i--) {
            const m = updated[i];
            if (m.metadata && m.metadata.event_type === 'tool_call' && m.metadata.tool_name === tool) {
              m.content = `🔧 ${tool} progress: ${progress}%`;
              m.metadata.progress_percent = progress;
              return updated;
            }
          }
          updated.push({ id:`tool-progress-${Date.now()}`, sender:'system', agentName:'System', content:`🔧 ${tool} progress: ${progress}%`, isStreaming:false, metadata:{ event_type:'tool_progress', tool_name: tool, progress_percent: progress }});
          return updated;
        });
        return;
      }
      case 'deployment_started': {
        const payload = data.data || {};
        const message = payload.message || 'Starting deployment to GitHub...';
        if (showSystemMessages) {
          setMessagesWithLogging(prev => [...prev, {
            id: `deploy-start-${Date.now()}`,
            sender: 'system',
            agentName: 'System',
            content: `🚀 ${message}`,
            isStreaming: false,
            metadata: { event_type: 'deployment', status: 'started', job_id: payload.job_id || payload.jobId || null }
          }]);
        }
        return;
      }
      case 'deployment_progress': {
        const payload = data.data || {};
        const jobId = payload.job_id || payload.jobId || null;
        const statusText = payload.status || payload.message || 'Deployment in progress...';
        if (!showSystemMessages) {
          return;
        }
        setMessagesWithLogging(prev => {
          const updated = [...prev];
          if (jobId) {
            for (let i = updated.length - 1; i >= 0; i--) {
              const m = updated[i];
              if (m.metadata && m.metadata.event_type === 'deployment' && m.metadata.job_id === jobId) {
                m.content = `⏳ ${statusText}`;
                m.metadata.status = 'progress';
                return updated;
              }
            }
          }
          updated.push({
            id: `deploy-progress-${Date.now()}`,
            sender: 'system',
            agentName: 'System',
            content: `⏳ ${statusText}`,
            isStreaming: false,
            metadata: { event_type: 'deployment', status: 'progress', job_id: jobId }
          });
          return updated;
        });
        return;
      }
      case 'deployment_completed': {
        const payload = data.data || {};
        const repoUrl = payload.repo_url || payload.repoUrl;
        const message = payload.message || 'Deployment completed.';
        if (showSystemMessages) {
          setMessagesWithLogging(prev => [...prev, {
            id: `deploy-done-${Date.now()}`,
            sender: 'system',
            agentName: 'System',
            content: repoUrl ? `✅ ${message} Repo: ${repoUrl}` : `✅ ${message}`,
            isStreaming: false,
            metadata: { event_type: 'deployment', status: 'completed', job_id: payload.job_id || payload.jobId || null, repo_url: repoUrl || null }
          }]);
        }
        return;
      }
      case 'deployment_failed': {
        const payload = data.data || {};
        const message = payload.message || payload.error || 'Deployment failed.';
        if (showSystemMessages) {
          setMessagesWithLogging(prev => [...prev, {
            id: `deploy-fail-${Date.now()}`,
            sender: 'system',
            agentName: 'System',
            content: `❌ ${message}`,
            isStreaming: false,
            metadata: { event_type: 'deployment', status: 'failed', job_id: payload.job_id || payload.jobId || null }
          }]);
        }
        return;
      }
      case 'input_timeout': {
        setMessagesWithLogging(prev => [...prev, { id:`timeout-${Date.now()}`, sender:'system', agentName:'System', content:`⏱️ Input request timed out.`, isStreaming:false }]);
        return;
      }
      case 'awaiting_reply': {
        const payload = data.data || {};
        setLoading(false);
        setPendingWorkflowReply({
          agent: payload.source_agent || payload.agent || 'Agent',
          prompt: payload.prompt || '',
          reason: payload.reason || 'awaiting_user_reply',
        });
        return;
      }
      case 'run_complete': {
        
        // Extract completion metadata
        const reason = data.reason || data.data?.reason || 'finished';
        const status = data.status ?? data.data?.status ?? 1;
        const normalizedStatus = String(status).trim().toLowerCase();
        const isFailureCompletion = ['failed', 'failure', 'error', 'errored'].includes(normalizedStatus);
        if (isFailureCompletion) {
          const errorMessage = data.error || data.data?.error || data.message || data.data?.message || `Workflow failed (${reason})`;
          setLoading(false);
          setPendingWorkflowReply(null);
          setError(errorMessage);
          setMessagesWithLogging(prev => [...prev, {
            id:`run-failed-${Date.now()}`,
            sender:'system',
            agentName:'System',
            content:`⚠️ ${errorMessage}`,
            isStreaming:false
          }]);
          return;
        }
        const isTerminalCompletion = (
          status === 1 ||
          normalizedStatus === '1' ||
          ['completed', 'complete', 'success', 'succeeded', 'done', 'ok'].includes(normalizedStatus)
        );
        if (!isTerminalCompletion) {
          setLoading(false);
          setPendingWorkflowReply(prev => prev || {
            agent: data.agent || data.data?.agent || 'Agent',
            prompt: data.prompt || data.data?.prompt || '',
            reason,
          });
          return;
        }
        setLoading(false);
        setPendingWorkflowReply(null);
        // Only show completion overlay when no server-fired transition is already pending.
        // pendingTransitionIdRef gives synchronous access to avoid the race where
        // transition_requested and run_complete arrive in quick succession.
        if (!pendingTransitionIdRef.current) {
          const duration = data.duration_sec || data.data?.duration_sec;
          const tokensUsed = data.total_tokens || data.data?.total_tokens;
          setPendingTransitionContext({
            workflowName: currentWorkflowNameRef.current || currentWorkflowName,
            summary: {
              duration: duration ? `${Math.round(duration)}s` : null,
              tokensUsed: tokensUsed ? tokensUsed.toLocaleString() : null,
            },
          });
          setPendingTransitionId('workflow_complete');
        }
        return;
      }
      case 'chat.revision_requested': {
        // Emitted by AppReview's submit_revision_request tool when the user
        // requests changes. Route into the refinement control plane and switch
        // the active chat session to the new workflow in-place.
        const detail = data.data || data;
        const revisionText = detail.refinement_request || '';
        const artifactKind = detail.artifact_kind || 'app_bundle';
        const artifactKey = detail.artifact_key || artifactKind;
        const artifactVersionId = detail.artifact_version_id || null;
        const sourceSurface = detail.source_surface || 'app_review';
        const requestExtra = (
          detail.extra && typeof detail.extra === 'object' && !Array.isArray(detail.extra)
        ) ? detail.extra : {};
        if (!revisionText) return;
        const resolvedAppId = (
          appId ||
          user?.app_id ||
          config?.chat?.defaultAppId ||
          config?.appId ||
          config?.app_id ||
          'default'
        );
        const resolvedUserId = user?.id || user?.user_id || user?.email || null;
        const refinementRequest = {
          raw_user_request: revisionText,
          artifact_kind: artifactKind,
          artifact_key: artifactKey,
          source_surface: sourceSurface,
        };
        if (artifactVersionId) {
          refinementRequest.artifact_version_id = artifactVersionId;
        }
        if (Object.keys(requestExtra).length > 0) {
          refinementRequest.extra = requestExtra;
        }
        const triggerPayload = {
          refinement_request: refinementRequest,
        };
        fetch('/api/workflows/trigger', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            trigger_source: 'refinement',
            app_id: resolvedAppId,
            user_id: resolvedUserId,
            trigger_payload: triggerPayload,
          }),
        })
          .then(async (res) => {
            if (!res.ok) {
              console.error('[ChatPage] revision trigger failed:', res.status);
              return;
            }
            const triggerData = await res.json();
            if (triggerData.execution_mode === 'workflow' && triggerData.chat_id && triggerData.workflow_id) {
              setCurrentChatId(triggerData.chat_id);
              setActiveChatId(triggerData.chat_id);
              setCurrentWorkflowName(triggerData.workflow_id);
              setActiveWorkflowName(triggerData.workflow_id);
              setConversationMode('workflow');
              setWorkflowCompleted(false);
              setPendingHarnessDecision(null);
              setPendingHarnessDecisionError(null);
              return;
            }
            if (triggerData.execution_mode === 'harness_decision') {
              const nextDecision = buildPendingHarnessDecision(
                triggerData.harness_decision,
                {
                  trigger_source: triggerData.trigger_source || 'refinement',
                  requested_workflow_id: triggerData.requested_workflow_id || triggerData.workflow_id || null,
                  recommended_workflow_id: triggerData.workflow_id || null,
                  journey_id: triggerData.journey_id || null,
                  context_variables: {},
                  trigger_payload: triggerPayload,
                },
              );
              setPendingHarnessDecision(nextDecision);
              setPendingHarnessDecisionError(
                nextDecision ? null : 'Refinement requires a decision, but the decision payload was incomplete.'
              );
              setLoading(false);
              setPendingWorkflowReply(null);
            }
          })
          .catch((err) => {
            console.error('[ChatPage] revision trigger error:', err);
          });
        return;
      }
      case 'error': {
        setPendingWorkflowReply(null);
        const errorMsg = data.message || data.data?.message || 'Unknown error';
        const errorCode = data.error_code || data.data?.error_code;
        // Create a stable ID based on error content to prevent duplicates
        const errorId = `${errorCode || 'error'}-${errorMsg.slice(0, 50)}`;
        
        // Prevent duplicate errors (React StrictMode or event replay)
        if (lastErrorIdRef.current === errorId) {
          return;
        }
        lastErrorIdRef.current = errorId;
        
        setMessagesWithLogging(prev => [...prev, { id:`err-${Date.now()}`, sender:'system', agentName:'System', content:`❌ Error: ${errorMsg}`, isStreaming:false }]);
        return;
      }
      case 'input_ack':
        setPendingWorkflowReply(null);
        return;
      case 'resume_boundary':
        if (workflowReplayPendingRef.current) {
          workflowReplayPendingRef.current = false;
          const payload = data.data || {};
          const replayedCount = Number(payload.replayed_messages ?? payload.replayed_events ?? 0);
          if (replayedCount <= 0 && messagesRef.current.length === 0) {
            const cachedWorkflow = sanitizeVisibleWorkflowMessages(workflowMessagesSharedRef.current || []);
            if (cachedWorkflow.length > 0) {
              setMessagesWithLogging(cachedWorkflow);
            }
          }
        }
        if (showSystemMessages) {
          // Replay boundary marker for debug visibility
          setMessagesWithLogging(prev => [...prev, { id:`resume-${Date.now()}`, sender:'system', agentName:'System', content:`🔄 Session replay complete. Live events resumed.`, isStreaming:false }]);
        }
        return;
      default:
        return;
    }
  }, [currentChatId, currentWorkflowName, sanitizeVisibleWorkflowMessages, setMessagesWithLogging, extractAgentName, isSidePanelOpen, showInitSpinner, setLayoutMode, isMobileView, mobileDrawerState, setConversationMode, setActiveGeneralChatId, setGeneralChatSummary, hydrateGeneralTranscript, refreshGeneralSessions, setActiveChatId, setActiveWorkflowName, setCurrentChatId, applyArtifactUpdateForAction, updateArtifactPayload, applySessionStatePendingHarnessDecision, buildPendingHarnessDecision]);
  useEffect(() => {
    handleIncomingRef.current = handleIncoming;
  }, [handleIncoming]);

  // Debug: Log spinner state changes
  useEffect(() => {
  }, [showInitSpinner]);

  // Failsafe: never leave the one-time init spinner on indefinitely.
  useEffect(() => {
    if (!showInitSpinner || initSpinnerHiddenOnceRef.current) return undefined;

    const timer = setTimeout(() => {
      if (!initSpinnerHiddenOnceRef.current) {
        initSpinnerHiddenOnceRef.current = true;
        setShowInitSpinner(false);
      }
    }, 4000);

    return () => clearTimeout(timer);
  }, [showInitSpinner]);

  // Workflow configuration & resume bootstrap (no direct startChat here; handled by preflight existence effect)
  useEffect(() => {
    if (!api) return;

    // Hard requirement: app_id must be set via route/query/env (no implicit fallback)
    if (!currentAppId) {
      console.error(
        "Missing app_id. Provide it via URL (/chat/<app_id>), query (?appId=...), or REACT_APP_DEFAULT_APP_ID."
      );
      setConnectionStatus('error');
      setLoading(false);
      // Allow retry once the user fixes config (don’t lock the flags)
      setConnectionInitialized(false);
      connectionInProgressRef.current = false;
      return;
    }
    // Fetch workflow configs first; only signal ready once the registry is populated.
    // This prevents connectWithCorrectTransport from firing against an empty registry.
    workflowConfig.fetchWorkflowConfigs().finally(() => {
      setWorkflowConfigLoaded(true);
    });
    // When the URL explicitly targets a chat or requests a fresh start,
    // clear any stale activeChatId that was hydrated from localStorage so it
    // doesn't drive the first WS connection before the URL params are consumed.
    if (queryChatId || queryFreshStart) {
      setActiveChatId(null);
    }

    if (queryMode !== 'ask' && !queryForceAsk && !currentChatId && !queryChatId && !queryFreshStart) {
      const stored = getStoredActiveChatId();
      if (stored) {
        setCurrentChatId(stored);
        const seedStored = getStoredChatCacheSeed(stored);
        if (seedStored !== null) {
          setCacheSeed(seedStored);
        }
      }
    }
  }, [api, currentChatId, currentAppId, queryChatId, queryFreshStart, queryForceAsk, queryMode, setActiveChatId]);

  // NEW: Preflight chat existence + cache clearing logic
useEffect(() => {
  if (!api) return;
  if (!workflowConfigLoaded) return; // wait until registry is ready
  if (currentChatId) return; // existing logic handles resume or already started
  if (queryDeferStart) {
    setLoading(false);
    pendingStartRef.current = false;
    return;
  }
  if (pendingStartRef.current) return;

  pendingStartRef.current = true;
  (async () => {
    try {
      const askCarrierMode = queryMode === 'ask' || queryForceAsk || conversationMode === 'ask';
      let reuseChatId = queryFreshStart ? null : queryChatId;

      if (reuseChatId) {
        const wfName = resolveKnownWorkflowName(currentWorkflowName);
        if (!wfName) {
          console.warn('[EXISTS] No runnable workflow resolved; skipping chat reuse check');
          pendingStartRef.current = false;
          return;
        }
        try {
          const resp = await fetch(`${api.getHttpBaseUrl()}/api/chats/exists/${currentAppId}/${wfName}/${reuseChatId}`);
          if (resp.ok) {
            const data = await resp.json();
            if (data.exists) {
              setCurrentChatId(reuseChatId);
              setChatExists(true);
              if (askCarrierMode) {
                setCurrentWorkflowName(wfName);
                setConversationMode('ask');
                setConnectionInitialized(false);
                connectionInProgressRef.current = false;
                pendingStartRef.current = false;
                return;
              }
              
              // Update global chat context for persistent bubble
              setActiveChatId(reuseChatId);
              setActiveWorkflowName(wfName);
              setChatMinimized(false);
              
              pendingStartRef.current = false;
              return;
            }
            clearStoredArtifactState(reuseChatId);
            clearStoredChatCacheSeed(reuseChatId);
            if (getStoredActiveChatId() === reuseChatId) {
              setStoredActiveChatId(null);
            }
            consumeNavigationQueryParams(['chat_id']);
          } else {
            console.warn('[EXISTS] Backend returned non-OK; falling back to reuse chat_id');
            setCurrentChatId(reuseChatId);
            setChatExists(null);
            if (askCarrierMode) {
              setCurrentWorkflowName(wfName);
              setConversationMode('ask');
              setConnectionInitialized(false);
              connectionInProgressRef.current = false;
              pendingStartRef.current = false;
              return;
            }
            setActiveChatId(reuseChatId);
            setActiveWorkflowName(wfName);
            setChatMinimized(false);
            pendingStartRef.current = false;
            return;
          }
        } catch (e) {
          console.warn('[EXISTS] Existence check failed; reusing chat_id without backend', e);
          setCurrentChatId(reuseChatId);
          setChatExists(null);
          if (askCarrierMode) {
            setCurrentWorkflowName(wfName || currentWorkflowName);
            setConversationMode('ask');
            setConnectionInitialized(false);
            connectionInProgressRef.current = false;
            pendingStartRef.current = false;
            return;
          }
          setActiveChatId(reuseChatId);
          setActiveWorkflowName(wfName || null);
          setChatMinimized(false);
          pendingStartRef.current = false;
          return;
        }
      }

      const startWorkflowName = resolveKnownWorkflowName(currentWorkflowName);
      if (!startWorkflowName) {
        console.warn('[INIT] No runnable workflow resolved; skipping startChat');
        return;
      }
      const triggerMeta = {
        trigger_source: queryTriggerSource,
        ...(queryActionId ? { action_id: queryActionId } : {}),
        ...(queryChangeClass ? { change_class: queryChangeClass } : {}),
        ...(queryArtifactVersionId ? { artifact_version_id: queryArtifactVersionId } : {}),
      };
      const sessionOptions = {
        ...(askCarrierMode ? { transportPurpose: 'ask_carrier' } : {}),
        ...(queryFreshStart ? { forceNew: true } : {}),
      };
      const result = await api.startChat(
        currentAppId,
        startWorkflowName,
        currentUserId,
        {},
        queryContext,
        triggerMeta,
        Object.keys(sessionOptions).length > 0 ? sessionOptions : null,
      );
      if (result && (result.chat_id || result.id)) {
        const newId = result.chat_id || result.id;
        const reused = !!result.reused;
        const resolvedWorkflowName = result.workflow_name || startWorkflowName;
        setCurrentChatId(newId);
        setChatExists(reused);

        if (askCarrierMode) {
          setCurrentWorkflowName(resolvedWorkflowName);
          setConversationMode('ask');
          setConnectionInitialized(false);
          connectionInProgressRef.current = false;
          if (!reused) {
            clearStoredArtifactState(newId);
          }
          return;
        }

        // Keep URL session context canonical after chat creation so refresh/resume
        // paths always keep the concrete chat_id for this workflow run.
        const nextParams = new URLSearchParams(location.search || '');
        nextParams.delete('context');
        nextParams.delete('new');
        nextParams.delete('fresh');
        nextParams.delete('force_new');
        nextParams.set('workflow', resolvedWorkflowName);
        nextParams.set('chat_id', newId);
        nextParams.set('mode', 'workflow');
        const nextSearch = nextParams.toString();
        navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true });

        // Update global chat context for persistent bubble
        setActiveChatId(newId);
        setActiveWorkflowName(resolvedWorkflowName);
        setCurrentWorkflowName(resolvedWorkflowName);
        setChatMinimized(false);
        setStoredActiveChatId(newId);
        if (!reused) {
          clearStoredArtifactState(newId);
        }
      }
    } catch (e) {
      console.error('[INIT] Failed to initialize chat:', e);
    } finally {
      pendingStartRef.current = false;
    }
  })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [api, workflowConfigLoaded, currentChatId, currentWorkflowName, currentAppId, currentUserId, queryDeferStart, queryForceAsk, queryMode, conversationMode, resolveKnownWorkflowName, consumeNavigationQueryParams]);

  // Ensure resumed workflow chats always keep a canonical URL session context.
  // This prevents ambiguous reconnect behavior when the page was loaded with only ?workflow=...
  // and currentChatId came from storage or metadata.
  useEffect(() => {
    if (queryFreshStart) {
      return;
    }
    const canonicalChatId = currentChatId || activeChatId;
    if (!canonicalChatId) {
      return;
    }
    if (conversationMode !== 'workflow') {
      return;
    }

    const workflowForUrl =
      resolveKnownWorkflowName(currentWorkflowName)
      || resolveKnownWorkflowName(urlWorkflowName)
      || workflowConfig.getDefaultWorkflow();
    if (!workflowForUrl) {
      return;
    }

    const params = new URLSearchParams(location.search || '');
    const chatParam = params.get('chat_id');
    const modeParam = params.get('mode');
    const workflowParam = params.get('workflow');

    if (
      chatParam === String(canonicalChatId)
      && modeParam === 'workflow'
      && workflowParam === workflowForUrl
    ) {
      return;
    }

    params.set('workflow', workflowForUrl);
    params.set('chat_id', String(canonicalChatId));
    params.set('mode', 'workflow');
    const nextSearch = params.toString();
    navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true });
  }, [
    activeChatId,
    conversationMode,
    currentChatId,
    currentWorkflowName,
    location.pathname,
    location.search,
    navigate,
    queryFreshStart,
    resolveKnownWorkflowName,
    urlWorkflowName,
    workflowConfig,
  ]);

  // Guard against stale chat IDs restored from local storage (e.g. after cleanse).
  // If the chat does not exist anymore, clear it so preflight can create a new one.
  useEffect(() => {
    if (!api) return;
    if (!workflowConfigLoaded) return;
    if (!currentAppId) return;
    if (!currentChatId) return;
    if (validatingChatIdRef.current) return;
    if (validatedChatIdRef.current === currentChatId) return;

    validatingChatIdRef.current = true;
    (async () => {
      try {
        const workflowForCheck =
          resolveKnownWorkflowName(currentWorkflowName)
          || resolveKnownWorkflowName(urlWorkflowName)
          || workflowConfig.getDefaultWorkflow()
          || 'GreenRoom';

        const resp = await fetch(
          `${api.getHttpBaseUrl()}/api/chats/exists/${currentAppId}/${workflowForCheck}/${currentChatId}`
        );
        if (!resp.ok) {
          validatedChatIdRef.current = currentChatId;
          return;
        }

        const result = await resp.json();
        if (result?.exists === false) {
          console.warn('🧹 [CHAT_GUARD] Stale chat id detected; resetting:', currentChatId);
          try {
            const activeWs = wsRef.current;
            if (activeWs && typeof activeWs.close === 'function') {
              activeWs.close();
            }
          } catch {}

          clearStoredArtifactState(currentChatId);
          clearStoredChatCacheSeed(currentChatId);
          setStoredActiveChatId(null);
          validatedChatIdRef.current = null;
          setActiveChatId(null);
          setMessagesWithLogging([]);
          setCurrentChatId(null);
          return;
        }

        validatedChatIdRef.current = currentChatId;
      } catch (err) {
        console.warn('⚠️ [CHAT_GUARD] Chat existence validation failed, keeping current chat id:', err);
        validatedChatIdRef.current = currentChatId;
      } finally {
        validatingChatIdRef.current = false;
      }
    })();
  }, [
    api,
    workflowConfigLoaded,
    currentAppId,
    currentChatId,
    currentWorkflowName,
    urlWorkflowName,
    resolveKnownWorkflowName,
    setActiveChatId,
    setMessagesWithLogging,
  ]);

  // Expose a helper to force-reset the current chat client-side (can be wired to a debug button later)
  const forceResetChat = useCallback(() => {
    const current = getStoredActiveChatId();
    if (current) {
      clearStoredArtifactState(current);
      clearStoredChatCacheSeed(current);
    }
    setCurrentArtifactMessages([]);
    lastArtifactEventRef.current = null;
    artifactRestoredOnceRef.current = false;
    artifactCacheValidRef.current = false;
    setCurrentChatId(null);
  }, []);

  // Dev: expose reset helper & read cacheSeed to avoid unused warnings
  useEffect(() => {
    // Use cacheSeed in a benign way (log only when it changes)
    if (cacheSeed !== null) {
      // Minimal, low-noise log – toggle off by removing line if undesired
      console.debug('🧬 Active cacheSeed now', cacheSeed);
    }
    // Expose forceResetChat for manual debugging in console
    try { window.__mozaiksForceResetChat = forceResetChat; } catch {}
    
    // Expose artifact inspection helper
    try {
      window.__mozaiksInspectArtifacts = () => {
        const keys = [];
        const chatId = currentChatId || getStoredActiveChatId();
        if (chatId) {
          const currentArtifact = readStoredCurrentArtifact(chatId);
          const lastArtifact = readStoredLastArtifact(chatId);
          const cacheSeedValue = getStoredChatCacheSeed(chatId);
          keys.push({ key: `mozaiks.current_artifact.${chatId}`, value: currentArtifact ? JSON.stringify(currentArtifact).slice(0, 200) : null });
          keys.push({ key: `mozaiks.last_artifact.${chatId}`, value: lastArtifact ? JSON.stringify(lastArtifact).slice(0, 200) : null });
          keys.push({ key: `${LOCAL_STORAGE_KEY}.cache_seed.${chatId}`, value: cacheSeedValue });
        }
        
        console.table(keys);
        return keys;
      };
    } catch {}
  }, [cacheSeed, forceResetChat, currentChatId]);

  // Connect to streaming when API becomes available and chat ID exists
  useEffect(() => {
    if (!api) return;
    
    // Wait for workflow configuration to be loaded before connecting
    if (!workflowConfigLoaded) {
  // console.debug('Waiting for workflow configuration to load...');
      return;
    }
    
    // Require chat ID to connect
    if (!currentChatId) {
  // console.debug('Waiting for chat ID to be available...');
      return;
    }
    
    // Prevent duplicate connections
    if (connectionInProgressRef.current) {
      // console.debug('Connection already initialized or in progress, skipping...');
      return;
    }
    if (wsRef.current && wsRef.current.chatId === currentChatId) {
      return;
    }
    
  // console.debug('Establishing WebSocket connection');
    
    // Mark connection as in progress immediately to prevent duplicates
    connectionInProgressRef.current = true;
    setConnectionInitialized(true);
    
    // Define connection functions inside useEffect to avoid dependency issues
    const connectWebSocket = (resolvedWorkflowName = null) => {
      // WebSocket connection for chat communication
      if (!currentChatId) {
        console.error('WebSocket requires existing chat ID');
        return () => {};
      }
      
      setConnectionStatus('connecting');
      setTransportType('websocket');

      const workflowName = resolveKnownWorkflowName(resolvedWorkflowName)
        || resolveKnownWorkflowName(urlWorkflowName)
        || resolveKnownWorkflowName(currentWorkflowName)
        || resolveWorkflow(resolvedWorkflowName || urlWorkflowName || currentWorkflowName);
      if (!workflowName) {
        console.warn('⚠️ No workflow available to connect');
        setConnectionInitialized(false);
        connectionInProgressRef.current = false;
        return () => {};
      }

      // If a stale connection is still around from a prior chat/session, close it first.
      if (wsRef.current && wsRef.current.chatId && wsRef.current.chatId !== currentChatId) {
        try {
          wsRef.current.close();
        } catch {}
        wsRef.current = null;
        setWs(null);
      }

      let connection = null;
      connection = api.createWebSocketConnection(
        currentAppId,
        currentUserId,
        {
          onOpen: () => {
            if (!connection || wsRef.current !== connection) {
              return;
            }
            // console.debug('WebSocket connection established');
            setConnectionStatus('connected');
            setLoading(false);
            // Show one-time spinner ONLY if it has never been shown and never been hidden
            if (!initSpinnerHiddenOnceRef.current && !initSpinnerShownRef.current) {
              setShowInitSpinner(true);
              initSpinnerShownRef.current = true;
            } else {
              // Ensure we never regress into showing again this session
              if (initSpinnerHiddenOnceRef.current) {
                initSpinnerShownRef.current = true; // lock state
              }
            }
            if (conversationModeRef.current === 'workflow') {
              setStoredActiveChatId(currentChatId);
            }
          },
          onMessage: (data) => {
            // Ignore callbacks from stale sockets that are no longer active.
            if (!connection || wsRef.current !== connection) {
              return;
            }
            if (handleIncomingRef.current) {
              handleIncomingRef.current(data);
            }
          },
          onError: (error) => {
            if (!connection || wsRef.current !== connection) {
              return;
            }
            console.error("WebSocket error:", error);
            setConnectionStatus('error');
            setConnectionInitialized(false);
            connectionInProgressRef.current = false;
            setLoading(false);
            setTimeout(() => {
              setConnectionRetryNonce((prev) => prev + 1);
            }, 250);
          },
          onClose: () => {
            if (!connection || wsRef.current !== connection) {
              return;
            }
            // console.debug('WebSocket connection closed');
            setConnectionStatus('disconnected');
            setConnectionInitialized(false);
            connectionInProgressRef.current = false;
            wsRef.current = null;
            setWs(null);
            setTimeout(() => {
              setConnectionRetryNonce((prev) => prev + 1);
            }, 250);
          }
        },
        workflowName,
        currentChatId // Pass the existing chat ID
      );

      if (!connection) {
        setConnectionStatus('error');
        setLoading(false);
        setConnectionInitialized(false);
        connectionInProgressRef.current = false;
        return () => {};
      }
      connection.chatId = currentChatId;
      setWs(connection);
      wsRef.current = connection;
  // console.debug('WebSocket connection established:', !!ws);
      return () => {
        if (connection) {
          connection.close();
          // console.debug('WebSocket connection closed');
        }
        if (wsRef.current === connection) {
          wsRef.current = null;
          setWs(null);
        }
      };
    };

    // Query the workflow transport type and use WebSocket connection
    const connectWithCorrectTransport = async () => {
      try {
        // Unified workflow resolution: URL explicit → backend entry_point → singleton → null
        const workflowName = resolveKnownWorkflowName(urlWorkflowName)
          || resolveKnownWorkflowName(currentWorkflowName)
          || resolveWorkflow(urlWorkflowName)
          || resolveWorkflow(currentWorkflowName);
        if (!workflowName) {
          throw new Error('No workflow available');
        }
  // console.debug('Using workflow name:', workflowName);
        
        // Query transport info from backend and use it (was previously unused)
        const transportInfo = await api.getWorkflowTransport(workflowName);
        // transportInfo example: { transport: 'websocket' | 'sse' | 'poll', allow_resume: true }
        if (transportInfo && transportInfo.transport) {
          setTransportType(transportInfo.transport);
        } else {
          setTransportType('websocket');
        }
        // Expose transport flags or capabilities if provided
        if (transportInfo && transportInfo.allow_resume === false) {
          console.debug('Transport indicates resume is disabled for', workflowName);
        }
        setCurrentWorkflowName(workflowName);
        return connectWebSocket(workflowName);
      } catch (error) {
        console.error('Error querying workflow transport:', error);
        // Fallback to WebSocket
        const fallbackWf = resolveKnownWorkflowName(currentWorkflowName)
          || resolveKnownWorkflowName(urlWorkflowName)
          || resolveWorkflow(currentWorkflowName)
          || resolveWorkflow(urlWorkflowName);
        if (!fallbackWf) {
          console.warn('⚠️ No workflow available for fallback');
          setConnectionInitialized(false);
          connectionInProgressRef.current = false;
          return () => {};
        }
        setTransportType('websocket');
        setCurrentWorkflowName(fallbackWf);
        return connectWebSocket(fallbackWf);
      }
    };
    
    // Execute the async function and handle cleanup
    let effectDisposed = false;
    let cleanup = () => {};
    connectWithCorrectTransport().then(cleanupFn => {
      const normalizedCleanup = typeof cleanupFn === 'function' ? cleanupFn : () => {};
      if (effectDisposed) {
        try { normalizedCleanup(); } catch {}
        return;
      }
      cleanup = normalizedCleanup;
    }).catch(error => {
      console.error('Failed to connect with transport:', error);
      // Reset connection flags on error so user can retry
      setConnectionInitialized(false);
      connectionInProgressRef.current = false;
    });
    
    return () => {
      effectDisposed = true;
      if (cleanup) cleanup();
      // Reset the in-progress flag when component unmounts
      connectionInProgressRef.current = false;
    };
  }, [api, currentAppId, currentUserId, workflowConfigLoaded, currentChatId, urlWorkflowName, currentWorkflowName, resolveKnownWorkflowName, connectionRetryNonce]);

  // Retry connection function
  const retryConnection = useCallback(() => {
  // console.debug('Retrying connection...');
    setConnectionInitialized(false);
    connectionInProgressRef.current = false;
    setConnectionStatus('disconnected');
    
    // Trigger reconnection by setting up the connection again
    setTimeout(() => {
      if (currentChatId && workflowConfigLoaded) {
        setConnectionStatus('connecting');
      }
    }, 1000);
  }, [currentChatId, workflowConfigLoaded]);

  // Subscribe to DynamicUIHandler updates and insert tool_call render events into chat messages
  useEffect(() => {
    // Bridge workflow UI tool calls into the chat message stream
    const unsubscribe = dynamicUIHandler.onUIUpdate((update) => {
      try {
        if (!update || !update.type) return;

        // ui.update — patch live component payload without re-mounting
        if (update.type === 'ui.update') {
          const { tool_call_id: patchId, patch = {} } = update;
          if (patchId) {
            const applyPatch = (msg) => {
              if (msg?.toolCall?.tool_call_id === patchId || msg?.metadata?.toolCallId === patchId) {
                return { ...msg, toolCall: msg.toolCall ? { ...msg.toolCall, payload: { ...(msg.toolCall.payload || {}), ...patch } } : msg.toolCall };
              }
              return msg;
            };
            setMessagesWithLogging(prev => prev.map(applyPatch));
            setCurrentArtifactMessages(prev => prev.map(applyPatch));
          }
          return;
        }

        // ui.dismiss — remove rendered component
        if (update.type === 'ui.dismiss') {
          const { tool_call_id: dismissId } = update;
          if (dismissId) {
            setMessagesWithLogging(prev => prev.filter(
              msg => !(
                msg?.metadata?.toolCallId === dismissId
                && (
                  msg?.metadata?.type === 'tool_call_agent_message'
                  || msg?.metadata?.type === 'composer_tool_call'
                  || msg?.metadata?.hideInTranscript
                )
              )
            ));
            if (lastArtifactEventRef.current === dismissId) {
              setIsSidePanelOpen(false);
              lastArtifactEventRef.current = null;
              artifactCacheValidRef.current = false;
              setCurrentArtifactMessages([]);
              if (currentChatId) clearStoredArtifactState(currentChatId);
            }
          }
          return;
        }

        // tool_call (InputRequestEvent path) and ui.render (typed UI path)
        if (update.type === 'tool_call' || update.type === 'ui.render') {
          setPendingWorkflowReply(null);
          if (dispatchSurfaceEvent) {
            dispatchSurfaceEvent(update);
          }
          const { tool_name, payload = {}, tool_call_id, workflow_name, onResponse, display } = update;
          // If this UI tool requests artifact display, auto-open the ArtifactPanel like OpenAI/Claude canvases
          const displayMode = (display || payload.display || payload.mode);
          const resolvedAgentName =
            payload.agentName
            || payload.agent_name
            || update.agent_name
            || update.agent
            || 'Agent';
          const composerPrompt = String(
            payload.prompt
              || payload.agent_message
              || payload.description
              || ''
          ).trim();
          if (displayMode === 'composer') {
            setMessagesWithLogging((prev) => {
              const withoutThinking = prev.filter((msg) => !msg.isThinking);
              const messageKey = tool_call_id || tool_name;
              const composerMessage = {
                id: `tool-call-${messageKey || Date.now()}`,
                sender: 'agent',
                agentName: resolvedAgentName,
                content: '',
                isStreaming: false,
                toolCall: {
                  tool_name,
                  payload: {
                    ...payload,
                    ...(composerPrompt ? { prompt: composerPrompt } : {}),
                  },
                  tool_call_id,
                  workflow_name,
                  onResponse,
                  display: 'composer',
                  component_type: update.component_type || payload.component_type || tool_name,
                },
                metadata: {
                  type: 'composer_tool_call',
                  toolCallId: messageKey,
                  tool_name,
                  display: 'composer',
                  hideInTranscript: true,
                },
              };

              const existingIndex = withoutThinking.findIndex(
                (msg) => msg?.metadata?.toolCallId === messageKey && msg?.metadata?.hideInTranscript
              );
              if (existingIndex === -1) {
                return [...withoutThinking, composerMessage];
              }

              const updated = [...withoutThinking];
              updated[existingIndex] = {
                ...updated[existingIndex],
                ...composerMessage,
                id: updated[existingIndex]?.id || composerMessage.id,
              };
              return updated;
            });
            return;
          }
          const isViewDisplay = displayMode === 'view' || displayMode === 'fullscreen';
          if (isViewDisplay) {
            const snapshotState = surfaceStateRef.current;
            if (snapshotState?.layoutMode !== 'view') {
              const snapshotLayout = snapshotState?.layoutMode || 'split';
              viewArtifactSnapshotRef.current = {
                isOpen: Boolean(snapshotState?.artifact?.panelOpen),
                layoutMode: snapshotLayout,
                messages: Array.isArray(currentArtifactMessagesRef.current) ? [...currentArtifactMessagesRef.current] : [],
              };
            }
          }
          const shouldRenderArtifact = displayMode === 'artifact' || isViewDisplay;
          if (shouldRenderArtifact) {
            if (isMobileView) {
              setMobileDrawerState('expanded');
              setHasUnseenArtifact(false);
            }
            if (!dispatchSurfaceEvent) {
              setIsSidePanelOpen(true);
            }
            const agentText = payload.agent_message || payload.description || null;
            if (agentText) {
              setMessagesWithLogging((prev) => {
                const withoutThinking = prev.filter(msg => !msg.isThinking);
                const hasExisting = withoutThinking.some(msg => msg?.metadata?.toolCallId === (tool_call_id || tool_name) && msg?.metadata?.type === 'tool_call_agent_message');
                if (hasExisting) return withoutThinking;
                return [
                  ...withoutThinking,
                  {
                    id: `tool-call-msg-${tool_call_id || Date.now()}`,
                    sender: 'agent',
                    agentName: resolvedAgentName,
                    content: agentText,
                    isStreaming: false,
                    metadata: { type: 'tool_call_agent_message', toolCallId: tool_call_id || tool_name, tool_name }
                  }
                ];
              });
            }
            // Create artifact payload for ArtifactPanel to render
            let artifactPayload = null;
            try {
              artifactPayload = {
                ...payload,
                artifact_id: deriveArtifactId(payload, tool_call_id || tool_name || null),
              };
              const artifactMsg = {
                id: `tool-call-artifact-${tool_call_id || Date.now()}`,
                sender: 'agent',
                agentName: resolvedAgentName,
                content: artifactPayload.structured_output || artifactPayload.content || artifactPayload || {},
                isStreaming: false,
                toolCall: { tool_name, payload: artifactPayload, tool_call_id, workflow_name, onResponse, display: displayMode, component_type: update.component_type || payload.component_type || tool_name }
              };
              setCurrentArtifactMessages([artifactMsg]);
              artifactCacheValidRef.current = true;
              
              // Also cache to platform storage for persistence across panel open/close
              try {
                if (currentChatId) {
                  // Create a serializable version without the function
                  const serializableArtifact = {
                    ...artifactMsg,
                    toolCall: {
                      ...artifactMsg.toolCall,
                      onResponse: null // Functions can't be serialized, will be reconstructed on restore
                    }
                  };
                  writeStoredCurrentArtifact(currentChatId, serializableArtifact);
                }
              } catch (e) { console.warn('Failed to cache artifact', e); }
            } catch (e) { console.warn('Failed to set artifact message', e); }
            // Remember this artifact to collapse on next sequence
            lastArtifactEventRef.current = tool_call_id || tool_name || 'artifact';
            // Persist minimal artifact session state for graceful refresh restore
            try {
              if (currentChatId) {
                const cache = {
                  tool_name,
                  tool_call_id: tool_call_id || null,
                  workflow_name,
                  payload: artifactPayload || payload,
                  display: displayMode || 'artifact',
                  ts: Date.now(),
                };
                writeStoredLastArtifact(currentChatId, cache);
              }
            } catch {}
            // Persist nav trigger cache if configured
            try {
              const navCache = navCacheContextRef.current;
              if (navCache?.cache_ttl && artifactPayload) {
                const artifactWorkflow = workflow_name || currentWorkflowName;
                if (navCache.workflow && artifactWorkflow && navCache.workflow !== artifactWorkflow) {
                  return;
                }
                const cacheWorkflow = navCache.workflow || artifactWorkflow;
                if (cacheWorkflow) {
                  writeNavigationCache(
                    cacheWorkflow,
                    navCache?.input ?? null,
                    {
                      tool_name,
                      tool_call_id: tool_call_id || null,
                      workflow_name: cacheWorkflow,
                      payload: artifactPayload,
                      display: displayMode || 'artifact',
                    },
                    navCache.cache_ttl,
                  );
                }
              }
            } catch (e) {
              console.warn('Failed to cache nav-trigger artifact', e);
            }
            // Don't inject artifact UIs into the chat feed; they'll render in ArtifactPanel only
            return;
          }
          setMessagesWithLogging((prev) => {
            const thinkingMessages = prev.filter(m => m.isThinking);
            if (thinkingMessages.length > 0) {
            }
            
            const withoutThinking = prev.filter(m => !m.isThinking); // Remove thinking bubbles when UI tool event arrives
            return [
              ...withoutThinking,
              {
                id: `tool-call-${tool_call_id || Date.now()}`,
                sender: 'agent',
                agentName: resolvedAgentName,
                content: (payload.agent_message || payload.description || ''), // Surface agent context alongside inline UI
                isStreaming: false,
                toolCall: {
                  tool_name,
                  payload,
                  tool_call_id,
                  workflow_name,
                  onResponse,
                  // Surface display mode for inline Completed chip logic
                  display: displayMode || 'inline',
                  component_type: update.component_type || payload.component_type || tool_name,
                },
              },
            ];
          });
        }
      } catch (err) {
        console.error('❌ Failed to handle DynamicUIHandler update in ChatPage:', err);
      }
    });
    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
    }, [setMessagesWithLogging, currentChatId]);

  const isComposerInputRequestToolCall = (toolCall, message = null) => {
    if (!toolCall?.tool_call_id || message?.tool_call_completed) {
      return false;
    }
    const payload = toolCall.payload || {};
    const interactionType = String(
      toolCall.interaction_type || payload.interaction_type || ''
    ).trim().toLowerCase();
    if (interactionType !== 'input_request') {
      return false;
    }
    if (Boolean(payload.password)) {
      return false;
    }
    const displayMode = String(
      toolCall.display || payload.display || payload.mode || ''
    ).trim().toLowerCase();
    return displayMode === 'composer';
  };

  const findPendingComposerInputRequestToolCall = (messageList) => {
    if (!Array.isArray(messageList) || messageList.length === 0) {
      return null;
    }
    for (let index = messageList.length - 1; index >= 0; index -= 1) {
      const message = messageList[index];
      const toolCall = message?.toolCall;
      if (isComposerInputRequestToolCall(toolCall, message)) {
        return toolCall;
      }
    }
    return null;
  };

  const clearPendingComposerInputRequestToolCall = useCallback((toolCallId) => {
    if (!toolCallId) {
      return;
    }
    setMessagesWithLogging((prev) =>
      prev.filter(
        (msg) => !(
          msg?.metadata?.toolCallId === toolCallId
          && msg?.metadata?.hideInTranscript
        )
      )
    );
  }, [setMessagesWithLogging]);

  const buildToolCallTextResponseAction = (toolCall, text = '') => ({
    type: 'tool_call_response',
    tool_name: toolCall.tool_name,
    tool_call_id: toolCall.tool_call_id,
    response: {
      status: 'submitted',
      text,
      user_input: text,
      user_response: text,
    },
  });

  const handlePendingComposerInputSkip = async (toolCall) => {
    if (!toolCall?.tool_call_id) {
      return;
    }
    await handleAgentAction(buildToolCallTextResponseAction(toolCall, ''));
    clearPendingComposerInputRequestToolCall(toolCall.tool_call_id);
    setLoading(true);
  };

  const sendMessage = async (messageContent) => {

    const artifactContextPayload = messageContent?.artifactContext || currentArtifactContext || null;
    
    // Create a properly structured user message
    const userMessage = {
      id: Date.now().toString(),
      sender: 'user',  // Use 'user' to align message to the right
      agentName: 'You',
      content: messageContent.content,
      timestamp: Date.now(),
      isStreaming: false
    };
    
    
    // Optimistic add: add user message to chat immediately, then add thinking indicator
    setMessagesWithLogging(prevMessages => {
      const existingThinking = prevMessages.filter(m => m.isThinking);
      if (existingThinking.length > 0) {
      }
      
      const thinkingBubble = {
        id: `thinking-${Date.now()}`,
        sender: 'agent',
        agentName: 'Agent',
        content: '',
        isThinking: true,
        timestamp: Date.now()
      };
      
      
      return [
        ...prevMessages.filter(m => !m.isThinking), // Remove any existing thinking bubbles
        userMessage,
        thinkingBubble
      ];
    });
    
    if (conversationMode === 'ask') {
      const targetChatId = currentChatId;
      if (!targetChatId) {
        console.error('❌ [SEND] No chat available for ask-mode message');
        return;
      }

      if (shouldOfferHumanSupport(messageContent.content)) {
        const transcriptSnapshot = buildSupportConversationTranscript(
          [
            ...(messagesRef.current || []),
            userMessage,
          ],
          { includeMessage: messageContent.content },
        );
        if (!escalationCardInjectedRef.current) {
          escalationCardInjectedRef.current = true;
          setMessagesWithLogging(prev => [
            ...prev.filter(m => !m.isThinking),
            {
              id: `escalation-${Date.now()}`,
              sender: 'agent',
              agentName: 'Support',
              content: '',
              toolCall: {
                tool_name: 'EscalationCard',
                payload: {
                  conversationTranscript: transcriptSnapshot,
                },
              },
              isStreaming: false,
            },
          ]);
        } else {
          setMessagesWithLogging(prev => prev.filter(m => !m.isThinking));
        }
        setLoading(false);
        return;
      }

      const didSend = sendWsMessage({
        type: 'user.input.submit',
        chat_id: targetChatId,
        text: messageContent.content,
        context: {
          source: 'chat_interface',
          conversation_mode: 'ask',
          general_chat_id: activeGeneralChatId || undefined,
          ...(artifactContextPayload ? { artifact_context: artifactContextPayload } : {}),
        },
      });
      if (!didSend) {
        console.error('❌ [SEND] Failed to send ask-mode message (socket unavailable)');
        setMessagesWithLogging(prev => [
          ...prev.filter(m => !m.isThinking),
        ]);
        setLoading(false);
      } else {
        setLoading(true);
      }
      return;
    }

      const pendingComposerInputRequestToolCall = findPendingComposerInputRequestToolCall(messagesRef.current);
      if (pendingComposerInputRequestToolCall?.tool_call_id) {
        await handleAgentAction(
          buildToolCallTextResponseAction(
            pendingComposerInputRequestToolCall,
            messageContent.content,
          ),
        );
        clearPendingComposerInputRequestToolCall(pendingComposerInputRequestToolCall.tool_call_id);
        setLoading(true);
        return;
      }

      // Deferred workflow launch: create the runtime chat only when the user
      // sends the first real message, then route that message into the run.
      try {
      let targetChatId = currentChatId;
      let targetWorkflowName = resolveKnownWorkflowName(currentWorkflowName)
        || resolveKnownWorkflowName(urlWorkflowName)
        || resolveWorkflow(currentWorkflowName)
        || resolveWorkflow(urlWorkflowName);

      if (!targetChatId) {
        if (!targetWorkflowName) {
          console.error('❌ [SEND] No workflow available for deferred message');
          return;
        }
        const startResult = await api.startChat(
          currentAppId,
          targetWorkflowName,
          currentUserId,
          {},
          queryContext,
          { trigger_source: queryTriggerSource },
          { forceNew: queryFreshStart },
        );
        targetChatId = startResult?.chat_id || startResult?.id || null;
        targetWorkflowName = startResult?.workflow_name || targetWorkflowName;
        if (!targetChatId) {
          console.error('❌ [SEND] Could not create workflow chat for deferred message');
          return;
        }
        setCurrentChatId(targetChatId);
        setActiveChatId(targetChatId);
        setCurrentWorkflowName(targetWorkflowName);
        setActiveWorkflowName(targetWorkflowName);
        setConversationMode('workflow');

        const nextParams = new URLSearchParams(location.search || '');
        nextParams.delete('context');
        nextParams.delete('new');
        nextParams.delete('fresh');
        nextParams.delete('force_new');
        nextParams.delete('defer_start');
        nextParams.set('workflow', targetWorkflowName);
        nextParams.set('chat_id', targetChatId);
        nextParams.set('mode', 'workflow');
        const nextSearch = nextParams.toString();
        navigate(location.pathname + (nextSearch ? `?${nextSearch}` : ''), { replace: true });
      }
      
      const success = await api.sendMessageToWorkflow(
        messageContent.content, 
        currentAppId, 
        currentUserId, 
        targetWorkflowName,
        targetChatId,
        artifactContextPayload ? { artifact_context: artifactContextPayload } : null
      );
      if (success) {
        if (pendingWorkflowReply) {
          setPendingWorkflowReply(null);
        }
        setLoading(true);
      }
    } catch (error) {
      console.error('❌ [SEND] Failed to send message via WebSocket:', error);
    }
  };

  const sendWsMessage = useCallback((payload) => {
    const activeWs = wsRef.current;
    if (!activeWs || typeof activeWs.send !== 'function') {
      console.warn('⚠️ No websocket connection available for payload', payload?.type || payload);
      return false;
    }
    try {
      activeWs.send(payload);
      return true;
    } catch (err) {
      console.error('Failed to send websocket payload', payload, err);
      return false;
    }
  }, []);

  const isViewArtifactMessage = useCallback((msg) => {
    const toolCall = msg?.toolCall;
    if (!toolCall) return false;
    const display = toolCall.display || toolCall?.payload?.display || toolCall?.payload?.mode;
    if (display === 'view' || display === 'fullscreen') return true;
    if (toolCall?.payload?.page && toolCall?.payload?.presentation === 'artifact') return true;
    return false;
  }, []);

  const restoreViewSnapshot = useCallback(() => {
    const snapshot = viewArtifactSnapshotRef.current;
    if (!snapshot) return false;
    const nextLayout = snapshot.layoutMode || (snapshot.isOpen ? 'split' : 'full');
    if (setLayoutMode) setLayoutMode(nextLayout);
    setIsSidePanelOpen(Boolean(snapshot.isOpen));
    if (Array.isArray(snapshot.messages)) {
      setCurrentArtifactMessages(snapshot.messages);
    }
    viewArtifactSnapshotRef.current = null;
    return true;
  }, [setLayoutMode, setIsSidePanelOpen, setCurrentArtifactMessages]);

  const clearViewArtifacts = useCallback(() => {
    if (!Array.isArray(currentArtifactMessages) || currentArtifactMessages.length === 0) return false;
    const hasView = currentArtifactMessages.some(isViewArtifactMessage);
    if (hasView) {
      setCurrentArtifactMessages([]);
      return true;
    }
    return false;
  }, [currentArtifactMessages, isViewArtifactMessage, setCurrentArtifactMessages]);

  const exitViewMode = () => {
    const restored = restoreViewSnapshot();
    if (!restored) {
      const cleared = clearViewArtifacts();
      if (setLayoutMode) setLayoutMode('split');
      setIsSidePanelOpen(!cleared);
    }
    if (isMobileView) {
      setMobileDrawerState('expanded');
    }
    if (widgetOverlayOpen) {
      setWidgetOverlayOpen(false);
    }
  };

  const describeApiError = useCallback((error) => ({
    status: error?.status || null,
    message: error?.message || String(error),
    body: error?.body || null,
  }), []);
  const {
    ensureGeneralMode,
    startNewGeneralSession,
    handleSelectGeneralChat,
    ensureWorkflowMode,
    resumeWorkflowSession,
    handleSelectWorkflowSession,
    handleConversationModeChange,
  } = useConversationModeController({
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
    queryMode,
    queryGeneralChatId,
    setConnectionInitialized,
    connectionInProgressRef,
  });

  useChatStartupEffects({
    api,
    currentAppId,
    currentUserId,
    refreshGeneralSessions,
    refreshWorkflowSessions,
    conversationBootstrapRef,
    queryMode,
    queryGeneralChatId,
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
    workflowReplayPendingRef,
  });

  const handleStartGeneralChat = useCallback(() => {
    startNewGeneralSession();
  }, [startNewGeneralSession]);

  // Force Ask mode once when transitioning away from the primary chat routes
  useEffect(() => {
    const wasPrimary = lastPrimaryRouteRef.current;
    if (!isPrimaryChatRoute && wasPrimary) {
      ensureGeneralMode();
    }
    lastPrimaryRouteRef.current = isPrimaryChatRoute;
  }, [ensureGeneralMode, isPrimaryChatRoute]);

  const sendArtifactAction = useCallback((action, contextData = {}) => {
    if (!action || !action.tool) {
      return null;
    }

    const artifactPayload = contextData?.artifactPayload || contextData?.payload || contextData || {};
    const fallbackId = currentArtifactContext?.id || (lastArtifactEventRef.current ? String(lastArtifactEventRef.current) : null);
    const artifactId = deriveArtifactId(artifactPayload, fallbackId);
    const actionId = (crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

    const paramsContext = {
      artifact: artifactPayload,
      ...artifactPayload,
      ...contextData,
      artifact_id: artifactId,
      app_id: currentAppId,
      chat_id: currentChatId,
      user_id: currentUserId,
      workflow_name: currentWorkflowName,
    };
    const params = interpolateParams(action.params || {}, paramsContext);

    const payload = {
      type: 'artifact.action',
      action_id: actionId,
      artifact_id: artifactId,
      tool: action.tool,
      params,
      context: {
        chat_id: currentChatId,
        app_id: currentAppId,
        user_id: currentUserId,
        workflow_name: currentWorkflowName,
      }
    };

    const connection = wsRef.current;
    if (connection && typeof connection.send === 'function') {
      connection.send(payload);
      setActionStatusMap((prev) => ({
        ...prev,
        [actionId]: { status: 'pending', tool: action.tool, artifact_id: artifactId, started_at: Date.now() }
      }));
      if (action.optimistic) {
        applyOptimisticForAction(actionId, artifactId, action.optimistic);
      }
    } else {
      console.warn('No WebSocket connection available for artifact.action');
      return null;
    }

    return actionId;
  }, [
    currentArtifactContext,
    currentChatId,
    currentUserId,
    currentAppId,
    currentWorkflowName,
    applyOptimisticForAction,
  ]);

  // Handle agent UI actions
  const handleAgentAction = async (action) => {

    try {
      // EscalationCard: create a support request from the current session, then
      // navigate to the profile support tab so the user sees the new ticket immediately.
      if (
        action.type === 'tool_call_response' &&
        action.tool_name === 'EscalationCard' &&
        action.response?.action === 'open_support'
      ) {
        const latestUserMessage = [...(messagesRef.current || [])]
          .reverse()
          .find((message) => message?.sender === 'user' && String(message?.content || '').trim());
        const supportMessage = String(latestUserMessage?.content || 'Requested operator assistance from chat.').trim();
        const conversationTranscript = Array.isArray(action?.payload?.conversationTranscript)
          ? action.payload.conversationTranscript
          : buildSupportConversationTranscript(messagesRef.current, {
            includeMessage: supportMessage,
          });
        try {
          const supportScope = await resolveSupportRequestScope({
            api,
            auth,
            config,
            user,
            fallbackAppId: currentAppId,
            fallbackUserId: currentUserId,
          });
          const response = await fetch('/api/modules/workspace_support/create_support_request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildSupportRequestPayload({
              message: supportMessage,
              appId: supportScope.appId || currentAppId,
              userId: supportScope.userId || currentUserId,
              severity: 'low',
              pageUrl: window.location.href,
              pageTitle: 'Chat session',
              conversationTranscript,
            })),
          });
          const created = response.ok ? await response.json().catch(() => ({})) : {};
          const requestId = created?.request_id || created?.request?.request_id || created?.result?.request_id || created?.data?.request_id;
          const createdAppId =
            created?.app_id ||
            created?.subject_app_id ||
            created?.request?.app_id ||
            created?.request?.subject_app_id ||
            created?.result?.app_id ||
            created?.result?.subject_app_id ||
            created?.data?.app_id ||
            created?.data?.subject_app_id ||
            supportScope.appId ||
            currentAppId;
          if (requestId) {
            navigate(buildUserSupportPath({ requestId, appId: createdAppId }));
            return;
          }
        } catch (_) {}
        navigate(buildUserSupportPath({ appId: currentAppId }));
        return;
      }

      // Handle UI tool responses for the dynamic UI system
      if (action.type === 'tool_call_response') {

        // If this response corresponds to the most recent artifact event, close the panel immediately
        if (lastArtifactEventRef.current && (!action.tool_call_id || action.tool_call_id === lastArtifactEventRef.current)) {
          setIsSidePanelOpen(false);
          if (dispatchSurfaceAction) {
            dispatchSurfaceAction({ type: 'ARTIFACT_CLEARED' });
          }
            lastArtifactEventRef.current = null;
          setCurrentArtifactMessages([]);
          // Clear persisted artifact cache for this chat
          if (currentChatId) {
            clearStoredArtifactState(currentChatId);
          }
        }
        // If we lack a real tool_call_id (e.g., restored artifact), don't submit to backend; just close locally
        if (!action.tool_call_id) {
          return;
        }

        const payload = {
          event_id: action.tool_call_id,
          response_data: action.response
        };

        // Send the UI tool response to the backend
        try {
          const submitPath = '/api/tool-call/respond';
          const baseUrl = api && typeof api.getHttpBaseUrl === 'function'
            ? api.getHttpBaseUrl()
            : null;
          const submitUrl = baseUrl ? `${baseUrl}${submitPath}` : submitPath;
          const headers = {
            'Content-Type': 'application/json',
          };
          const token = getAccessToken();
          if (token) {
            headers.Authorization = `Bearer ${token}`;
          }

          const response = await fetch(submitUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify(payload)
          });
          if (response.ok) {
            const result = await response.json();
          } else {
            console.error('❌ Failed to submit UI tool response:', response.statusText);
          }
        } catch (e) {
          console.error('❌ Network error submitting UI tool response:', e);
        }
        
        return;
      }
      
      // Handle other agent action types
    // console.debug('Agent action handled through workflow system');
      // Other response types will come through WebSocket from backend
    } catch (error) {
      console.error('❌ Error handling agent action:', error);
    }
  };

  const handleAppClick = () => {
  // console.debug('Navigate to App');
  };

  const handleNotificationClick = () => {
  // console.debug('Show notifications');
  };

  const emitLocalArtifactEvent = useCallback((event) => {
    try {
      if (!event || !event.tool_name) {
        return;
      }
      dynamicUIHandler.notifyUIUpdate(event);
    } catch (err) {
      console.warn('Failed to emit local artifact event', err);
    }
  }, []);

  const { handleHeaderAction } = useEmbeddedViewController({
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
    locationPathname: location.pathname,
    locationSearch: location.search,
    navigate,
    logout,
  });

  const handleReturnToChat = useCallback(() => {
    navigate('/chat');
  }, [navigate]);

  const toggleWidgetChatMinimized = useCallback(() => {
    setWidgetChatMinimized(prev => !prev);
  }, []);

  useChatArtifactLayoutEffects({
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
  });

  const toggleSidePanel = () => {
    if (layoutMode === 'view') {
      exitViewMode();
      return;
    }
    setIsSidePanelOpen((open) => {
      const next = !open;
      
      // Update fluid layout state
      if (next) {
        // Opening artifact - switch to split view
        setLayoutMode('split');
      } else {
        // Closing artifact - back to full chat
        setLayoutMode('full');
      }

      if (isMobileView) {
        setMobileDrawerState(next ? 'expanded' : 'peek');
      }
      
      if (next && currentArtifactMessages.length === 0 && artifactCacheValidRef.current) {
        // Panel opening and no current artifact - try to restore from cache
        try {
          const artifactMsg = readStoredCurrentArtifact(currentChatId);
          if (artifactMsg) {
            if (artifactMsg.toolCall && !artifactMsg.toolCall.onResponse) {
              artifactMsg.toolCall.onResponse = (response) => {
                console.warn('⚠️ This is a restored artifact - responses may not work until next interaction');
              };
            }

            setCurrentArtifactMessages([artifactMsg]);
            lastArtifactEventRef.current = artifactMsg.toolCall?.tool_call_id || 'cached';
          } else {
            artifactCacheValidRef.current = false;
          }
        } catch (e) {
          artifactCacheValidRef.current = false;
          console.warn('Failed to restore artifact from cache', e);
        }
      } else if (next && currentArtifactMessages.length === 0) {
        artifactCacheValidRef.current = false;
      }
      
      return next;
    });
  };

  // Only show artifact toggle in workflow mode (Ask mode has no artifacts)
  const artifactToggleHandler = conversationMode === 'ask'
    ? null
    : isInWidgetMode
      ? handleReturnToChat
      : (isMobileView
          ? () => {
              if (layoutMode === 'view') {
                exitViewMode();
                return;
              }
              setIsSidePanelOpen(true);
              setMobileDrawerState((prev) => (prev === 'expanded' ? 'peek' : 'expanded'));
            }
          : toggleSidePanel);

  const artifactToggleLabel = conversationMode === 'ask'
    ? undefined
    : isInWidgetMode
      ? 'Return to chat'
      : (isMobileView ? 'Artifact drawer' : undefined);

  const pendingComposerInputToolCall = findPendingComposerInputRequestToolCall(messages);

  const handleViewWidgetAsk = useCallback(() => {
    setWidgetOverlayOpen(false);
    handleConversationModeChange('ask');
  }, [handleConversationModeChange, setWidgetOverlayOpen]);

  const handleViewWidgetWorkflow = useCallback(() => {
    setWidgetOverlayOpen(false);
    handleConversationModeChange('workflow');
  }, [handleConversationModeChange, setWidgetOverlayOpen]);

  const handleOpenViewWidget = useCallback(() => {
    setWidgetOverlayOpen(true);
  }, [setWidgetOverlayOpen]);

  const handleCloseViewWidget = useCallback(() => {
    setWidgetOverlayOpen(false);
  }, [setWidgetOverlayOpen]);

  const viewWidgetChatContent = (
    <ChatInterface
      messages={messages}
      onSendMessage={sendMessage}
      loading={loading}
      onAgentAction={handleAgentAction}
      connectionStatus={connectionStatus}
      transportType={transportType}
      workflowName={currentWorkflowName}
      structuredOutputs={getWorkflow(currentWorkflowName)?.structuredOutputs || {}}
      startupMode={workflowConfig?.getWorkflowConfig(currentWorkflowName)?.startup_mode}
      onRetry={retryConnection}
      conversationMode={conversationMode}
      onConversationModeChange={handleConversationModeChange}
      onStartGeneralChat={handleStartGeneralChat}
      generalChatSummary={generalChatSummary}
      isOnChatPage={false}
      generalSessionsLoading={generalSessionsLoading}
      showAskHistoryMenu={false}
      showHistoryMenu={false}
      hideHeader={true}
      disableMobileShellChrome={true}
      plainContainer={true}
      chatTheme={chatTheme}
      appDisplayName={resolvedAppDisplayName}
      artifactContext={currentArtifactContext}
      onArtifactAction={sendArtifactAction}
      actionStatusMap={actionStatusMap}
      pendingComposerInputToolCall={pendingComposerInputToolCall}
      pendingComposerReply={pendingWorkflowReply}
      onPendingComposerInputSkip={handlePendingComposerInputSkip}
      pendingHarnessDecision={pendingHarnessDecision}
      pendingHarnessDecisionBusy={pendingHarnessDecisionBusy}
      pendingHarnessDecisionError={pendingHarnessDecisionError}
      onPendingHarnessDecisionAction={handlePendingHarnessDecisionAction}
    />
  );

  const viewWidget = isViewMode ? (
    <ChatPageViewWidget
      widgetOverlayOpen={widgetOverlayOpen}
      onOpen={handleOpenViewWidget}
      onClose={handleCloseViewWidget}
      brandLogoSrc={brandLogoSrc}
      onBrandImageError={applyBrandImageFallback}
      appDisplayName={resolvedAppDisplayName}
      onAskClick={handleViewWidgetAsk}
      onWorkflowClick={handleViewWidgetWorkflow}
      chatContent={viewWidgetChatContent}
    />
  ) : null;

  const mobileChatPaddingBottomClass = 'pb-[calc(env(safe-area-inset-bottom,0px)+0.5rem)]';
  const mobileChatTopMarginClass = 'mt-0';

  const isChatPageSurface = isPrimaryChatRoute && !isInWidgetMode && !isViewMode;
  const showAskHistorySidebar = isChatPageSurface && !isMobileView && conversationMode === 'ask';
  const showWorkflowHistorySidebar = false;
  const showMobileAskHistoryMenu = isChatPageSurface && isMobileView && conversationMode === 'ask';
  const showMobileWorkflowHistoryMenu = false;
  const showMobileHistoryMenu = showMobileAskHistoryMenu || showMobileWorkflowHistoryMenu;
  const mobileHistoryLabel = conversationMode === 'workflow' ? 'Workflows' : 'Chats';

  useEffect(() => {
    if (!showMobileHistoryMenu && isAskHistoryDrawerOpen) {
      setIsAskHistoryDrawerOpen(false);
    }
  }, [showMobileHistoryMenu, isAskHistoryDrawerOpen]);

  useEffect(() => {
    if (!isAskHistoryDrawerOpen) {
      return undefined;
    }
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [isAskHistoryDrawerOpen]);

  const currentWorkflowConfig = workflowConfig?.getWorkflowConfig(currentWorkflowName) || {};
  const uiStartupMode = currentWorkflowConfig?.startup_mode;

  const handlePendingTransitionNavigate = useCallback(
    async (option_id = null, contextVariables = {}) => {
      if (!pendingTransitionId) return false;

      // workflow_complete is a client-terminal transition — dismiss the overlay
      // without hitting the backend. The run is already finished.
      if (pendingTransitionId === 'workflow_complete') {
        setPendingTransitionId(null);
        setPendingTransitionContext({});
        return true;
      }

      const mergedContext = {
        ...(pendingTransitionContext || {}),
        ...(contextVariables || {}),
      };
      const resolvedAppId = (
        appId ||
        user?.app_id ||
        config?.chat?.defaultAppId ||
        config?.appId ||
        config?.app_id ||
        'default'
      );
      const resolvedUserId = user?.id || user?.user_id || user?.email || null;

      try {
        const res = await fetch('/api/transitions/resolve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            transition_id: pendingTransitionId,
            option_id,
            context_variables: mergedContext,
            app_id: resolvedAppId,
            user_id: resolvedUserId,
          }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || 'Failed to resolve transition');
        }

        const data = await res.json();
        if (data.resolution_type === 'transition' && data.transition?.id) {
          setPendingTransitionContext(data.context_variables ?? mergedContext);
          setPendingTransitionId(data.transition.id);
          return true;
        }

        if (data.resolution_type === 'workflow' && data.chat_id && data.workflow_id) {
          setPendingTransitionId(null);
          setPendingTransitionContext({});
          setCurrentChatId(data.chat_id);
          setActiveChatId(data.chat_id);
          setCurrentWorkflowName(data.workflow_id);
          setActiveWorkflowName(data.workflow_id);
          setConversationMode('workflow');
          const chatParams = new URLSearchParams({
            mode: 'workflow',
            workflow: String(data.workflow_id),
            chat_id: String(data.chat_id),
          });
          navigate(`/chat?${chatParams.toString()}`);
          return true;
        }

        if (data.resolution_type === 'chat_session' && data.chat_id && data.workflow_id) {
          setPendingTransitionId(null);
          setPendingTransitionContext({});
          setCurrentChatId(data.chat_id);
          setActiveChatId(data.chat_id);
          setCurrentWorkflowName(data.workflow_id);
          setActiveWorkflowName(data.workflow_id);
          setConversationMode('workflow');
          return true;
        }

        throw new Error('Transition resolution returned an unsupported response');
      } catch (err) {
        console.error('[ChatPage] transition resolution failed:', err);
        return false;
      }
    },
    [
      appId,
      config,
      navigate,
      pendingTransitionContext,
      pendingTransitionId,
      setActiveChatId,
      setActiveWorkflowName,
      setConversationMode,
      user,
    ]
  );

  const chatInterface = (
    <ErrorBoundary fallback={<ChatInterfaceErrorFallback onRetry={() => window.location.reload()} />}>
      <ChatInterface
        messages={messages}
        onSendMessage={sendMessage}
        loading={loading}
        onAgentAction={handleAgentAction}
        onArtifactToggle={artifactToggleHandler}
        artifactToggleLabel={artifactToggleLabel}
        connectionStatus={connectionStatus}
        transportType={transportType}
        workflowName={currentWorkflowName}
        structuredOutputs={getWorkflow(currentWorkflowName)?.structuredOutputs || {}}
        startupMode={uiStartupMode}
        onRetry={retryConnection}
        onBrandClick={undefined}
        conversationMode={conversationMode}
        onConversationModeChange={handleConversationModeChange}
        modeTogglePending={modeChangePending}
        onStartGeneralChat={handleStartGeneralChat}
        generalChatSummary={generalChatSummary}
        isOnChatPage={isChatPageSurface}
        generalSessionsLoading={generalSessionsLoading}
        showAskHistoryMenu={showMobileAskHistoryMenu}
        onAskHistoryToggle={() => setIsAskHistoryDrawerOpen((prev) => !prev)}
        showHistoryMenu={showMobileHistoryMenu}
        onHistoryToggle={() => setIsAskHistoryDrawerOpen((prev) => !prev)}
        historyMenuLabel={mobileHistoryLabel}
              chatTheme={chatTheme}
              appDisplayName={resolvedAppDisplayName}
              artifactContext={currentArtifactContext}
              onArtifactAction={sendArtifactAction}
        actionStatusMap={actionStatusMap}
        pendingComposerInputToolCall={pendingComposerInputToolCall}
        pendingComposerReply={pendingWorkflowReply}
        onPendingComposerInputSkip={handlePendingComposerInputSkip}
        pendingHarnessDecision={pendingHarnessDecision}
        pendingHarnessDecisionBusy={pendingHarnessDecisionBusy}
        pendingHarnessDecisionError={pendingHarnessDecisionError}
        onPendingHarnessDecisionAction={handlePendingHarnessDecisionAction}
        hasUnseenArtifact={hasUnseenArtifact}
      />
    </ErrorBoundary>
  );


  // Widget mode has its own UI (persistent widget on non-ChatPage routes), so render that
  if (isInWidgetMode) {
    if (widgetChatMinimized) {
      return (
        <div className="fixed right-4 bottom-4 z-50 widget-safe-bottom">
          <button
            type="button"
            onClick={toggleWidgetChatMinimized}
            className="group relative w-20 h-20 rounded-2xl bg-gradient-to-br from-primary to-secondary shadow-[0_8px_32px_rgba(15,23,42,0.6)] border-2 border-primary/50 hover:shadow-[0_16px_48px_rgba(51,240,250,0.4)] hover:scale-105 transition-all duration-300 flex items-center justify-center"
            title="Expand chat"
          >
            <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-primary/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <img 
              src={brandLogoSrc} 
              alt="mozaiksai" 
              className="w-11 h-11 relative z-10 group-hover:scale-110 transition-transform"
              onError={applyBrandImageFallback}
            />
          </button>
        </div>
      );
    }

    return (
      <div className="fixed right-4 bottom-4 z-50 flex flex-col items-end gap-0 pointer-events-none widget-safe-bottom">
        <button
          type="button"
          onClick={toggleWidgetChatMinimized}
          className="pointer-events-auto relative group mb-[-1px] z-20"
          title="Minimize chat"
        >
          <div className="w-32 h-8 rounded-t-2xl bg-gradient-to-r from-primary/40 to-secondary/40 border-t border-l border-r border-primary/40 backdrop-blur-sm flex items-center justify-center group-hover:from-primary/60 group-hover:to-secondary/60 transition-all">
            <svg className="w-5 h-5 text-primary group-hover:text-white transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </button>
        <div className="pointer-events-auto w-[26rem] max-w-[calc(100vw-2.5rem)] h-[50vh] md:h-[70vh] min-h-[360px]">
          <div className="h-full">
            {chatInterface}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="relative flex flex-col h-screen min-h-screen overflow-hidden"
      style={chatPageShellStyle}
    >
      {showInitSpinner && (
        // Make the overlay visually blocking but non-interactive so background UI can still receive events
        <div className="fixed inset-0 flex items-center justify-center bg-black/20 backdrop-blur-sm z-50 pointer-events-none">
          <div className="pointer-events-auto">
            <LoadingSpinner />
          </div>
        </div>
      )}
      {pendingTransitionId && (
        <TransitionScreen
          transitionId={pendingTransitionId}
          onNavigate={handlePendingTransitionNavigate}
          context={pendingTransitionContext}
        />
      )}
      <img
        src={chatBackgroundSrc}
        alt=""
        className="z-[-10] fixed sm:-w-auto w-full h-full top-0 object-cover"
      />
      <Header 
        user={user}
        chatTheme={chatTheme}
        themeLoading={themeLoading}
        onNotificationClick={handleNotificationClick}
        onAction={handleHeaderAction}
      />
      
      {/* Main content area that fills remaining screen height - no scrolling */}
      <div
        className={`flex-1 flex flex-col min-h-0 overflow-hidden ${mainPaddingClass}`}
        style={mainContentStyle}
      >{/* Padding for header */}
        {isMobileView ? (
          <div className="relative flex-1 flex flex-col">
            <div className={`flex-1 flex flex-col transition-[padding-bottom] duration-300 ${mobileChatTopMarginClass} ${mobileChatPaddingBottomClass}`}>
              {chatInterface}
            </div>

            {isSidePanelOpen && mobileDrawerState === 'expanded' && conversationMode === 'workflow' && (
              <button
                type="button"
                aria-label="Collapse artifact workspace"
                className="absolute inset-x-0 top-0 z-30 h-16 bg-gradient-to-b from-black/40 to-transparent"
                onClick={() => setMobileDrawerState('peek')}
              ></button>
            )}

            {/* Only show mobile artifact drawer in workflow mode (Ask mode has no artifacts) */}
            {conversationMode === 'workflow' && (
              <MobileArtifactDrawer
                state={mobileDrawerState}
                onStateChange={setMobileDrawerState}
                onClose={() => {
                  setMobileDrawerState('peek');
                  setIsSidePanelOpen(false);
                }}
                viewMode={isViewMode}
                chatTheme={chatTheme}
                onExitView={exitViewMode}
                artifactContent={
                  <ErrorBoundary fallback={<ArtifactErrorFallback onRetry={() => setMobileDrawerState('peek')} />}>
                    <ArtifactPanel
                      onClose={() => {
                        setMobileDrawerState('peek');
                        setIsSidePanelOpen(false);
                      }}
                      isMobile
                      isEmbedded
                      viewMode={isViewMode}
                      onExitView={exitViewMode}
                      messages={currentArtifactMessages}
                      chatId={currentChatId}
                      workflowName={currentWorkflowName}
                      chatTheme={chatTheme}
                      onArtifactAction={sendArtifactAction}
                      actionStatusMap={actionStatusMap}
                      floatingWidget={viewWidget}
                    />
                  </ErrorBoundary>
                }
                hasUnseenChat={hasUnseenChat}
                hasUnseenArtifact={hasUnseenArtifact}
              />
            )}

            {showMobileHistoryMenu && (
              <MobileAskHistoryDrawer
                mode={conversationMode}
                open={isAskHistoryDrawerOpen}
                sessions={conversationMode === 'workflow' ? workflowSessions : generalChatSessions}
                activeChatId={conversationMode === 'workflow' ? (activeChatId || currentChatId) : activeGeneralChatId}
                loading={conversationMode === 'workflow' ? workflowSessionsLoading : generalSessionsLoading}
                onSelectChat={handleSelectGeneralChat}
                onSelectWorkflow={handleSelectWorkflowSession}
                onStartNewChat={handleStartGeneralChat}
                onStartEntryWorkflow={() => handleConversationModeChange('workflow')}
                onRefresh={conversationMode === 'workflow' ? handleRefreshWorkflowSessions : handleRefreshGeneralSessions}
                onClear={conversationMode === 'workflow' ? handleClearWorkflowSessions : handleClearGeneralSessions}
                onDeleteSession={conversationMode === 'ask' ? handleDeleteGeneralSession : undefined}
                onClose={() => setIsAskHistoryDrawerOpen(false)}
              />
            )}
          </div>
        ) : (
          <div className="flex flex-1 min-h-0 gap-4 px-3 md:px-6 pt-5 pb-4 items-stretch">
            {showAskHistorySidebar && (
              <AskHistorySidebar
                sessions={generalChatSessions}
                activeChatId={activeGeneralChatId}
                loading={generalSessionsLoading}
                onSelectChat={handleSelectGeneralChat}
                onStartNewChat={handleStartGeneralChat}
                onRefresh={handleRefreshGeneralSessions}
                onClear={handleClearGeneralSessions}
                onDeleteSession={handleDeleteGeneralSession}
              />
            )}
            <div className="flex-1 min-h-0 -my-2 h-[calc(100%+1rem)]">
              <FluidChatLayout
                layoutMode={effectiveLayoutMode}
                onLayoutChange={setLayoutMode}
                isArtifactAvailable={true}
                hasActiveChat={!!currentChatId}
                onToggleArtifact={() => {
                  if (layoutMode === 'full') {
                    setLayoutMode('split');
                    setIsSidePanelOpen(true);
                  } else {
                    setLayoutMode('full');
                    setIsSidePanelOpen(false);
                  }
                }}
                onToggleChat={() => {
                  if (layoutMode === 'minimized') {
                    setLayoutMode('split');
                  }
                }}
                chatContent={chatInterface}
                artifactContent={
                  <ErrorBoundary fallback={<ArtifactErrorFallback onRetry={() => setLayoutMode('full')} />}>
                    <ArtifactPanel
                      onClose={toggleSidePanel}
                      viewMode={isViewMode}
                      onExitView={exitViewMode}
                      messages={currentArtifactMessages}
                      chatId={currentChatId}
                      workflowName={currentWorkflowName}
                      chatTheme={chatTheme}
                      onArtifactAction={sendArtifactAction}
                      actionStatusMap={actionStatusMap}
                      floatingWidget={viewWidget}
                    />
                  </ErrorBoundary>
                }
              />
            </div>
          </div>
        )}
      </div>
      {!isMobileView && <Footer />}

    </div>
  );
};

export default ChatPage;
