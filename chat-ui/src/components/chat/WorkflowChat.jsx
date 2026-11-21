import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { WebSocketApiAdapter } from '../../adapters/api';
import { submitToolCallResponse } from '../../adapters/uiToolResponse';
import platform from '../../platform/index.js';
import config from '../../config';
import { useChatWebSocket } from '../../pages/hooks';
import ShellUIToolRenderer from '../../core/ui/ShellUIToolRenderer';
import ChatInterface from './ChatInterface';
import { buildInlineToolCallMessageFromEvent, upsertInlineToolCallMessage } from './inlineSurfaces';

function normalizeWorkflowEvent(event) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  let normalized = { ...event };

  if (
    typeof normalized.content === 'string'
    && !String(normalized.type || '').startsWith('chat.')
  ) {
    const trimmed = normalized.content.trim();
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object' && parsed.type) {
          normalized = { ...parsed };
        }
      } catch {
        // Ignore double-serialized content we cannot parse.
      }
    }
  }

  if (normalized.data && typeof normalized.data === 'object') {
    normalized = { ...normalized.data, ...normalized };
  }

  if (normalized.content && typeof normalized.content === 'object') {
    normalized.content = normalized.content.content
      || normalized.content.text
      || normalized.content.message
      || '';
  }

  return normalized;
}

function getWorkflowEventType(event) {
  const rawType = String(event?.type || '');
  return rawType.startsWith('chat.') ? rawType.slice(5) : rawType;
}

function getWorkflowEventContent(event) {
  return String(event?.content || event?.message || '');
}

function getWorkflowAgentName(event) {
  return String(event?.agent || event?.agent_name || event?.sender || 'assistant');
}

function WorkflowConversation({
  workflow,
  userId,
  appId,
  initialContext,
  onMessage,
  onComplete,
  onClose,
  className = '',
  showHeader = true,
  brandName,
  logo,
  backgroundImage,
  presentation,
  freshSession = false,
  getAccessToken,
  onArtifact,
  onReplyActivity,
  onSessionChange,
  composerLabel,
  transcriptLabel,
}) {
  const [chatId, setChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [canReply, setCanReply] = useState(false);
  const [activity, setActivity] = useState('idle');
  const [sendError, setSendError] = useState('');
  const [ended, setEnded] = useState(false);
  const expectingUser = useRef(false);
  const pendingUser = useRef(null);
  const activeUserTurn = useRef(false);
  const seenArtifacts = useRef(new Set());
  const streams = useRef(new Map());
  const messageSequence = useRef(0);
  const active = useRef(true);
  const studio = presentation === 'studio';
  const callbacks = useRef({});
  callbacks.current = { onArtifact, onMessage, onComplete, onSessionChange, onReplyActivity };
  useEffect(() => { callbacks.current.onReplyActivity?.(activity); }, [activity]);
  useEffect(() => {
    if (chatId) callbacks.current.onSessionChange?.(chatId);
    return () => { if (chatId) callbacks.current.onSessionChange?.(null); };
  }, [chatId]);

  const resolvedAppId = appId || config.get('chat.defaultAppId') || 'default';
  const tokenProvider = useRef(getAccessToken);
  tokenProvider.current = getAccessToken;
  const hasTokenProvider = typeof getAccessToken === 'function';
  const workflowApi = useMemo(
    () => new WebSocketApiAdapter({
      ...config.get('api'),
      ...(hasTokenProvider && { getAccessToken: () => tokenProvider.current?.() }),
    }),
    [hasTokenProvider]
  );

  const handleRuntimeEvent = useCallback((rawEvent) => {
    const event = normalizeWorkflowEvent(rawEvent);
    if (!event) {
      return;
    }

    const receiveAnswer = (complete) => {
      const agent = getWorkflowAgentName(event);
      const streamKey = event.stream_id || agent;
      const previous = streams.current.get(streamKey);
      const fragment = getWorkflowEventContent(event);
      if (!fragment && !previous) return;
      if (!complete && previous && Number.isInteger(event.chunk_seq) && event.chunk_seq <= previous.chunkSeq) return;
      const message = {
        id: previous?.id || ++messageSequence.current,
        sender: 'assistant', agentName: agent, timestamp: previous?.timestamp || new Date().toISOString(),
        content: complete ? fragment || previous.content : (previous?.content || '') + fragment,
        isStreaming: !complete,
      };
      if (complete) streams.current.delete(streamKey);
      else streams.current.set(streamKey, { ...message, chunkSeq: event.chunk_seq });
      setMessages(items => previous ? items.map(item => item.id === message.id ? message : item) : [...items, message]);
      setActivity(complete ? 'replied' : 'answering');
      if (complete) callbacks.current.onMessage?.(message);
    };
    const eventType = getWorkflowEventType(event);
    const callbacksForSession = callbacks.current;
    switch (eventType) {
      case 'awaiting_reply':
        expectingUser.current = true;
        activeUserTurn.current = false;
        setCanReply(true);
        return;
      case 'text':
      case 'print':
      case 'message':
      case 'agent_message':
        if (studio && event.replay) {
          const content = getWorkflowEventContent(event);
          if (!content) return;
          const agentName = getWorkflowAgentName(event);
          const sender = agentName.toLowerCase() === 'user' ? 'user' : 'assistant';
          const replayed = { id: ++messageSequence.current, sender, agentName, content, replayIndex: event.index,
            timestamp: event.timestamp || new Date().toISOString() };
          if (sender === 'user' && pendingUser.current?.content === content) {
            pendingUser.current = null;
            setSendError('');
          }
          setMessages(items => {
            const matches = item => item.sender === sender && (sender === 'user' || item.agentName === agentName);
            const recorded = items.findIndex(item => matches(item) && Number.isInteger(event.index) && item.replayIndex === event.index);
            if (recorded >= 0) return items;
            const live = items.findIndex(item => matches(item) && item.replayIndex == null
              && (item.content === content || (item.isStreaming && content.startsWith(item.content))));
            if (live < 0) return [...items, replayed];
            return items.map((item, index) => index === live ? { ...replayed, id: item.id } : item);
          });
          return;
        }
        if (getWorkflowAgentName(event).toLowerCase() === 'user') {
          const text = getWorkflowEventContent(event);
          if (text && pendingUser.current?.content === text) {
            setSendError('');
            pendingUser.current = null;
          } else if (text) {
            setMessages(previous => [...previous, { id: event.id || ++messageSequence.current, sender: 'user', content: text }]);
          }
          return;
        }
        receiveAnswer(true);
        return;
      case 'stream_chunk':
        receiveAnswer(false);
        return;
      case 'stream_end':
        receiveAnswer(true);
        return;
      case 'tool_call':
      case 'ui.render': {
        if ((event.display || event.mode) === 'artifact') {
          const id = event.tool_call_id;
          if (id && ['ui.render', 'chat.ui.render', 'chat.tool_call'].includes(rawEvent.type)
              && rawEvent.data?.tool_call_id === id && !seenArtifacts.current.has(id)) {
            seenArtifacts.current.add(id);
            callbacksForSession.onArtifact?.(rawEvent, { chatId, afterUserInput: activeUserTurn.current && !event.replay });
          }
          return;
        }
        const inlineEntry = buildInlineToolCallMessageFromEvent(event, workflow);
        if (!inlineEntry) {
          return;
        }
        setMessages((prev) => {
          return upsertInlineToolCallMessage(prev, {
            ...inlineEntry,
            timestamp: new Date().toISOString(),
          });
        });
        callbacksForSession.onMessage?.(inlineEntry);
        return;
      }
      case 'run_complete':
      case 'workflow_complete':
        if (event.status === 'failed') {
          activeUserTurn.current = false;
          setActivity('idle');
          setError('The workflow could not continue.');
          return;
        }
        if (event.run_completed === true && !event.awaiting_user_input) {
          activeUserTurn.current = false;
          expectingUser.current = false;
          setCanReply(false);
          setEnded(true);
        }
        if (event.run_completed === false || event.awaiting_user_input === true) return;
        if (
          event?.status === 1 ||
          String(event?.status ?? '').trim().toLowerCase() === '1' ||
          ['completed', 'complete', 'success', 'succeeded', 'done', 'ok'].includes(
            String(event?.status ?? '').trim().toLowerCase()
          )
        ) {
          callbacksForSession.onComplete?.(event);
        }
        return;
      case 'error':
        if (event.error_code === 'CHAT_BUSY' && pendingUser.current) {
          const refused = pendingUser.current;
          pendingUser.current = null;
          activeUserTurn.current = false;
          expectingUser.current = true;
          setCanReply(true);
          setActivity('idle');
          setInputValue(refused.content);
          setMessages(previous => previous.filter(message => message !== refused));
          setSendError('The agent is finishing the previous turn. Send your message again.');
          return;
        }
        activeUserTurn.current = false;
        setActivity('idle');
        setError(event.message || 'Workflow error');
        return;
      default:
        return;
    }
  }, [chatId, workflow, studio]);

  const { connectionStatus, retryConnection } = useChatWebSocket({
    api: workflowApi,
    appId: resolvedAppId,
    userId,
    chatId,
    workflowName: workflow,
    workflowConfigLoaded: true,
    onMessage: handleRuntimeEvent,
    recoverOnDrop: studio && !ended,
    onInterrupted: () => {
      if (pendingUser.current) setSendError('Delivery of your last message is unconfirmed. Check the conversation before sending it again.');
      streams.current.clear();
      expectingUser.current = false;
      activeUserTurn.current = false;
      setCanReply(false);
      setActivity('idle');
    },
  });

  useEffect(() => {
    let mounted = true;
    active.current = true;
    const controller = new AbortController();
    setMessages([]);
    setChatId(null);

    const startWorkflow = async () => {
      try {
        setLoading(true);
        setError(null);

        const result = await workflowApi.startChat(
          resolvedAppId,
          workflow,
          userId,
          { signal: controller.signal },
          initialContext || null,
          null,
          { forceNew: freshSession }
        );

        if (!mounted) {
          return;
        }

        if (result?.chat_id) {
          setChatId(result.chat_id);
          return;
        }

        const detail = typeof result?.detail === 'string'
          ? result.detail
          : result?.detail?.message || result?.detail?.error;
        setError(detail || result?.error || 'Failed to start workflow');
      } catch (err) {
        if (mounted) {
          setError(err.message);
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };

    if (workflow && userId) {
      // StrictMode can dispose the first mount before a new chat is requested.
      queueMicrotask(() => { if (mounted) startWorkflow(); });
    }

    return () => {
      mounted = false;
      active.current = false;
      controller.abort();
    };
  }, [workflowApi, workflow, userId, resolvedAppId, initialContext, freshSession]);

  const handleSendMessage = useCallback(async (e) => {
    e?.preventDefault?.();
    const trimmed = (e?.content ?? inputValue).trim();
    if (!trimmed || !chatId || connectionStatus !== 'connected' || isSending || (studio && !expectingUser.current)) {
      return;
    }

    const userMessage = {
      id: ++messageSequence.current,
      sender: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    expectingUser.current = false;
    pendingUser.current = userMessage;
    activeUserTurn.current = true;
    setCanReply(false);
    setActivity('answering');
    setSendError('');
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsSending(true);

    try {
      const result = await workflowApi.sendMessageToWorkflow(trimmed, resolvedAppId, userId, workflow, chatId);
      if (result === false || result?.success === false) throw new Error();
    } catch {
      if (!active.current || pendingUser.current !== userMessage) return;
      expectingUser.current = true;
      activeUserTurn.current = false;
      pendingUser.current = null;
      setCanReply(true);
      setMessages(previous => previous.filter(message => message !== userMessage));
      setInputValue(trimmed);
      setActivity('idle');
      setSendError('Your message could not be sent. Try again.');
    } finally {
      if (active.current) setIsSending(false);
    }
  }, [chatId, connectionStatus, inputValue, isSending, resolvedAppId, userId, workflowApi, workflow, studio]);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  if (loading) {
    return (
      <div
        className={`flex items-center justify-center h-full ${className}`}
        style={{ backgroundColor: 'var(--color-background, var(--mozaiks-bg, #0f172a))', fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)' }}
      >
        <div className="text-center">
          <div
            className="animate-spin rounded-full h-8 w-8 border-b-2 mx-auto mb-2"
            style={{ borderColor: 'var(--mozaiks-primary, #3b82f6)' }}
          ></div>
          <p className="text-sm" style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}>
            Starting {brandName || workflow}...
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={`flex items-center justify-center h-full ${className}`}
        style={{ backgroundColor: 'var(--color-background, var(--mozaiks-bg, #0f172a))', fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)' }}
      >
        <div className="text-center text-red-500">
          <p className="font-semibold">Failed to start workflow</p>
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (studio) {
    const agent = brandName || workflow;
    const phase = ended ? 'complete' : activity === 'answering' ? 'working' : 'idle';
    const detail = ended ? 'Conversation ended.' : activity === 'answering' ? agent + ' is replying…'
      : activity === 'replied' ? agent + ' replied' : canReply ? 'Ready for your question.' : 'Opening chat…';
    return <div className={'flex h-full min-h-0 flex-col ' + className}>
      {sendError && <p role="alert">{sendError}</p>}
      <ChatInterface messages={messages} onSendMessage={handleSendMessage} loading={activity === 'answering'}
        onAgentAction={action => submitToolCallResponse(action.tool_call_id, action.response, {
          baseUrl: workflowApi.getHttpBaseUrl(), token: getAccessToken ? getAccessToken() : platform.getAccessToken(),
        })}
        hideHeader={!showHeader} plainContainer isOnChatPage={false} appDisplayName={agent} workflowName={workflow}
        connectionStatus={connectionStatus} onRetry={ended ? undefined : retryConnection} chatStatus={{ phase, detail }}
        composerDisabled={!canReply || connectionStatus !== 'connected' || ended} composerLabel={composerLabel}
        composerValue={inputValue} onComposerChange={setInputValue} transcriptLabel={transcriptLabel}/>
    </div>;
  }

  const containerStyle = {
    fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)',
    backgroundColor: 'var(--color-background, var(--mozaiks-bg, #0f172a))',
    color: 'var(--color-text-primary, var(--mozaiks-text, #f8fafc))',
  };

  const headerStyle = {
    backgroundColor: 'var(--color-surface, var(--mozaiks-bg-secondary, #1e293b))',
    borderColor: 'var(--color-border-subtle, var(--mozaiks-border, #334155))',
  };

  const messagesStyle = backgroundImage
    ? { backgroundImage: `url(${backgroundImage})`, backgroundSize: 'cover', backgroundPosition: 'center' }
    : {};

  return (
    <div className={`flex flex-col h-full ${className}`} style={containerStyle}>
      {sendError && <p role="alert">{sendError}</p>}
      {showHeader && (
        <div className="flex-shrink-0 p-4 border-b" style={headerStyle}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {logo && (
                <img src={logo} alt={brandName || workflow} className="h-8 w-auto" />
              )}
              <div>
                <h2 className="text-lg font-semibold">{brandName || workflow}</h2>
                <span
                  className="text-xs"
                  style={{ color: connectionStatus === 'connected' ? '#22c55e' : 'var(--mozaiks-text-secondary, #6b7280)' }}
                >
                  {connectionStatus}
                </span>
              </div>
            </div>
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                className="p-2 rounded-lg transition-colors"
                style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}
                aria-label="Close chat"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-4" style={messagesStyle}>
        {messages.length === 0 ? (
          <div className="text-center py-8" style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}>
            <p className="text-sm">This workflow is ready when you are.</p>
          </div>
        ) : (
          messages.map((message) => {
            const hasContent = Boolean(message.content && String(message.content).trim());
            const hasToolCall = Boolean(message.toolCall);
            if (!hasContent && !hasToolCall) {
              return null;
            }

            const isUser = message.sender === 'user';
            const isToolOnly = hasToolCall && !hasContent;
            const messageStyle = isToolOnly
              ? { color: 'var(--color-text-primary, #f8fafc)' }
              : {
                backgroundColor: isUser
                  ? 'var(--mozaiks-primary, #3b82f6)'
                  : 'var(--color-surface, var(--mozaiks-bg-secondary, #1e293b))',
                color: isUser
                  ? 'var(--color-text-on-accent, #ffffff)'
                  : 'var(--color-text-primary, #f8fafc)',
                border: isUser
                  ? 'none'
                  : '1px solid var(--color-border-subtle, var(--mozaiks-border, #334155))',
              };

            return (
              <div
                key={message.id}
                className={`${isToolOnly ? 'max-w-[90%]' : 'max-w-[80%] rounded-2xl px-4 py-3'} ${
                  isUser ? 'ml-auto' : 'mr-auto'
                }`}
                style={messageStyle}
              >
                {hasContent && (
                  <div className="text-sm whitespace-pre-wrap leading-relaxed">{message.content}</div>
                )}
                {hasToolCall && (
                  <ShellUIToolRenderer
                    event={message.toolCall}
                    className={hasContent ? 'mt-3' : ''}
                  />
                )}
                {hasContent && message.agentName && !isUser && (
                  <div className="mt-2 text-[11px] uppercase tracking-[0.18em]" style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}>
                    {message.agentName}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <form
        onSubmit={handleSendMessage}
        className="flex-shrink-0 p-4 border-t"
        style={{
          borderColor: 'var(--color-border-subtle, var(--mozaiks-border, #334155))',
          backgroundColor: 'var(--color-surface, var(--mozaiks-bg-secondary, #1e293b))',
        }}
      >
        <div className="flex gap-3">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyPress}
            placeholder="Type your message..."
            className="flex-1 min-h-[48px] max-h-40 px-4 py-3 rounded-2xl resize-none"
            style={{
              backgroundColor: 'var(--color-background, var(--mozaiks-bg, #0f172a))',
              color: 'var(--color-text-primary, var(--mozaiks-text, #f8fafc))',
              border: '1px solid var(--color-border-subtle, var(--mozaiks-border, #334155))',
            }}
          />
          <button
            type="submit"
            disabled={!inputValue.trim() || connectionStatus !== 'connected' || isSending}
            className="px-5 py-3 rounded-2xl font-semibold transition-opacity disabled:opacity-50"
            style={{
              backgroundColor: 'var(--mozaiks-primary, #3b82f6)',
              color: 'var(--color-text-on-accent, #ffffff)',
            }}
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}

export default function WorkflowChat(props) {
  const identity = JSON.stringify([props.appId, props.userId, props.workflow, props.initialContext, props.freshSession]);
  const initialContext = useMemo(() => props.initialContext, [identity]);
  return <WorkflowConversation key={identity} {...props} initialContext={initialContext} />;
}
