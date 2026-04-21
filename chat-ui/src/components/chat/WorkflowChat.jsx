import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { WebSocketApiAdapter } from '../../adapters/api';
import config from '../../config';
import { useChatWebSocket } from '../../pages/hooks';

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
  return event?.agent || event?.agent_name || event?.sender || 'assistant';
}

function appendAssistantMessage(setMessages, event, onMessage) {
  const content = getWorkflowEventContent(event);
  if (!content.trim()) {
    return;
  }

  const newMessage = {
    id: event.id || `assistant-${Date.now()}`,
    sender: 'assistant',
    content,
    agentName: getWorkflowAgentName(event),
    timestamp: new Date().toISOString(),
  };

  setMessages((prev) => [...prev, newMessage]);
  onMessage?.(newMessage);
}

function appendStreamChunk(setMessages, event) {
  const content = getWorkflowEventContent(event);
  if (!content) {
    return;
  }

  const agentName = getWorkflowAgentName(event);

  setMessages((prev) => {
    const updated = [...prev];
    for (let i = updated.length - 1; i >= 0; i -= 1) {
      const message = updated[i];
      if (message.__streaming && message.agentName === agentName) {
        updated[i] = { ...message, content: `${message.content}${content}` };
        return updated;
      }
    }

    updated.push({
      id: `stream-${Date.now()}`,
      sender: 'assistant',
      content,
      agentName,
      timestamp: new Date().toISOString(),
      isStreaming: true,
      __streaming: true,
    });
    return updated;
  });
}

function finalizeStream(setMessages, event, onMessage) {
  const content = getWorkflowEventContent(event);
  const agentName = getWorkflowAgentName(event);

  setMessages((prev) => {
    const updated = [...prev];
    for (let i = updated.length - 1; i >= 0; i -= 1) {
      const message = updated[i];
      if (message.__streaming && message.agentName === agentName) {
        const finalized = {
          ...message,
          content: content || message.content,
          isStreaming: false,
        };
        delete finalized.__streaming;
        updated[i] = finalized;
        onMessage?.(finalized);
        return updated;
      }
    }

    if (!content.trim()) {
      return updated;
    }

    const newMessage = {
      id: `stream-end-${Date.now()}`,
      sender: 'assistant',
      content,
      agentName,
      timestamp: new Date().toISOString(),
      isStreaming: false,
    };
    updated.push(newMessage);
    onMessage?.(newMessage);
    return updated;
  });
}

function WorkflowChat({
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
}) {
  const [chatId, setChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);

  const resolvedAppId = appId || config.get('chat.defaultAppId') || 'default';
  const workflowApi = useMemo(
    () => new WebSocketApiAdapter(config.get ? config.get('api') : null),
    []
  );

  const handleRuntimeEvent = useCallback((rawEvent) => {
    const event = normalizeWorkflowEvent(rawEvent);
    if (!event) {
      return;
    }

    const eventType = getWorkflowEventType(event);
    switch (eventType) {
      case 'text':
      case 'print':
      case 'message':
      case 'agent_message':
        appendAssistantMessage(setMessages, event, onMessage);
        return;
      case 'stream_chunk':
        appendStreamChunk(setMessages, event);
        return;
      case 'stream_end':
        finalizeStream(setMessages, event, onMessage);
        return;
      case 'run_complete':
      case 'workflow_complete':
        onComplete?.(event);
        return;
      case 'error':
        setError(event.message || 'Workflow error');
        return;
      default:
        return;
    }
  }, [onComplete, onMessage]);

  const { connectionStatus } = useChatWebSocket({
    api: workflowApi,
    appId: resolvedAppId,
    userId,
    chatId,
    workflowName: workflow,
    workflowConfigLoaded: true,
    onMessage: handleRuntimeEvent,
  });

  useEffect(() => {
    let mounted = true;
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
          {},
          initialContext || null
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
      startWorkflow();
    }

    return () => {
      mounted = false;
    };
  }, [workflowApi, workflow, userId, resolvedAppId, initialContext]);

  const handleSendMessage = useCallback(async (e) => {
    e?.preventDefault();
    const trimmed = inputValue.trim();
    if (!trimmed || !chatId || connectionStatus !== 'connected' || isSending) {
      return;
    }

    const userMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsSending(true);

    const result = await workflowApi.sendMessageToWorkflow(
      trimmed,
      resolvedAppId,
      userId,
      workflow,
      chatId
    );

    if (result === false || result?.success === false) {
      setError(result?.error || 'Failed to send message');
    }

    setIsSending(false);
  }, [chatId, connectionStatus, inputValue, isSending, resolvedAppId, userId, workflowApi, workflow]);

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
          messages.map((message) => (
            <div
              key={message.id}
              className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                message.sender === 'user' ? 'ml-auto' : 'mr-auto'
              }`}
              style={{
                backgroundColor: message.sender === 'user'
                  ? 'var(--mozaiks-primary, #3b82f6)'
                  : 'var(--color-surface, var(--mozaiks-bg-secondary, #1e293b))',
                color: message.sender === 'user'
                  ? 'var(--color-text-on-accent, #ffffff)'
                  : 'var(--color-text-primary, #f8fafc)',
                border: message.sender === 'user'
                  ? 'none'
                  : '1px solid var(--color-border-subtle, var(--mozaiks-border, #334155))',
              }}
            >
              <div className="text-sm whitespace-pre-wrap leading-relaxed">{message.content}</div>
              {message.agentName && message.sender !== 'user' && (
                <div className="mt-2 text-[11px] uppercase tracking-[0.18em]" style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}>
                  {message.agentName}
                </div>
              )}
            </div>
          ))
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

export default WorkflowChat;
