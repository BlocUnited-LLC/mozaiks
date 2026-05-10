import React, { useEffect, useState, useRef } from "react";
import ChatMessage from "./ChatMessage";
import { useNavigate, useParams } from "react-router-dom";
import UIToolRenderer from "../../core/ui/UIToolRenderer";
import {
  applyBrandImageFallback,
  getBrandLogoSrc,
} from "../../styles/brandAssets";

const PENDING_HARNESS_DECISION_TITLES = {
  workflow_reentry: 'Workflow Re-Entry',
  core_restart: 'Core Change Detected',
  auto_patch: 'Scoped Patch',
  clarify_scope: 'Clarify Scope',
  fallback_workflow: 'Workflow Fallback',
};

// Tool-call renderer for workflow-agnostic UI surfaces
// NOTE: Hooks must run unconditionally; define state first, then early-return.
const ToolCallRenderer = React.memo(({ toolCall, onResponse, isCompleted, onArtifactAction, actionStatusMap }) => {
  const [completed, setCompleted] = React.useState(isCompleted || false);
  const [hasInteracted, setHasInteracted] = React.useState(false);
  const rootRef = React.useRef(null);

  // Sync with external completion status (from backend completion event)
  React.useEffect(() => {
    if (isCompleted && !completed) {
      setCompleted(true);
    }
  }, [isCompleted, completed]);

  // Ensure the inline UI tool is fully visible when it mounts and when it changes state
  // NOTE: place hooks before any early return to satisfy react-hooks rules
  React.useEffect(() => {
    try {
      const el = rootRef.current;
      if (!el) return;

      // Use a small timeout to allow the component to finish layout before scrolling
      const EXTRA_MARGIN = 64; // px - make sure bottom of component has breathing room above the input
      const t = setTimeout(() => {
        // Find the nearest scrollable ancestor (chat container)
        let ancestor = el.parentElement;
        while (ancestor && ancestor !== document.body) {
          try {
            const cs = window.getComputedStyle(ancestor);
            const overflowY = cs.overflowY;
            if ((overflowY === 'auto' || overflowY === 'scroll') && ancestor.scrollHeight > ancestor.clientHeight) {
              break;
            }
          } catch (err) {
            // ignore compute errors and continue climbing
          }
          ancestor = ancestor.parentElement;
        }

        if (ancestor && ancestor !== document.body) {
          const elRect = el.getBoundingClientRect();
          const ancRect = ancestor.getBoundingClientRect();
          // How far the bottom of element extends past the bottom of the container (positive means overflow)
          const delta = elRect.bottom - ancRect.bottom;
          // If element's bottom is near or beyond the container bottom, scroll the container by delta + margin
          if (delta > -EXTRA_MARGIN) {
            const toScroll = Math.max(0, delta + EXTRA_MARGIN);
            try { ancestor.scrollBy({ top: toScroll, behavior: 'smooth' }); } catch (e) { ancestor.scrollTop += toScroll; }
          } else {
            // Fallback: ensure element is visible at end
            try { el.scrollIntoView({ behavior: 'smooth', block: 'end', inline: 'nearest' }); } catch (e) { /* ignore */ }
          }
        } else {
          // No ancestor found; fall back to window scrolling
          try { el.scrollIntoView({ behavior: 'smooth', block: 'end', inline: 'nearest' }); } catch (e) { /* ignore */ }
        }
      }, 80);
      return () => clearTimeout(t);
    } catch (e) {
      // swallow errors - non-critical UI behavior
    }
  }, [completed, hasInteracted, toolCall?.tool_name]);

  if (!toolCall || !toolCall.tool_name) {
    return null;
  }

  const displayMode =
    toolCall.display ||
    toolCall.payload?.display ||
    toolCall.payload?.mode ||
    'inline';
  if (displayMode === 'composer') {
    return null;
  }

  const handleResponse = async (resp) => {
    try {
      if (onResponse) {
        await onResponse(resp);
      }
    } finally {
      if (displayMode === 'inline') {
        setCompleted(true);
      }
    }
  };

  const handleUserInteraction = () => {
    if (!hasInteracted) {
      setHasInteracted(true);
    }
  };

  return (
    <div ref={rootRef} className="flex justify-center px-0 message-container">
      <div className="w-full max-w-2xl">
        <div className="flex flex-col gap-3 w-full items-center">
          {completed && displayMode === 'inline' && (
            <span
              aria-label="Completed"
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-semibold rounded-full bg-[rgba(var(--color-success-rgb),0.15)] text-[var(--color-success)] border border-[rgba(var(--color-success-rgb),0.3)] select-none"
            >
              ✓ {toolCall?.tool_name || 'UI Tool'} completed
            </span>
          )}
          {/* Hide the component when completed (auto-vanish effect) */}
          {!completed && (
            <div
              className={`inline-block ${displayMode === 'inline' && !completed && !hasInteracted ? 'inline-component-attention' : ''} ${hasInteracted ? 'interacted' : ''}`}
              onClick={handleUserInteraction}
              onFocus={handleUserInteraction}
              onMouseDown={handleUserInteraction}
              onKeyDown={handleUserInteraction}
            >
              <UIToolRenderer
                event={toolCall}
                onResponse={handleResponse}
                onArtifactAction={onArtifactAction}
                actionStatusMap={actionStatusMap}
                className="ui-tool-in-chat"
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

const ModernChatInterface = ({
  messages,
  onSendMessage,
  onUploadFile,
  loading,
  onAgentAction,
  onArtifactToggle,
  artifactToggleLabel,
  connectionStatus,
  transportType,
  workflowName,
  workflowHasChildren = false,
  conversationMode = 'workflow',
  onConversationModeChange,
  modeTogglePending = false,
  onStartGeneralChat,
  generalChatSummary,
  generalSessionsLoading = false,
  showAskHistoryMenu = false,
  onAskHistoryToggle,
  showHistoryMenu = false,
  onHistoryToggle = null,
  historyMenuLabel = 'Recents',
  structuredOutputs = {},
  startupMode,
  initialMessageToUser,
  onRetry,
  onBrandClick, // Optional callback when brand/logo clicked
  isOnChatPage = true, // Whether we're on the primary chat page (not discovery/workflows)
  hideHeader = false, // Hide the header (used when widget has its own header)
  plainContainer = false, // Skip decorative cosmic-ui-module styling (used in widget)
  chatTheme = null,
  appDisplayName = null,
  artifactContext = null,
  overlayMode = false,
  onOverlayClose = null,
  onArtifactAction = null,
  actionStatusMap = null,
  pendingComposerInputToolCall = null,
  pendingComposerReply = null,
  onPendingComposerInputSkip = null,
  pendingHarnessDecision = null,
  pendingHarnessDecisionBusy = false,
  pendingHarnessDecisionError = null,
  onPendingHarnessDecisionAction = null,
}) => {
  const [message, setMessage] = useState('');
  const [hasUserInteracted, setHasUserInteracted] = useState(false);
  const [isUploadingFile, setIsUploadingFile] = useState(false);
  const chatEndRef = useRef(null);
  const chatContainerRef = useRef(null);
  const fileInputRef = useRef(null);
  const [buttonText, setButtonText] = useState('SEND');
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const navigate = useNavigate();
  const formattedWorkflowName = workflowName ? workflowName.charAt(0).toUpperCase() + workflowName.slice(1) : null;
  const conversationSubtitle = conversationMode === 'ask'
    ? (generalChatSummary?.label || 'Ask Session')
    : `${formattedWorkflowName || 'AI-Powered Workflow'}${workflowHasChildren ? ' · Pack' : ''}`;
  // When not on chat page: allow brand click if provided, else only show 🧠 (ask→workflow toggle)
  const showModeToggle = isOnChatPage || conversationMode === 'ask' || Boolean(onBrandClick);
  const historyToggleHandler = typeof onHistoryToggle === 'function'
    ? onHistoryToggle
    : (typeof onAskHistoryToggle === 'function' ? onAskHistoryToggle : null);
  const showAskHistoryToggle = (showHistoryMenu || showAskHistoryMenu) && typeof historyToggleHandler === 'function';
  const avatarIcon = conversationMode === 'ask' ? '🧠' : '🤖';
  const baseShellTitle = String(appDisplayName || chatTheme?.branding?.name || 'MozaiksAI').trim() || 'MozaiksAI';
  const shellTitle = conversationMode === 'ask' ? `Ask ${baseShellTitle}` : baseShellTitle;
  const brandLogoSrc = getBrandLogoSrc(chatTheme);
  // const renderCountRef = useRef(0); // For debugging renders if needed

  // Optional debug: enable to trace renders
  // renderCountRef.current += 1;
  // console.debug(`ModernChatInterface render #${renderCountRef.current} with ${messages?.length || 0} messages`);
  // useEffect(() => {
  //   console.debug('ModernChatInterface received messages:', messages?.length || 0);
  // }, [messages]);

  // Chat flow UI tool event handling
  // This keeps the main chat interface clean and avoids hook violations.

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleScroll = () => {
    if (chatContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
      const hasScrolledUp = scrollTop > 100;
      setIsScrolledUp(!isAtBottom && hasScrolledUp);
    }
  };

  const { appId } = useParams();
  const pendingComposerPayload = pendingComposerInputToolCall?.payload || null;
  const pendingComposerPrompt = String(
    pendingComposerPayload?.prompt
      || pendingComposerReply?.prompt
      || pendingComposerPayload?.agent_message
      || pendingComposerPayload?.description
      || ''
  ).trim();
  const pendingComposerAgent = (
    pendingComposerInputToolCall?.agent
    || pendingComposerInputToolCall?.payload?.agent
    || pendingComposerInputToolCall?.payload?.agent_name
    || pendingComposerReply?.agent
    || 'Agent'
  );
  const hasPendingHarnessDecision = Boolean(
    pendingHarnessDecision
    && typeof pendingHarnessDecision === 'object'
    && pendingHarnessDecision.decision_id
  );
  const pendingHarnessTitle = PENDING_HARNESS_DECISION_TITLES[pendingHarnessDecision?.decision_type] || 'Harness Decision';
  const pendingHarnessActions = Array.isArray(pendingHarnessDecision?.actions)
    ? pendingHarnessDecision.actions
    : [];
  const pendingHarnessSelectedPaths = Array.isArray(pendingHarnessDecision?.selected_paths)
    ? pendingHarnessDecision.selected_paths
    : [];
  // Only show the "Awaiting Workflow Reply" banner when the agent explicitly
  // requested a custom interaction — either a non-empty prompt or a named
  // component (not the generic UserInputRequest fallback).  Bare AG2
  // conversational turns produce a UserInputRequest with no prompt; those
  // should route transparently through the textarea without visual noise.
  const showComposerBanner = Boolean(
    (pendingComposerInputToolCall || pendingComposerReply) && (
      pendingComposerPrompt ||
      pendingComposerReply ||
      (pendingComposerInputToolCall?.tool_name &&
       pendingComposerInputToolCall.tool_name !== 'UserInputRequest')
    )
  );
  const composerPlaceholder = hasPendingHarnessDecision
    ? 'Choose an action to continue...'
    : (pendingComposerInputToolCall || pendingComposerReply)
      ? 'Reply to continue the workflow...'
      : 'Message the agent\u2026';

  // Agent action handler - used by UI tool event responses
  const handleAgentAction = (action) => {
    console.log('Agent action received:', action);
    if (onAgentAction) {
      onAgentAction(action);
    }
  };

  const onSubmitClick = (event) => {
    event.preventDefault();
    if (buttonText === 'NEXT') {
      const envDefaultAppId =
        (typeof process !== 'undefined' && process.env && (process.env.REACT_APP_DEFAULT_APP_ID || process.env.REACT_APP_DEFAULT_app_id))
          ? (process.env.REACT_APP_DEFAULT_APP_ID || process.env.REACT_APP_DEFAULT_app_id)
          : undefined;
      const currentAppId = appId || envDefaultAppId;
      navigate("/chat/blueprint/" + currentAppId);
      return;
    }

    if (hasPendingHarnessDecision) return;
    if (message.trim() === '') return;

    const newMessage = { "sender": "user", "content": message, "artifactContext": artifactContext || null };
    onSendMessage(newMessage);
    setMessage('');
  };

  const onUploadClick = () => {
    if (!onUploadFile) return;
    if (buttonText === 'NEXT') return;
    if (hasPendingHarnessDecision) return;
    if (pendingComposerInputToolCall || pendingComposerReply) return;
    try {
      fileInputRef.current?.click?.();
    } catch {}
  };

  const onFileSelected = async (event) => {
    try {
      const file = event?.target?.files?.[0];
      if (!file || !onUploadFile) return;

      setIsUploadingFile(true);
      await onUploadFile(file);
    } finally {
      setIsUploadingFile(false);
      try {
        // allow re-selecting the same file
        if (event?.target) event.target.value = '';
      } catch {}
    }
  };

  const handleKeyPress = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSubmitClick(event);
    }
  };

  useEffect(() => {
    const isCompleted = messages.find(x => x.VerificationChatStatus === 1);
    if (isCompleted !== undefined) {
      setButtonText('NEXT');
    }
  }, [messages, loading]);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    const chatContainer = chatContainerRef.current;
    if (chatContainer) {
      chatContainer.addEventListener('scroll', handleScroll);
      handleScroll();

      return () => {
        chatContainer.removeEventListener('scroll', handleScroll);
      };
    }
  }, []);

  const handleModeToggle = () => {
    if (!onConversationModeChange) {
      return;
    }
    if (modeTogglePending) {
      console.log('⏳ [CHAT_INTERFACE] Mode toggle ignored because a transition is already in progress');
      return;
    }
    console.log('🔘 [CHAT_INTERFACE] Mode toggle clicked, current mode:', conversationMode);
    console.log('🔘 [CHAT_INTERFACE] isOnChatPage:', isOnChatPage);
    console.log('🔘 [CHAT_INTERFACE] showModeToggle:', showModeToggle);

    // When NOT on chat page and switching from Ask → Workflow:
    // Immediately switch mode (which triggers most recent workflow fetch), navigation will follow
    if (!isOnChatPage && conversationMode === 'ask') {
      console.log('🚀 [CHAT_INTERFACE] Off chat page in Ask mode → switching to workflow mode (will navigate)');
      onConversationModeChange('workflow');
      return;
    }

    const nextMode = conversationMode === 'workflow' ? 'ask' : 'workflow';
    console.log('🔘 [CHAT_INTERFACE] Toggling to mode:', nextMode);
    onConversationModeChange(nextMode);
    // Note: chat.enter_general_mode automatically creates/reuses session, no need for separate call
  };

  const handleAvatarClick = () => {
    if (onBrandClick) {
      onBrandClick();
      return;
    }
    // Only allow toggle from Workflow mode to Ask mode (robot emoji is clickable)
    // In Ask mode (brain emoji), the avatar is NOT clickable - use Mozaiks logo button instead
    if (showModeToggle && conversationMode === 'workflow') {
      handleModeToggle();
    }
  };

  const renderedMessages = (() => {
    // Determine the last chat index with a primary content message
    let lastContentIndex = -1;
    if (Array.isArray(messages)) {
      messages.forEach((m, i) => {
        if (m && m.content && !m.isTokenMessage && !m.isWarningMessage) {
          lastContentIndex = i;
        }
      });
    }

    return messages?.map((chat, index) => {
      if (!chat || chat.metadata?.hideInTranscript) {
        return null;
      }

      const isEmptyContent = !(chat.content && String(chat.content).trim().length);
      const hasStructured = !!(chat.structuredOutput && typeof chat.structuredOutput === 'object');
      const hasToolCall = !!chat.toolCall;
      const hasAttachment = !!chat.attachment;
      const hasTrace = Array.isArray(chat.trace) && chat.trace.length > 0;
      const isSystem = chat.isTokenMessage || chat.isWarningMessage;
      if (isEmptyContent && !chat.isThinking && !hasStructured && !hasToolCall && !hasAttachment && !hasTrace && !isSystem) {
        return null;
      }

      const isStructuredCapable = typeof chat.isStructuredCapable === 'boolean'
        ? chat.isStructuredCapable
        : !!(chat.agentName && structuredOutputs[chat.agentName]);

      try {
        if (['1','true','on','yes'].includes((localStorage.getItem('mozaiks.debug_render')||'').toLowerCase())) {
          console.log('[RENDER] ChatMessage', {
            index,
            id: chat.id,
            agent: chat.agentName,
            visual: chat.isVisual,
            structured: isStructuredCapable,
            streaming: chat.isStreaming,
            preview: (chat.content||'').slice(0,80)
          });
        }
      } catch {}

      return (
        <div key={index}>
          <ChatMessage
            message={chat.content}
            message_from={chat.sender}
            agentName={chat.agentName}
            isTokenMessage={chat.isTokenMessage}
            isWarningMessage={chat.isWarningMessage}
            isLatest={index === lastContentIndex}
            isStructuredCapable={isStructuredCapable}
            structuredOutput={chat.structuredOutput}
            structuredSchema={chat.structuredSchema}
            isThinking={chat.isThinking}
            attachment={chat.attachment}
            trace={chat.trace}
          />

          {chat.toolCall && (
            <ToolCallRenderer
              toolCall={chat.toolCall}
              isCompleted={chat.tool_call_completed || false}
              onArtifactAction={onArtifactAction}
              actionStatusMap={actionStatusMap}
              onResponse={(response) => {
                handleAgentAction({
                  type: 'tool_call_response',
                  tool_name: chat.toolCall.tool_name,
                  tool_call_id: chat.toolCall.tool_call_id,
                  response: response
                });
              }}
            />
          )}
        </div>
      );
    });
  })();

  const messageStackClass = isOnChatPage
    ? 'chat-feed-stream flex flex-col'
    : 'relative';

  const disableMobileShellChrome = conversationMode === 'ask';

  const messageStack = (
    <div className={messageStackClass} style={{ rowGap: 'var(--chat-bubble-stack-gap, 1rem)' }}>
      {/* Messages render below */}
      {renderedMessages}
      {/* Typing indicator slot (rendered when loading without messages updating) */}
      {loading && (
        <div className="flex justify-start px-0 message-container">
          <div className="mt-1 px-2 py-1 rounded-md bg-transparent text-[rgba(var(--color-primary-light-rgb),0.7)] flex items-center gap-1 text-xs font-mono tracking-wide typing-indicator" aria-label="Assistant is typing" role="status">
            <span className="typing-dot" />
            <span className="typing-dot delay-150" />
            <span className="typing-dot delay-300" />
          </div>
        </div>
      )}
      <div ref={chatEndRef} />
    </div>
  );

  return (
    <div className={`relative chat-shell ${conversationMode === 'ask' ? 'chat-shell-ask' : 'chat-shell-workflow'} flex flex-col h-full rounded-2xl border border-[rgba(var(--color-primary-light-rgb),0.3)] md:overflow-hidden overflow-visible shadow-2xl bg-gradient-to-br from-white/5 to-[rgba(var(--color-primary-rgb),0.05)] backdrop-blur-sm ${plainContainer ? '' : 'cosmic-ui-module'} p-0`} style={{ overflow: 'clip' }}>
      {/* Per-turn loading: rely on subtle typing indicator inside messages area; avoid full-page spinner after init */}

      {/* Fixed Command Center Header - Dark background to match artifact */}
      {!hideHeader && (
      <div className={`flex-shrink-0 chat-header-backdrop ${conversationMode === 'ask' ? 'chat-header-ask' : 'chat-header-workflow'} border-b border-[rgba(var(--color-primary-light-rgb),0.22)] backdrop-blur-xl`}>
        <div className="flex flex-row items-center justify-between px-3 py-2.5 sm:px-4 sm:py-3 md:px-5 md:py-3.5">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
            {showAskHistoryToggle && (
              <button
                type="button"
                onClick={historyToggleHandler}
                className="md:hidden inline-flex flex-col gap-1.5 p-2 rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.35)] text-white/80 hover:text-white hover:border-[rgba(var(--color-primary-light-rgb),0.7)] transition focus:outline-none focus:ring-2 focus:ring-[rgba(var(--color-primary-light-rgb),0.6)]"
                aria-label={`Show recent ${conversationMode === 'workflow' ? 'workflows' : 'chats'}`}
                title={historyMenuLabel || 'Recents'}
              >
                <span className="w-5 h-0.5 bg-current rounded-full"></span>
                <span className="w-5 h-0.5 bg-current rounded-full"></span>
                <span className="w-5 h-0.5 bg-current rounded-full"></span>
              </button>
            )}
            {/* Chat Icon + Title */}
            {/* Avatar is clickable ONLY in workflow mode (to switch to Ask) */}
            {conversationMode === 'workflow' ? (
              <button
                type="button"
                onClick={handleAvatarClick}
                disabled={modeTogglePending}
                className={`flex items-center gap-2 sm:gap-3 focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-light)]/60 rounded-xl min-w-0 ${modeTogglePending ? 'opacity-60 cursor-not-allowed' : ''}`}
                title={modeTogglePending ? 'Switching modes…' : 'Switch to Ask Mode'}
              >
                <span className="w-9 h-9 sm:w-10 sm:h-10 md:w-12 md:h-12 rounded-lg sm:rounded-xl flex items-center justify-center shadow-lg transition-all duration-300 flex-shrink-0 bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)]">
                  <span className="text-xl sm:text-2xl" role="img" aria-hidden="true">🤖</span>
                </span>
                <span className="text-left min-w-0 flex-1">
                    <span className="block text-sm sm:text-lg md:text-xl font-bold text-white tracking-tight truncate heading-font">{shellTitle}</span>
                    <span className="block text-[10px] sm:text-xs text-gray-400 truncate transmission-typing-font">{conversationSubtitle}</span>
                </span>
              </button>
            ) : (
              /* In Ask mode (or when toggle not available), avatar is NOT clickable */
              <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
                <span className={`w-9 h-9 sm:w-10 sm:h-10 md:w-12 md:h-12 rounded-lg sm:rounded-xl flex items-center justify-center shadow-lg flex-shrink-0 ${conversationMode === 'ask'
                  ? 'bg-gradient-to-br from-[var(--color-secondary)] to-[var(--color-primary)]'
                  : 'bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)]'}`}>
                  <span className="text-xl sm:text-2xl" role="img" aria-hidden="true">{avatarIcon}</span>
                </span>
                <span className="text-left min-w-0 flex-1">
                    <span className="block text-sm sm:text-lg md:text-xl font-bold text-white tracking-tight truncate heading-font">{shellTitle}</span>
                    <span className="block text-[10px] sm:text-xs text-gray-400 truncate transmission-typing-font">{conversationSubtitle}</span>
                </span>
              </div>
            )}
          </div>

          {overlayMode && typeof onOverlayClose === 'function' && (
            <button
              type="button"
              onClick={onOverlayClose}
              className="inline-flex items-center justify-center px-2.5 py-1.5 sm:px-3 sm:py-2 rounded-lg border border-white/20 bg-black/40 text-white text-[10px] sm:text-xs font-semibold uppercase tracking-wide mr-2"
              title="Close chat overlay"
            >
              Close
            </button>
          )}

          {/* Mozaiks Logo Button - context-aware behavior */}
          {/* In Workflow mode: toggles artifact panel | In Ask mode: switches to Workflow mode */}
          {isOnChatPage && (
            <>
              <button
                onClick={() => {
                  if (modeTogglePending) {
                    return;
                  }
                  if (conversationMode === 'workflow' && onArtifactToggle) {
                    // In workflow mode, toggle the artifact panel
                    onArtifactToggle();
                  } else if (conversationMode === 'ask') {
                    // In ask mode, switch to workflow mode
                    handleModeToggle();
                  } else if (onArtifactToggle) {
                    // Fallback: toggle artifact if available
                    onArtifactToggle();
                  }
                }}
                disabled={modeTogglePending}
                className={`hidden md:block group relative p-2 md:p-3 rounded-lg bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.1)] to-[rgba(var(--color-secondary-rgb),0.1)] border border-[rgba(var(--color-primary-light-rgb),0.3)] hover:border-[rgba(var(--color-primary-light-rgb),0.6)] transition-all duration-300 backdrop-blur-sm artifact-hover-glow flex-shrink-0 ${modeTogglePending ? 'opacity-60 cursor-not-allowed pointer-events-none' : ''}`}
                title={modeTogglePending ? 'Switching modes…' : (conversationMode === 'ask' ? 'Switch to Workflow Mode' : (artifactToggleLabel || 'Toggle Artifact Canvas'))}
              >
                <img
                  src={brandLogoSrc}
                  className="w-8 h-8 md:w-10 md:h-10 opacity-70 group-hover:opacity-100 transition-all duration-300 group-hover:scale-105"
                  alt={conversationMode === 'ask' ? 'Switch to Workflow' : 'Artifact Canvas'}
                  onError={applyBrandImageFallback}
                />
                <div className="absolute inset-0 bg-[rgba(var(--color-primary-light-rgb),0.1)] rounded-lg blur opacity-0 group-hover:opacity-100 transition-opacity duration-300 -z-10"></div>
              </button>
              <button
                onClick={() => {
                  if (modeTogglePending) {
                    return;
                  }
                  if (conversationMode === 'workflow' && onArtifactToggle) {
                    onArtifactToggle();
                  } else if (conversationMode === 'ask') {
                    handleModeToggle();
                  } else if (onArtifactToggle) {
                    onArtifactToggle();
                  }
                }}
                disabled={modeTogglePending}
                className={`md:hidden group relative p-2 rounded-lg bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.15)] to-[rgba(var(--color-secondary-rgb),0.15)] border border-[rgba(var(--color-primary-light-rgb),0.35)] hover:border-[rgba(var(--color-primary-light-rgb),0.7)] transition-all duration-300 backdrop-blur-sm flex-shrink-0 ${modeTogglePending ? 'opacity-60 cursor-not-allowed pointer-events-none' : ''}`}
                title={modeTogglePending ? 'Switching modes…' : (conversationMode === 'ask' ? 'Switch to Workflow Mode' : (artifactToggleLabel || 'Toggle Artifact Canvas'))}
              >
                <img
                  src={brandLogoSrc}
                  className="w-7 h-7 opacity-80 group-hover:opacity-100 transition-all duration-300 group-hover:scale-105"
                  alt={conversationMode === 'ask' ? 'Switch to Workflow' : 'Artifact Canvas'}
                  onError={applyBrandImageFallback}
                />
              </button>
            </>
          )}

          {/* Desktop: Artifact Canvas Toggle Button - only show when NOT on chat page */}
          {onArtifactToggle && !isOnChatPage && (
            <button
              onClick={onArtifactToggle}
              className="hidden md:block group relative p-2 md:p-3 rounded-lg bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.1)] to-[rgba(var(--color-secondary-rgb),0.1)] border transition-all duration-300 backdrop-blur-sm artifact-hover-glow artifact-cta flex-shrink-0"
              title={artifactToggleLabel || 'Toggle Artifact Canvas'}
            >
              <img
                src={brandLogoSrc}
                className="w-8 h-8 md:w-10 md:h-10 opacity-70 group-hover:opacity-100 transition-all duration-300 group-hover:scale-105"
                alt="Artifact Canvas"
                onError={applyBrandImageFallback}
              />
              <div className="absolute inset-0 bg-[rgba(var(--color-primary-light-rgb),0.1)] rounded-lg blur opacity-0 group-hover:opacity-100 transition-opacity duration-300 -z-10"></div>
            </button>
          )}
        </div>

        {/* Initial Message - only show for UserDriven workflows and if message exists */}
        {startupMode === 'UserDriven' && initialMessageToUser && (
          <div className="pb-2 sm:pb-3 px-3 sm:px-4 md:px-6 flex justify-center">
            <div className="relative px-2 py-1 sm:px-3 sm:py-1.5 rounded-lg bg-[rgba(var(--color-secondary-rgb),0.15)] border border-[rgba(var(--color-secondary-rgb),0.3)] flex items-center justify-center space-x-1.5 sm:space-x-2 backdrop-blur-sm max-w-full sm:max-w-md">
              <div className="relative w-1.5 h-1.5 sm:w-2 sm:h-2 bg-[var(--color-secondary)] rounded-full animate-pulse flex-shrink-0"></div>
              <span className="relative text-[var(--color-secondary-light)] text-[10px] sm:text-xs font-semibold tracking-wide heading-font text-center truncate">
                {initialMessageToUser}
              </span>
            </div>
          </div>
        )}
      </div>
      )}
    {/* Chat Messages Area - ONLY THIS SCROLLS */}
    <div className="flex-1 relative overflow-hidden" role="log" aria-live="polite" aria-relevant="additions">
      <div className={`absolute inset-0 pointer-events-none ${isOnChatPage ? 'chat-feed-backdrop' : 'bg-[rgba(0,0,0,0.45)]'} ${isOnChatPage ? (conversationMode === 'ask' ? 'chat-backdrop-ask' : 'chat-backdrop-workflow') : ''} z-0`} />
        <div
          ref={chatContainerRef}
          className={`absolute inset-0 overflow-y-auto my-scroll1 z-10 ${isOnChatPage ? '' : 'px-2 py-2 md:p-6'}`}
        >
          {isOnChatPage ? (
            <div className={`chat-feed-shell ${conversationMode === 'ask' ? 'chat-feed-shell-ask' : ''}`}>
              {messageStack}
            </div>
          ) : disableMobileShellChrome ? (
            <div className="px-2 pb-16 pt-3">
              {messageStack}
            </div>
          ) : (
            messageStack
          )}
        </div>

        {/* Scroll gradient fades */}
        <div className="chat-scroll-fade-top" aria-hidden="true" />
        <div className="chat-scroll-fade-bottom" aria-hidden="true" />

        {/* Jump to Present Button - Positioned over the messages area */}
        {isScrolledUp && (
      <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 z-10">
            <button
              onClick={scrollToBottom}
        className="jump-present"
            >
              Jump to Present
            </button>
          </div>
        )}
      </div>

  {/* Fixed Transmission Input Area - Never moves */}
            <div className={`flex-shrink-0 p-3 sm:p-4 border-t border-[rgba(var(--color-primary-light-rgb),0.2)] bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.05)] to-[rgba(var(--color-secondary-rgb),0.05)] backdrop-blur-xl shadow-lg transition-all duration-500 transmission-input-tight rounded-b-[inherit]`}>
        {false && showComposerBanner && (
          <div className="mb-2 rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.3)] bg-[rgba(var(--color-primary-rgb),0.08)] px-3 py-2 backdrop-blur-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-primary-light)]">
                  Awaiting Workflow Reply
                </div>
                <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
                  {pendingComposerPrompt || `${pendingComposerAgent} is waiting for your reply. Your next message will be sent directly to this workflow step.`}
                </div>
              </div>
              {typeof onPendingComposerInputSkip === 'function' && pendingComposerInputToolCall && (
                <button
                  type="button"
                  onClick={() => {
                    setMessage('');
                    onPendingComposerInputSkip(pendingComposerInputToolCall);
                  }}
                  className="shrink-0 rounded-md border border-[rgba(var(--color-primary-light-rgb),0.35)] bg-white/5 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--color-text-secondary)] transition hover:border-[rgba(var(--color-primary-light-rgb),0.65)] hover:text-white"
                >
                  Skip
                </button>
              )}
            </div>
          </div>
        )}
        {hasPendingHarnessDecision && (
          <div className="mb-3 rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.28)] bg-[rgba(8,15,32,0.8)] px-3 py-3 backdrop-blur-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-primary-light)]">
                  Build Routing
                </div>
                <div className="mt-1 text-sm font-semibold text-white">
                  {pendingHarnessTitle}
                </div>
              </div>
              {typeof pendingHarnessDecision?.confidence === 'number' && (
                <div className="shrink-0 rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.22)] bg-white/5 px-2.5 py-1 text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)]">
                  {`Confidence ${Math.round((pendingHarnessDecision.confidence || 0) * 100)}%`}
                </div>
              )}
            </div>

            <p className="mt-3 text-sm font-medium text-white">
              {pendingHarnessDecision?.message}
            </p>
            <p className="mt-2 text-xs leading-6 text-[var(--color-text-secondary)]">
              {pendingHarnessDecision?.rationale}
            </p>

            {pendingHarnessDecision?.clarification_question && (
              <div className="mt-3 rounded-lg border border-[rgba(var(--color-secondary-rgb),0.28)] bg-[rgba(var(--color-secondary-rgb),0.08)] px-3 py-2 text-xs text-white/90">
                {pendingHarnessDecision.clarification_question}
              </div>
            )}

            {pendingHarnessSelectedPaths.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {pendingHarnessSelectedPaths.map((path) => (
                  <span
                    key={path}
                    className="rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.2)] bg-white/5 px-2 py-1 font-mono text-[11px] text-[var(--color-text-secondary)]"
                  >
                    {path}
                  </span>
                ))}
              </div>
            )}

            {pendingHarnessActions.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {pendingHarnessActions.map((action) => (
                  <button
                    key={action.action_id}
                    type="button"
                    disabled={pendingHarnessDecisionBusy}
                    onClick={() => typeof onPendingHarnessDecisionAction === 'function' && onPendingHarnessDecisionAction(action)}
                    className="rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.28)] bg-[rgba(var(--color-primary-rgb),0.14)] px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-white transition hover:bg-[rgba(var(--color-primary-rgb),0.2)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {pendingHarnessDecisionBusy ? 'Working...' : action.label}
                  </button>
                ))}
              </div>
            )}

            {pendingHarnessDecisionError && (
              <div className="mt-3 rounded-lg border border-[rgba(248,113,113,0.35)] bg-[rgba(127,29,29,0.22)] px-3 py-2 text-xs text-[rgba(254,202,202,0.95)]">
                {pendingHarnessDecisionError}
              </div>
            )}
          </div>
        )}
        <form onSubmit={onSubmitClick} className="flex gap-2 sm:gap-3 flex-row items-center">
          {/* Hidden file input (Upload) */}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={onFileSelected}
          />

          <div className="flex-1 relative min-w-0 flex items-center">
            <textarea
              value={message}
              onChange={(e) => {
                setMessage(e.target.value);
                if (!hasUserInteracted) setHasUserInteracted(true);
                // Auto-resize textarea
                e.target.style.height = 'auto';
                e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
              }}
              onKeyPress={handleKeyPress}
              onFocus={() => setHasUserInteracted(true)}
              placeholder={composerPlaceholder}
              disabled={buttonText === 'NEXT' || hasPendingHarnessDecision}
              rows={1}
              className={`w-full bg-white/10 border-2 rounded-lg sm:rounded-xl px-2.5 sm:px-3 py-1.5 sm:py-2 mt-0.5 text-[var(--color-text-primary)] text-slate-100 text-sm sm:text-base placeholder:text-[var(--color-text-secondary)] placeholder:text-slate-400 placeholder:text-xs sm:placeholder:text-sm focus:outline-none resize-none transition-all duration-300 transmission-typing-font min-h-[36px] sm:min-h-[40px] max-h-[120px] my-scroll1 backdrop-blur-sm ${
                hasUserInteracted
                  ? 'border-[rgba(var(--color-primary-light-rgb),0.5)] focus:border-[rgba(var(--color-primary-light-rgb),0.8)] focus:bg-white/15 focus:shadow-[0_0_25px_rgba(var(--color-primary-light-rgb),0.4)]'
                  : 'border-[rgba(var(--color-primary-light-rgb),0.3)] focus:border-[rgba(var(--color-primary-light-rgb),0.7)] focus:bg-white/15 focus:shadow-[0_0_30px_rgba(var(--color-primary-light-rgb),0.5)] shadow-[0_0_15px_rgba(var(--color-primary-light-rgb),0.2)]'
              }`}
              style={{
                height: '36px',
                overflowY: message.split('\n').length > 2 || message.length > 100 ? 'auto' : 'hidden'
              }}
            />
            {!hasUserInteracted && (
              <div className="absolute -top-1 -right-1 input-prompt-ping rounded-full subtle-ping" aria-hidden="true"></div>
            )}
          </div>

          {/* Command Button - icon-only for simplicity */}
          {onUploadFile && (
            <button
              type="button"
              onClick={onUploadClick}
              disabled={buttonText === 'NEXT' || isUploadingFile || hasPendingHarnessDecision || !!pendingComposerInputToolCall || !!pendingComposerReply}
              className={
                `px-2 py-1.5 rounded-md transition-all duration-300 min-w-[36px] sm:min-w-[40px] w-auto h-8 sm:h-9 oxanium font-bold text-[13px] flex items-center justify-center letter-spacing-wide border-2 flex-shrink-0 ` +
                `${(buttonText === 'NEXT' || isUploadingFile)
                  ? 'bg-gray-800/50 text-gray-400 cursor-not-allowed border-gray-600/50'
                  : 'bg-white/10 text-white border-[rgba(var(--color-primary-light-rgb),0.35)] hover:border-[rgba(var(--color-primary-light-rgb),0.7)] hover:bg-white/15 active:scale-95'
                }`
              }
              title={isUploadingFile ? 'Uploading…' : 'Upload a file'}
              aria-label={isUploadingFile ? 'Uploading file' : 'Upload file'}
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 sm:w-5 sm:h-5" aria-hidden="true">
                <path fillRule="evenodd" d="M15.621 4.379a3 3 0 0 0-4.242 0l-7 7a3 3 0 0 0 4.241 4.243h.001l.497-.5a.75.75 0 0 1 1.064 1.057l-.498.501-.002.002a4.5 4.5 0 0 1-6.364-6.364l7-7a4.5 4.5 0 0 1 6.368 6.36l-3.455 3.553A2.625 2.625 0 1 1 9.52 9.52l3.45-3.451a.75.75 0 1 1 1.061 1.06l-3.45 3.451a1.125 1.125 0 0 0 1.587 1.595l3.454-3.553a3 3 0 0 0 0-4.243Z" clipRule="evenodd" />
              </svg>
            </button>
          )}

          <button
            type="submit"
            disabled={!message.trim() || hasPendingHarnessDecision}
            className={`
              px-2 py-1.5 rounded-md transition-all duration-300 min-w-[36px] sm:min-w-[40px] w-auto h-8 sm:h-9 oxanium font-bold text-[13px] flex items-center justify-center letter-spacing-wide border-2 flex-shrink-0
              ${(!message.trim())
                ? 'bg-gray-800/50 text-gray-400 cursor-not-allowed border-gray-600/50'
                : 'bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.8)] to-[rgba(var(--color-primary-dark-rgb),0.8)] hover:from-[rgba(var(--color-primary-light-rgb),0.9)] hover:to-[rgba(var(--color-primary-light-rgb),0.9)] text-white border-[rgba(var(--color-primary-light-rgb),0.5)] hover:border-[rgba(var(--color-primary-light-rgb),0.7)] shadow-sm [box-shadow:0_0_0_rgba(var(--color-primary-rgb),0.1)] hover:[box-shadow:0_0_0_rgba(var(--color-primary-light-rgb),0.2)] active:scale-95'
              }
            `}
          >
            {buttonText === 'NEXT' ? (
              <span className="text-[11px] font-bold tracking-widest" aria-label="Continue">NEXT</span>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 sm:w-5 sm:h-5" aria-hidden="true">
                <path d="M3.105 2.288a.75.75 0 0 0-.826.95l1.414 4.926A1.5 1.5 0 0 0 5.135 9.25h6.115a.75.75 0 0 1 0 1.5H5.135a1.5 1.5 0 0 0-1.442 1.086l-1.414 4.926a.75.75 0 0 0 .826.95 28.897 28.897 0 0 0 15.293-7.154.75.75 0 0 0 0-1.115A28.897 28.897 0 0 0 3.105 2.288Z" />
              </svg>
            )}
          </button>
        </form>
      </div>

  {/* Mobile artifact button now lives in ConnectionStatus row */}
    </div>
  );
};

export default ModernChatInterface;
