import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI } from '../../context/ChatUIContext';
import ChatInterface from './ChatInterface';
import useTheme from '../../styles/useTheme';
import {
  applyBrandImageFallback,
  getBrandLogoSrc,
} from '../../styles/brandAssets';
import { useWidgetAskWS } from '../../hooks/useWidgetAskWS';
import {
  buildSupportConversationTranscript,
  buildSupportRequestPayload,
  buildUserSupportPath,
  resolveSupportRequestScope,
  shouldOfferHumanSupport,
  supportTrace,
  supportWarn,
} from '../../utils/supportLinks';

/**
 * PersistentChatWidget - Floating chat widget in bottom-right corner.
 *
 * Always runs in ask/general mode — the widget is for quick questions and
 * page-contextual help on any route. Workflow sessions live in the full
 * ChatPage; the widget never enters workflow mode.
 *
 * The widget maintains its own WebSocket connection (separate chat_id from any
 * workflow session) so it works independently of ChatPage. The connection is
 * established lazily on first open and stays alive for the page session.
 *
 * Header:
 * - Left 🧠 button: opens full ChatPage in ask mode
 * - Right logo: "Back to workspace" — only shown when a workflow session exists
 * - ? button: opens the operator support form
 *
 * Ask session persistence:
 * - activeGeneralChatId is stored in localStorage via setStoredActiveGeneralChatId
 * - On next page load the widget resumes the same general chat via chat.enter_general_mode
 */
const PersistentChatWidget = ({
  chatId,
  workflowName,
  pageContext = null,
}) => {
  const {
    setConversationMode,
    activeChatId,
    activeWorkflowName,
    setActiveChatId,
    setActiveWorkflowName,
    activeGeneralChatId,
    setActiveGeneralChatId,
    askMessages,
    setAskMessages,
    unreadChatCount,
    setUnreadChatCount,
    api,
    auth,
    user,
    config,
  } = useChatUI();
  const navigate = useNavigate();

  const [isExpanded, setIsExpanded] = useState(false);
  const [wsEnabled, setWsEnabled] = useState(false);
  const [inSupportMode, setInSupportMode] = useState(false);
  const [supportMessage, setSupportMessage] = useState('');
  const [supportSent, setSupportSent] = useState(false);
  const [supportError, setSupportError] = useState(null);
  const [supportLoading, setSupportLoading] = useState(false);

  // Resolve identity from ChatUIProvider context
  const resolvedAppId = user?.app_id || config?.appId || config?.app_id || null;
  const resolvedUserId = user?.id || user?.user_id || user?.sub || null;
  const [widgetIdentity, setWidgetIdentity] = useState({
    appId: resolvedAppId,
    userId: resolvedUserId,
  });
  const effectiveAppId = widgetIdentity.appId || resolvedAppId;
  const effectiveUserId = widgetIdentity.userId || resolvedUserId;
  const { theme: chatTheme } = useTheme(resolvedAppId);
  const brandLogoSrc = getBrandLogoSrc(chatTheme);

  useEffect(() => {
    let cancelled = false;
    if (effectiveAppId && effectiveUserId) return () => {};

    resolveSupportRequestScope({
      api,
      auth,
      config,
      user,
      fallbackAppId: resolvedAppId,
      fallbackUserId: resolvedUserId,
    }).then((scope) => {
      if (cancelled) return;
      setWidgetIdentity({
        appId: scope.appId || resolvedAppId,
        userId: scope.userId || resolvedUserId,
      });
    });

    return () => {
      cancelled = true;
    };
  }, [api, auth, config, effectiveAppId, effectiveUserId, resolvedAppId, resolvedUserId, user]);

  // Enable the WS connection the first time the widget is opened (stays alive after)
  useEffect(() => {
    if (isExpanded && !wsEnabled) setWsEnabled(true);
  }, [isExpanded, wsEnabled]);

  // Widget's own WS connection in ask/general mode.
  const { send: wsSend, status: wsStatus, isAgentTyping, generalModeReady } = useWidgetAskWS({
    api,
    appId: effectiveAppId,
    userId: effectiveUserId,
    workflowName: null,
    activeGeneralChatId,
    setActiveGeneralChatId,
    onAgentMessage: (msg) => setAskMessages(prev => [...prev, msg]),
    enabled: wsEnabled,
  });
  const pendingWidgetSendsRef = useRef([]);

  useEffect(() => {
    if (wsStatus !== 'connected' || !generalModeReady || pendingWidgetSendsRef.current.length === 0) {
      return;
    }
    const pending = [...pendingWidgetSendsRef.current];
    pendingWidgetSendsRef.current = [];
    pending.forEach((text) => {
      const sent = wsSend(text);
      if (!sent) {
        pendingWidgetSendsRef.current.push(text);
      }
    });
  }, [generalModeReady, wsSend, wsStatus]);

  // Workflow session exists → show the "Back to workspace" button
  const hasActiveWorkflow = !!(activeChatId || chatId);

  // Unread badge: count new messages that arrive while the widget is collapsed
  const prevAskLenRef = useRef(null);
  const supportCueInjectedRef = useRef(false);
  useEffect(() => {
    const aLen = Array.isArray(askMessages) ? askMessages.length : 0;
    if (prevAskLenRef.current === null) {
      prevAskLenRef.current = aLen;
      return;
    }
    if (!isExpanded) {
      const delta = aLen - (prevAskLenRef.current ?? aLen);
      if (delta > 0) setUnreadChatCount(prev => prev + delta);
    }
    prevAskLenRef.current = aLen;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askMessages?.length]);

  // Programmatic support navigation (e.g. EscalationCard on other pages)
  useEffect(() => {
    const handler = () => {
      supportTrace('open_support:event', {
        source: 'window_event',
        resolvedAppId,
        activeGeneralChatId,
      });
      setIsExpanded(false);
      navigate(buildUserSupportPath({ appId: resolvedAppId }));
    };
    window.addEventListener('mozaiks:open_support', handler);
    return () => window.removeEventListener('mozaiks:open_support', handler);
  }, [activeGeneralChatId, navigate, resolvedAppId]);

  const handleSendMessage = (message) => {
    const text = message && typeof message === 'object' ? message.content : message;
    if (!text?.trim()) return;
    const wantsHumanSupport = shouldOfferHumanSupport(text);

    // Optimistic — show the user's message immediately
    setAskMessages(prev => [...prev, {
      id: `opt_${Date.now()}`,
      sender: 'user',
      agentName: 'You',
      content: text,
      timestamp: new Date().toISOString(),
    }]);

    if (wantsHumanSupport) {
      const transcriptSnapshot = buildSupportConversationTranscript(
        [
          ...(askMessages || []),
          {
            sender: 'user',
            content: text,
          },
        ],
        { includeMessage: text },
      );
      supportTrace('escalation_card:injected', {
        source: 'widget_ask',
        activeGeneralChatId,
        generalModeReady,
        resolvedAppId,
        alreadyInjected: supportCueInjectedRef.current,
        transcriptCount: transcriptSnapshot.length,
      });
      if (!supportCueInjectedRef.current) {
        supportCueInjectedRef.current = true;
        setAskMessages(prev => [
          ...prev,
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
      }
      return;
    }

    // Send via the widget's own WS; queue if the user submits before connect finishes.
    if (wsStatus === 'connected' && generalModeReady) {
      const sent = wsSend(text);
      if (!sent) {
        pendingWidgetSendsRef.current.push(text);
        setWsEnabled(true);
      }
    } else {
      pendingWidgetSendsRef.current.push(text);
      setWsEnabled(true);
    }
  };

  // Start a fresh ask conversation
  const handleNewConversation = () => {
    const newId = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
      ? `ask_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
      : `ask_${Date.now()}`;
    setAskMessages([]);
    supportCueInjectedRef.current = false;
    pendingWidgetSendsRef.current = [];
    setActiveGeneralChatId(newId);
  };

  // Navigate to full ask ChatPage
  const handleOpenAsk = () => {
    setConversationMode('ask');
    setIsExpanded(false);
    const params = new URLSearchParams({ mode: 'ask', force_ask: '1' });
    if (resolvedAppId) params.set('app_id', resolvedAppId);
    if (activeGeneralChatId) params.set('general_chat_id', activeGeneralChatId);
    if (pageContext) params.set('page_context', pageContext);
    navigate(`/chat?${params.toString()}`);
  };

  // Navigate back to the active workflow session
  const handleBackToWorkspace = () => {
    const safeGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
    const resolvedChatId = chatId || activeChatId || safeGet('mozaiks.current_chat_id');
    const resolvedWorkflowName = workflowName || activeWorkflowName || safeGet('mozaiks.current_workflow_name');

    if (resolvedChatId) {
      setActiveChatId(resolvedChatId);
      try { localStorage.setItem('mozaiks.current_chat_id', resolvedChatId); } catch {}
    }
    if (resolvedWorkflowName) {
      setActiveWorkflowName(resolvedWorkflowName);
      try { localStorage.setItem('mozaiks.current_workflow_name', resolvedWorkflowName); } catch {}
    }

    setConversationMode('workflow');
    setIsExpanded(false);

    const params = new URLSearchParams({ mode: 'workflow' });
    if (resolvedChatId) params.set('chat_id', resolvedChatId);
    if (resolvedWorkflowName) params.set('workflow', resolvedWorkflowName);
    navigate(`/chat?${params.toString()}`);
  };

  // Support form handlers
  const handleOpenSupport = () => {
    supportTrace('support_form:open', {
      source: 'widget_header',
      appId: resolvedAppId,
      activeGeneralChatId,
      pageContext,
    });
    setSupportSent(false);
    setSupportError(null);
    setSupportMessage('');
    setInSupportMode(true);
  };

  const openSupportThread = useCallback((created) => {
    const requestId =
      created?.request_id ||
      created?.request?.request_id ||
      created?.result?.request_id ||
      created?.data?.request_id ||
      null;
    const messageThreadId =
      created?.message_thread_id ||
      created?.request?.message_thread_id ||
      created?.result?.message_thread_id ||
      created?.data?.message_thread_id ||
      null;
    const createdAppId =
      created?.app_id ||
      created?.subject_app_id ||
      created?.request?.app_id ||
      created?.request?.subject_app_id ||
      created?.result?.app_id ||
      created?.result?.subject_app_id ||
      created?.data?.app_id ||
      created?.data?.subject_app_id ||
      resolvedAppId;
    const path = buildUserSupportPath({ requestId, appId: createdAppId });
    supportTrace('support_thread:open', {
      requestId,
      appId: createdAppId,
      messageThreadId,
      path,
    });
    if (!requestId) {
      supportWarn('support_thread:missing_request_id', {
        appId: resolvedAppId,
        responseKeys: created && typeof created === 'object' ? Object.keys(created) : [],
      });
    }
    setIsExpanded(false);
    navigate(path);
  }, [navigate, resolvedAppId]);

  const createSupportRequest = useCallback(async ({
    message,
    conversationTranscript = null,
    pageTitle = null,
    pageUrl = null,
    severity = 'low',
  }) => {
    const supportScope = await resolveSupportRequestScope({
      api,
      auth,
      config,
      user,
      fallbackAppId: resolvedAppId,
      fallbackUserId: resolvedUserId,
    });
    const payload = buildSupportRequestPayload({
      message,
      appId: supportScope.appId || resolvedAppId,
      userId: supportScope.userId || resolvedUserId,
      pageUrl: pageUrl || (typeof window !== 'undefined' ? window.location.href : null),
      pageTitle: pageTitle || pageContext || null,
      severity,
      conversationTranscript,
    });
    supportTrace('support_request:create:start', {
      appId: payload.app_id || null,
      userId: payload.user_id || null,
      severity,
      pageUrl: payload.page_url || null,
      pageTitle: payload.page_title || null,
      transcriptCount: payload.conversation_transcript?.length || 0,
      message,
    });
    const response = await fetch('/api/modules/workspace_support/create_support_request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      supportWarn('support_request:create:failed_http', {
        status: response.status,
        appId: resolvedAppId,
      });
      throw new Error(`Support request failed with ${response.status}`);
    }
    const created = await response.json();
    supportTrace('support_request:create:success', {
      requestId: created?.request_id || created?.request?.request_id || created?.result?.request_id || created?.data?.request_id || null,
      appId: created?.app_id || created?.subject_app_id || created?.result?.app_id || created?.data?.app_id || resolvedAppId || null,
      userId: supportScope.userId || resolvedUserId || null,
      messageThreadId: created?.message_thread_id || created?.request?.message_thread_id || created?.result?.message_thread_id || created?.data?.message_thread_id || null,
      status: created?.status || created?.request?.status || created?.result?.status || created?.data?.status || null,
      responseKeys: created && typeof created === 'object' ? Object.keys(created) : [],
    });
    return created;
  }, [api, auth, config, pageContext, resolvedAppId, resolvedUserId, user]);

  const handleSubmitSupport = useCallback(async () => {
    const text = supportMessage.trim();
    if (!text || supportLoading) return;
    setSupportLoading(true);
    setSupportError(null);
    try {
      const created = await createSupportRequest({ message: text });
      openSupportThread(created);
    } catch (error) {
      supportWarn('support_request:create:failed_widget_form', {
        appId: resolvedAppId,
        error: error?.message || String(error || ''),
      });
      setSupportError('Could not send the request. Open your profile support tab and try again.');
    }
    setSupportLoading(false);
  }, [createSupportRequest, openSupportThread, supportMessage, supportLoading]);

  // ─── Minimized state ────────────────────────────────────────────────────────
  if (!isExpanded) {
    return (
      <div className="fixed right-0 bottom-6 z-50 widget-safe-bottom">
        <button
          type="button"
          onClick={() => { setIsExpanded(true); setUnreadChatCount(0); }}
          className="group relative flex flex-col items-center gap-1.5 rounded-l-2xl border border-r-0 border-border/50 bg-card/90 px-2 py-4 shadow-md backdrop-blur-sm transition-all duration-200 hover:border-primary/40 hover:bg-card hover:px-3"
          title="Open assistant"
        >
          <img
            src={brandLogoSrc}
            alt="Chat"
            className="h-4 w-4 opacity-60 transition-opacity group-hover:opacity-100"
            onError={applyBrandImageFallback}
          />
          <svg
            className="h-3.5 w-3.5 text-muted-foreground transition-colors group-hover:text-foreground"
            fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2.5"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
          {unreadChatCount > 0 && (
            <span className="absolute -top-1 -left-1 h-2.5 w-2.5 rounded-full bg-primary" />
          )}
        </button>
      </div>
    );
  }

  // ─── Expanded state ──────────────────────────────────────────────────────────
  const connectionDot = wsStatus === 'connected'
    ? 'bg-success'
    : wsStatus === 'connecting'
    ? 'bg-warning animate-pulse'
    : 'bg-muted-foreground/40';

  return (
    <div className="fixed right-4 bottom-4 z-50 flex flex-col items-end gap-0 pointer-events-none widget-safe-bottom">
      {/* Collapse tab */}
      <button
        type="button"
        onClick={() => setIsExpanded(false)}
        className="pointer-events-auto relative group mb-[-1px] z-20"
        title="Minimize"
      >
        <div className="w-32 h-8 rounded-t-2xl bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.4)] to-[rgba(var(--color-secondary-rgb),0.4)] border-t border-l border-r border-[rgba(var(--color-primary-light-rgb),0.4)] backdrop-blur-sm flex items-center justify-center group-hover:from-[rgba(var(--color-primary-rgb),0.6)] group-hover:to-[rgba(var(--color-secondary-rgb),0.6)] transition-all">
          <svg className="w-5 h-5 text-[var(--color-primary-light)] group-hover:text-white transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {/* Chat panel */}
      <div className="pointer-events-auto w-[26rem] max-w-[calc(100vw-2.5rem)] h-[50vh] md:h-[70vh] min-h-[360px] bg-gradient-to-br from-gray-900/95 via-slate-900/95 to-black/95 backdrop-blur-xl border border-[rgba(var(--color-primary-light-rgb),0.3)] rounded-2xl rounded-tr-none shadow-2xl overflow-hidden flex flex-col">

        {/* Header */}
        <div className="flex-shrink-0 bg-[rgba(0,0,0,0.6)] border-b border-[rgba(var(--color-primary-light-rgb),0.2)] backdrop-blur-xl">
          <div className="flex flex-row items-center justify-between px-3 py-2.5 sm:px-4 sm:py-3 gap-2">

            {/* Left: Ask mode label / support label */}
            <button
              type="button"
              onClick={inSupportMode ? () => { setInSupportMode(false); setSupportSent(false); setSupportError(null); setSupportMessage(''); } : handleOpenAsk}
              className="flex items-center gap-2 sm:gap-3 focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-light)]/60 rounded-xl min-w-0 flex-1"
              title={inSupportMode ? 'Back to chat' : 'Open full Ask chat'}
            >
              <span className={`w-9 h-9 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl flex items-center justify-center shadow-lg flex-shrink-0 ${inSupportMode ? 'bg-gradient-to-br from-warning/60 to-warning/30' : 'bg-gradient-to-br from-[var(--color-secondary)] to-[var(--color-primary)]'}`}>
                <span className="text-xl sm:text-2xl" role="img" aria-hidden="true">
                  {inSupportMode ? '?' : '🧠'}
                </span>
              </span>
              <span className="text-left min-w-0 flex-1">
                <span className="block text-sm sm:text-lg font-bold text-white tracking-tight truncate">
                  {inSupportMode ? 'Help & Support' : 'mozaiksai'}
                </span>
                <span className="block text-[10px] sm:text-xs text-gray-400 truncate flex items-center gap-1.5">
                  {inSupportMode ? 'Talk to an operator' : (
                    <>
                      <span className={`inline-block w-1.5 h-1.5 rounded-full ${connectionDot}`} />
                  {wsStatus === 'connected' && generalModeReady ? 'Ask anything' : wsStatus === 'connecting' || (wsStatus === 'connected' && !generalModeReady) ? 'Connecting…' : 'Ask mode'}
                    </>
                  )}
                </span>
              </span>
            </button>

            {/* Right actions */}
            <div className="flex items-center gap-1.5 flex-shrink-0">
              {/* Help (?) */}
              <button
                type="button"
                onClick={handleOpenSupport}
                title="Get help from an operator"
                className={`flex h-9 w-9 items-center justify-center rounded-lg border text-sm font-bold transition-all duration-200 ${
                  inSupportMode
                    ? 'border-warning/60 bg-warning/20 text-warning'
                    : 'border-[rgba(var(--color-primary-light-rgb),0.25)] bg-[rgba(var(--color-primary-rgb),0.08)] text-gray-300 hover:border-[rgba(var(--color-primary-light-rgb),0.5)] hover:text-white'
                }`}
              >
                ?
              </button>

              {/* Back to workspace — only when a workflow session is active */}
              {hasActiveWorkflow && !inSupportMode && (
                <button
                  onClick={handleBackToWorkspace}
                  className="group relative p-2 rounded-lg bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.1)] to-[rgba(var(--color-secondary-rgb),0.1)] border border-[rgba(var(--color-primary-light-rgb),0.3)] hover:border-[rgba(var(--color-primary-light-rgb),0.6)] transition-all duration-300 backdrop-blur-sm"
                  title="Back to workspace"
                >
                  <img
                    src={brandLogoSrc}
                    className="w-8 h-8 opacity-70 group-hover:opacity-100 transition-all duration-300 group-hover:scale-105"
                    alt="Back to workspace"
                    onError={applyBrandImageFallback}
                  />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Sub-header: compose link */}
        {!inSupportMode && (
          <div className="flex-shrink-0 px-3 pt-2 pb-1.5 border-b border-[rgba(var(--color-primary-light-rgb),0.06)]">
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={handleNewConversation}
                className="text-[11px] text-[var(--color-primary-light)] hover:text-white transition-colors opacity-60 hover:opacity-100 flex items-center gap-1"
              >
                <span>+</span>
                <span>New conversation</span>
              </button>
              {pageContext && (
                <span className="text-[10px] text-gray-500 truncate max-w-[8rem]" title={pageContext}>
                  📍 {pageContext}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Body */}
        {inSupportMode ? (
          <div className="flex flex-1 flex-col overflow-hidden">
            {supportSent ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 py-8 text-center">
                <span className="flex h-14 w-14 items-center justify-center rounded-full bg-success/20 text-3xl">✓</span>
                <p className="text-sm leading-relaxed text-gray-300">
                  Got it — your request has been received. An operator will review it and follow up shortly.
                </p>
                <button
                  type="button"
                  onClick={() => { setInSupportMode(false); setSupportSent(false); setSupportError(null); setSupportMessage(''); }}
                  className="mt-2 rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.3)] px-4 py-2 text-sm text-gray-300 transition-colors hover:border-[rgba(var(--color-primary-light-rgb),0.6)] hover:text-white"
                >
                  Back to chat
                </button>
              </div>
            ) : (
              <div className="flex flex-1 flex-col gap-3 overflow-hidden px-4 py-4">
                <p className="text-[11px] text-gray-400">
                  Describe what you need help with and an operator will follow up shortly.
                </p>
                <textarea
                  value={supportMessage}
                  onChange={(e) => setSupportMessage(e.target.value)}
                  placeholder="What do you need help with?"
                  rows={5}
                  className="flex-1 resize-none rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-gray-500 focus:border-[rgba(var(--color-primary-light-rgb),0.4)] focus:outline-none"
                />
                {supportError && (
                  <p className="text-xs leading-relaxed text-red-300">
                    {supportError}
                  </p>
                )}
                <button
                  type="button"
                  onClick={handleSubmitSupport}
                  disabled={!supportMessage.trim() || supportLoading}
                  className="rounded-xl bg-gradient-to-r from-[var(--color-primary)] to-[var(--color-secondary)] px-4 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40 hover:opacity-90"
                >
                  {supportLoading ? 'Sending…' : 'Send request'}
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 overflow-hidden">
            <ChatInterface
              messages={isAgentTyping ? [...askMessages, { id: 'widget-thinking', isThinking: true, agentName: 'Assistant' }] : askMessages}
              onSendMessage={handleSendMessage}
              loading={wsStatus === 'connecting'}
              connectionStatus={wsStatus === 'connected' ? 'connected' : 'disconnected'}
              conversationMode="ask"
              onConversationModeChange={(mode) => setConversationMode(mode)}
              isOnChatPage={false}
              hideHeader={true}
              disableMobileShellChrome={true}
              plainContainer={true}
              chatTheme={chatTheme}
              onAgentAction={async (action) => {
                if (
                  action?.type === 'tool_call_response' &&
                  action?.tool_name === 'EscalationCard' &&
                  action?.response?.action === 'open_support'
                ) {
                  supportTrace('escalation_card:open_support:start', {
                    source: 'widget_card',
                    appId: resolvedAppId,
                    activeGeneralChatId,
                  });
                  try {
                    const latestUserMessage = [...(askMessages || [])]
                      .reverse()
                      .find((message) => message?.sender === 'user' && String(message?.content || '').trim());
                    const supportMessage = String(latestUserMessage?.content || 'Requested operator assistance from chat widget.').trim();
                    const conversationTranscript = Array.isArray(action?.payload?.conversationTranscript)
                      ? action.payload.conversationTranscript
                      : buildSupportConversationTranscript(askMessages, {
                        includeMessage: supportMessage,
                      });
                    supportTrace('escalation_card:open_support:transcript', {
                      source: action?.payload?.conversationTranscript ? 'card_payload' : 'state_fallback',
                      transcriptCount: conversationTranscript.length,
                    });
                    const created = await createSupportRequest({
                      message: supportMessage,
                      conversationTranscript,
                      pageUrl: window.location.href,
                    });
                    openSupportThread(created);
                    return;
                  } catch (error) {
                    supportWarn('escalation_card:open_support:create_failed', {
                      appId: resolvedAppId,
                      error: error?.message || String(error || ''),
                    });
                  }
                  setIsExpanded(false);
                  const fallbackPath = buildUserSupportPath({ appId: resolvedAppId });
                  supportTrace('escalation_card:open_support:fallback_navigate', {
                    appId: resolvedAppId,
                    path: fallbackPath,
                  });
                  navigate(fallbackPath);
                }
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default PersistentChatWidget;
