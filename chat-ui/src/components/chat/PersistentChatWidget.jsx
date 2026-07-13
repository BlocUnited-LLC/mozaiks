import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI } from '../../context/ChatUIContext';
import ChatInterface from './ChatInterface';
import useTheme from '../../styles/useTheme';
import {
  applyBrandImageFallback,
  getBrandLogoSrc,
} from '../../styles/brandAssets';

/**
 * PersistentChatWidget - Floating chat widget in bottom-right corner
 *
 * Visible on: non-chat routes (via GlobalChatWidgetWrapper) + ChatPage layoutMode='view'
 * (via ArtifactPanel floatingWidget prop). Suppressed on /chat and /app/:id/:workflow routes.
 *
 * Context-adaptive chat display:
 * - If a workflow is active → shows workflowMessages by default (user stays in their context)
 * - User can click 🧠 to switch to ask context inline (no navigation)
 * - If no workflow → shows askMessages directly
 *
 * Header buttons (max 2 — keeps header clean on mobile + desktop):
 * - Left 🧠: when workflow showing → switch to ask context inline
 *            when ask context → navigate to full Ask ChatPage
 * - Right logo: "Back to workspace" → navigate to split/minimized workflow view
 *               hidden when no active workflow
 *
 * Compose affordance: "+ New conversation" text link inside body (ask context only)
 * Unread badge: dot on minimized button when unreadChatCount > 0
 *
 * Persistence wiring:
 * - Workflow send: api.sendMessageToWorkflow() routes through the live WS connection
 *   registered by ChatPage. If ChatPage's WS was closed, the send gracefully no-ops
 *   and logs a warning — the user should navigate back to the chat page to reconnect.
 * - New conversation (ask context): generates a local UUID as a new ask session ID
 *   (setActiveGeneralChatId) and clears the message cache. No server round-trip needed
 *   since there is no POST /api/general_chats/start endpoint — general chat sessions
 *   are created lazily on first WebSocket connection in ChatPage.
 */
const PersistentChatWidget = ({
  chatId,
  workflowName,
  conversationMode: conversationModeProp,
  pageContext = null,
}) => {
  const {
    setConversationMode,
    activeChatId,
    activeWorkflowName,
    activeGeneralChatId,
    setActiveGeneralChatId,
    setActiveChatId,
    setActiveWorkflowName,
    askMessages,
    setAskMessages,
    workflowMessages,
    setWorkflowMessages,
    workflowStatus,
    unreadChatCount,
    setUnreadChatCount,
    api,
    user,
    config,
  } = useChatUI();
  const navigate = useNavigate();

  const [isExpanded, setIsExpanded] = useState(false);
  const [showRatingBanner, setShowRatingBanner] = useState(false);
  const [supportMessage, setSupportMessage] = useState('');
  const [supportSent, setSupportSent] = useState(false);
  const [supportLoading, setSupportLoading] = useState(false);
  const prevWorkflowStatusRef = useRef(null);

  // Detect when a workflow session ends and prompt for a rating
  useEffect(() => {
    const prev = prevWorkflowStatusRef.current;
    const current = workflowStatus || 'idle';
    const wasActive = prev !== null && prev !== 'idle';
    const isNowIdle = current === 'idle';

    if (wasActive && isNowIdle && Array.isArray(workflowMessages) && workflowMessages.length > 1) {
      const sessionId = activeChatId || chatId || null;
      const ratedKey = `mozaiks.session_rated:${sessionId || 'unknown'}`;
      try {
        if (!sessionStorage.getItem(ratedKey)) {
          setShowRatingBanner(true);
        }
      } catch {}
    }

    prevWorkflowStatusRef.current = current;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowStatus]);

  const handleRating = useCallback((rating) => {
    const sessionId = activeChatId || chatId || null;
    fetch('/api/modules/workspace_support/submit_session_feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        workflow_name: activeWorkflowName || workflowName || null,
        rating,
      }),
    }).catch(() => {});
    try {
      sessionStorage.setItem(`mozaiks.session_rated:${sessionId || 'unknown'}`, String(rating));
    } catch {}
    setShowRatingBanner(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChatId, chatId, activeWorkflowName, workflowName]);

  // Track message array lengths to increment unread badge when new messages arrive
  // while the widget is collapsed. Only counts messages added after mount.
  const prevWorkflowLenRef = useRef(null);
  const prevAskLenRef = useRef(null);
  useEffect(() => {
    const wLen = Array.isArray(workflowMessages) ? workflowMessages.length : 0;
    const aLen = Array.isArray(askMessages) ? askMessages.length : 0;
    if (prevWorkflowLenRef.current === null) {
      // Initial mount — set baseline; don't count existing messages as "new"
      prevWorkflowLenRef.current = wLen;
      prevAskLenRef.current = aLen;
      return;
    }
    if (!isExpanded) {
      const wNew = wLen - (prevWorkflowLenRef.current ?? wLen);
      const aNew = aLen - (prevAskLenRef.current ?? aLen);
      const totalNew = (wNew > 0 ? wNew : 0) + (aNew > 0 ? aNew : 0);
      if (totalNew > 0) {
        setUnreadChatCount(prev => prev + totalNew);
      }
    }
    prevWorkflowLenRef.current = wLen;
    prevAskLenRef.current = aLen;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowMessages?.length, askMessages?.length]);

  // Resolve app/user identity from context (provided by the host app via ChatUIProvider props)
  const resolvedAppId = user?.app_id || config?.appId || config?.app_id || null;
  const resolvedUserId = user?.id || user?.user_id || user?.sub || null;
  const { theme: chatTheme } = useTheme(resolvedAppId);
  const brandLogoSrc = getBrandLogoSrc(chatTheme);

  // A workflow is "active" if status is not idle OR there are cached workflow messages
  const hasActiveWorkflow =
    (workflowStatus && workflowStatus !== 'idle') ||
    (Array.isArray(workflowMessages) && workflowMessages.length > 0) ||
    !!(activeChatId || chatId);

  // widgetContext: 'auto' | 'ask' | 'support'
  const [widgetContext, setWidgetContext] = useState('auto');
  const showingWorkflowContext = hasActiveWorkflow && widgetContext !== 'ask';

  const messages = showingWorkflowContext ? workflowMessages : askMessages;
  const hasBackend = !!api;

  const handleOpenSupport = () => {
    setSupportSent(false);
    setSupportMessage('');
    setWidgetContext('support');
  };

  const handleSubmitSupport = async () => {
    const text = supportMessage.trim();
    if (!text || supportLoading) return;
    setSupportLoading(true);
    try {
      await fetch('/api/modules/workspace_support/create_support_request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          page_url: typeof window !== 'undefined' ? window.location.pathname : null,
          page_title: pageContext || null,
          severity: 'low',
        }),
      });
    } catch (_) {}
    setSupportLoading(false);
    setSupportSent(true);
  };

  const handleSendMessage = (message) => {
    if (showingWorkflowContext) {
      const resolvedChatId = activeChatId || chatId;
      const resolvedWorkflow = activeWorkflowName || workflowName;

      // Optimistically append user message so the widget feels responsive
      setWorkflowMessages(prev => [...prev, {
        id: Date.now(),
        role: 'user',
        content: message,
        timestamp: new Date().toISOString(),
      }]);

      // Route through api.sendMessageToWorkflow — this checks api._chatConnections
      // for an existing live WebSocket registered by ChatPage. If the connection
      // was closed when the user navigated away, the send no-ops and logs a warning.
      if (api?.sendMessageToWorkflow && resolvedAppId && resolvedUserId && resolvedChatId) {
        api.sendMessageToWorkflow(message, resolvedAppId, resolvedUserId, resolvedWorkflow, resolvedChatId)
          .then(result => {
            if (!result || result.success === false) {
              console.warn('[WIDGET] sendMessageToWorkflow: no live connection — navigate to chat to reconnect');
            }
          })
          .catch(err => console.error('[WIDGET] sendMessageToWorkflow error:', err));
      } else {
        console.warn('[WIDGET] Cannot send workflow message: missing api, appId, userId, or chatId', {
          hasApi: !!api, resolvedAppId, resolvedUserId, resolvedChatId,
        });
      }
      return;
    }

    // Ask context — append locally (general chat WebSocket lives in ChatPage,
    // not in the widget; messages persist in context until user opens ChatPage)
    setAskMessages(prev => [...prev, {
      id: Date.now(),
      role: 'user',
      content: message,
      timestamp: new Date().toISOString(),
    }]);
    setTimeout(() => {
      setAskMessages(prev => [...prev, {
        id: Date.now() + 1,
        role: 'assistant',
        content: 'Open the full chat to connect to a live session.',
        timestamp: new Date().toISOString(),
      }]);
    }, 500);
  };

  // Right button: "Back to workspace" — navigate to workflow split view
  const handleBackToWorkspace = () => {
    const safeGet = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
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

    const params = new URLSearchParams();
    params.set('mode', 'workflow');
    if (resolvedChatId) params.set('chat_id', resolvedChatId);
    if (resolvedWorkflowName) params.set('workflow', resolvedWorkflowName);
    if (!resolvedChatId) params.set('resume', hasBackend ? 'oldest' : 'local');
    const suffix = params.toString() ? `?${params.toString()}` : '';
    navigate(`/chat${suffix}`);
  };

  // Compose: start a fresh ask conversation.
  // Generates a local UUID as the new ask session ID and clears the message cache.
  // General chat sessions are created lazily by ChatPage's WebSocket on first connect —
  // no POST /api/general_chats/start endpoint exists. setActiveGeneralChatId signals
  // to ChatPage that a fresh session should be opened on next navigation.
  const handleNewConversation = () => {
    const newGeneralId = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
      ? `ask_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
      : `ask_${Date.now()}`;
    setWidgetContext('ask');
    setAskMessages([]);
    setActiveGeneralChatId(newGeneralId);
  };

  // ─── Minimized state — slim right-edge tab ──────────────────────────────────
  if (!isExpanded) {
    return (
      <div className="fixed right-0 bottom-6 z-50 widget-safe-bottom">
        <button
          type="button"
          onClick={() => { setIsExpanded(true); setUnreadChatCount(0); }}
          className="group relative flex flex-col items-center gap-1.5 rounded-l-2xl border border-r-0 border-border/50 bg-card/90 px-2 py-4 shadow-md backdrop-blur-sm transition-all duration-200 hover:border-primary/40 hover:bg-card hover:px-3"
          title={workflowName ? `Continue: ${workflowName}` : 'Open chat'}
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

  // ─── Expanded state ─────────────────────────────────────────────────────────
  const inSupportMode = widgetContext === 'support';
  const leftLabel = inSupportMode ? 'Help & Support' : showingWorkflowContext ? 'Ask Mode' : 'mozaiksai';
  const leftSubLabel = inSupportMode ? 'Talk to an operator' : showingWorkflowContext ? 'Switch to ask' : 'Chat Station';
  const leftTitle = inSupportMode ? 'Back to chat' : showingWorkflowContext ? 'Switch to ask mode' : 'Open Chat Station';

  const handleLeftButton = () => {
    if (inSupportMode) {
      setWidgetContext('auto');
      setSupportSent(false);
      setSupportMessage('');
    } else if (showingWorkflowContext) {
      setWidgetContext('ask');
    } else {
      setConversationMode('ask');
      setIsExpanded(false);
      const params = new URLSearchParams({ mode: 'ask' });
      if (pageContext) params.set('page_context', pageContext);
      navigate(`/chat?${params.toString()}`);
    }
  };

  return (
    <div className="fixed right-4 bottom-4 z-50 flex flex-col items-end gap-0 pointer-events-none widget-safe-bottom">
      {/* Collapse tab */}
      <button
        type="button"
        onClick={() => setIsExpanded(false)}
        className="pointer-events-auto relative group mb-[-1px] z-20"
        title="Minimize chat"
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

            {/* Left: context-adaptive label button */}
            <button
              type="button"
              onClick={handleLeftButton}
              className="flex items-center gap-2 sm:gap-3 focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-light)]/60 rounded-xl min-w-0 flex-1"
              title={leftTitle}
            >
              <span className={`w-9 h-9 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl flex items-center justify-center shadow-lg flex-shrink-0 ${inSupportMode ? 'bg-gradient-to-br from-warning/60 to-warning/30' : 'bg-gradient-to-br from-[var(--color-secondary)] to-[var(--color-primary)]'}`}>
                <span className="text-xl sm:text-2xl" role="img" aria-hidden="true">
                  {inSupportMode ? '?' : '🧠'}
                </span>
              </span>
              <span className="text-left min-w-0 flex-1">
                <span className="block text-sm sm:text-lg font-bold text-white tracking-tight truncate">{leftLabel}</span>
                <span className="block text-[10px] sm:text-xs text-gray-400 truncate">{leftSubLabel}</span>
              </span>
            </button>

            {/* Right actions */}
            <div className="flex items-center gap-1.5 flex-shrink-0">
              {/* Help (?) button — always visible, highlighted when active */}
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

              {/* Back to workspace — only when workflow is active and not in support mode */}
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

        {/* Sub-header: context label / compose / workflow name */}
        {!inSupportMode && (
          <div className="flex-shrink-0 px-3 pt-2 pb-1.5 border-b border-[rgba(var(--color-primary-light-rgb),0.06)]">
            {showingWorkflowContext ? (
              <div>
                <span className="text-[10px] text-gray-500 uppercase tracking-wider">
                  {activeWorkflowName || workflowName || 'Active workflow'}
                </span>
                {showRatingBanner && (
                  <div className="mt-1.5 flex items-center gap-2">
                    <span className="text-[10px] text-gray-400">How was your experience?</span>
                    <button type="button" onClick={() => handleRating(0)} className="text-base leading-none hover:scale-110 transition-transform" title="Poor">👎</button>
                    <button type="button" onClick={() => handleRating(1)} className="text-base leading-none hover:scale-110 transition-transform" title="Great">👍</button>
                    <button type="button" onClick={() => setShowRatingBanner(false)} className="text-[10px] text-gray-600 hover:text-gray-400 transition-colors ml-1" title="Dismiss">✕</button>
                  </div>
                )}
              </div>
            ) : (
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
                    📍 Page active
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        {/* Body: support panel OR chat */}
        {inSupportMode ? (
          <div className="flex flex-1 flex-col overflow-hidden">
            {supportSent ? (
              /* Confirmation */
              <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 py-8 text-center">
                <span className="flex h-14 w-14 items-center justify-center rounded-full bg-success/20 text-3xl">✓</span>
                <p className="text-sm leading-relaxed text-gray-300">
                  Got it — your request has been received. An operator will review it and follow up with you shortly.
                </p>
                <button
                  type="button"
                  onClick={() => { setWidgetContext('auto'); setSupportSent(false); setSupportMessage(''); }}
                  className="mt-2 rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.3)] px-4 py-2 text-sm text-gray-300 transition-colors hover:border-[rgba(var(--color-primary-light-rgb),0.6)] hover:text-white"
                >
                  Back to chat
                </button>
              </div>
            ) : (
              /* Support form */
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
          /* Normal chat */
          <div className="flex-1 overflow-hidden">
            <ChatInterface
              messages={messages}
              onSendMessage={handleSendMessage}
              loading={false}
              connectionStatus="disconnected"
              conversationMode={showingWorkflowContext ? 'workflow' : 'ask'}
              onConversationModeChange={(mode) => setConversationMode(mode)}
              isOnChatPage={false}
              hideHeader={true}
              disableMobileShellChrome={true}
              plainContainer={true}
              chatTheme={chatTheme}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default PersistentChatWidget;
