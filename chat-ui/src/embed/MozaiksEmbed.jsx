import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { WebSocketApiAdapter } from '../adapters/api';
import BrowserConsoleBridge from '../components/debug/BrowserConsoleBridge.jsx';

function normalizeRuntimeBaseUrl(runtimeUrl) {
  if (!runtimeUrl || typeof runtimeUrl !== 'string') {
    return null;
  }
  return runtimeUrl.endsWith('/') ? runtimeUrl.slice(0, -1) : runtimeUrl;
}

function deriveWsBaseUrl(runtimeUrl) {
  const normalized = normalizeRuntimeBaseUrl(runtimeUrl);
  if (!normalized) {
    return null;
  }
  if (normalized.startsWith('ws://') || normalized.startsWith('wss://')) {
    return normalized;
  }
  return normalized.replace(/^http/i, 'ws');
}

function getOrCreateGuestUserId(appId) {
  if (typeof window === 'undefined') {
    return `guest-${appId || 'mozaiks'}`;
  }

  const storageKey = `mozaiks.embed.user.${appId || 'default'}`;
  try {
    const existing = window.localStorage.getItem(storageKey);
    if (existing) {
      return existing;
    }
    const created = `guest-${crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
    window.localStorage.setItem(storageKey, created);
    return created;
  } catch {
    return `guest-${appId || 'mozaiks'}`;
  }
}

function normalizeRuntimeEvent(event) {
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
        // Ignore nested envelopes we cannot parse cleanly.
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

function getEventType(event) {
  const rawType = String(event?.type || '');
  return rawType.startsWith('chat.') ? rawType.slice(5) : rawType;
}

function getEventContent(event) {
  return String(event?.content || event?.message || '');
}

function getEventAgentName(event) {
  return event?.agent || event?.agent_name || event?.sender || 'assistant';
}

function chooseWorkflowName(workflowsPayload) {
  if (!workflowsPayload || typeof workflowsPayload !== 'object') {
    return null;
  }

  const entries = Array.isArray(workflowsPayload)
    ? workflowsPayload
    : Object.entries(workflowsPayload).map(([workflowName, config]) => ({
        workflow_name: workflowName,
        ...(config || {}),
      }));

  const canonical = entries.filter((entry) => entry && typeof entry.workflow_name === 'string');
  if (!canonical.length) {
    return null;
  }

  return canonical.find((entry) => entry.entry_point === true)?.workflow_name
    || canonical[0].workflow_name;
}

function applyThemeToContainer(container, theme) {
  if (!container || !theme) {
    return;
  }

  const colors = theme.colors || {};
  const fonts = theme.fonts || {};
  const shadows = theme.shadows || {};
  const themeV2 = theme.theme || {};

  const colorMap = {
    '--color-primary': colors.primary?.main,
    '--color-primary-light': colors.primary?.light,
    '--color-primary-dark': colors.primary?.dark,
    '--color-secondary': colors.secondary?.main,
    '--color-secondary-light': colors.secondary?.light,
    '--color-secondary-dark': colors.secondary?.dark,
    '--color-accent': colors.accent?.main,
    '--color-accent-light': colors.accent?.light,
    '--color-accent-dark': colors.accent?.dark,
    '--color-success': colors.success?.main,
    '--color-warning': colors.warning?.main,
    '--color-error': colors.error?.main,
    '--color-background': colors.background?.base,
    '--color-surface': colors.background?.surface,
    '--color-surface-alt': colors.background?.elevated,
    '--color-surface-overlay': colors.background?.overlay,
    '--color-border-subtle': colors.border?.subtle,
    '--color-border-strong': colors.border?.strong,
    '--color-border-accent': colors.border?.accent,
    '--color-text-primary': colors.text?.primary,
    '--color-text-secondary': colors.text?.secondary,
    '--color-text-muted': colors.text?.muted,
    '--color-text-on-accent': colors.text?.onAccent,
  };

  Object.entries(colorMap).forEach(([prop, value]) => {
    if (value) {
      container.style.setProperty(prop, value);
    }
  });

  if (fonts.body?.family) {
    container.style.setProperty('--mozaiks-font', `${fonts.body.family}, ${fonts.body.fallbacks || 'system-ui'}`);
  }
  if (fonts.heading?.family) {
    container.style.setProperty('--mz-font-heading', `${fonts.heading.family}, ${fonts.heading.fallbacks || 'system-ui'}`);
  }

  Object.entries(shadows).forEach(([key, value]) => {
    if (value) {
      container.style.setProperty(`--shadow-${key}`, value);
    }
  });

  Object.values(fonts).forEach((slot) => {
    if (slot?.googleFont && typeof document !== 'undefined') {
      const existing = document.querySelector(`link[href="${slot.googleFont}"]`);
      if (!existing) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = slot.googleFont;
        document.head.appendChild(link);
      }
    }
  });

  if (themeV2.appearance === 'dark') {
    container.classList.add('dark');
  } else {
    container.classList.remove('dark');
  }
}

function appendAssistantMessage(setMessages, event) {
  const content = getEventContent(event);
  if (!content.trim()) {
    return;
  }

  const newMessage = {
    id: event.id || `assistant-${Date.now()}`,
    role: 'assistant',
    content,
    agentName: getEventAgentName(event),
    timestamp: Date.now(),
  };

  setMessages((prev) => [...prev, newMessage]);
}

function appendStreamChunk(setMessages, event) {
  const content = getEventContent(event);
  if (!content) {
    return;
  }

  const agentName = getEventAgentName(event);
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
      role: 'assistant',
      content,
      agentName,
      timestamp: Date.now(),
      __streaming: true,
    });
    return updated;
  });
}

function finalizeStream(setMessages, event) {
  const content = getEventContent(event);
  const agentName = getEventAgentName(event);

  setMessages((prev) => {
    const updated = [...prev];
    for (let i = updated.length - 1; i >= 0; i -= 1) {
      const message = updated[i];
      if (message.__streaming && message.agentName === agentName) {
        const finalized = { ...message, content: content || message.content };
        delete finalized.__streaming;
        updated[i] = finalized;
        return updated;
      }
    }

    if (!content.trim()) {
      return updated;
    }

    updated.push({
      id: `stream-end-${Date.now()}`,
      role: 'assistant',
      content,
      agentName,
      timestamp: Date.now(),
    });
    return updated;
  });
}

function EmbedChatPanel({
  messages,
  input,
  setInput,
  sendMessage,
  isTyping,
  connected,
  theme,
  error,
  workflowName,
}) {
  const messagesEndRef = useRef(null);
  const colors = theme?.colors || {};
  const fonts = theme?.fonts || {};

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              textAlign: 'center',
              padding: '40px 20px',
              color: colors.text?.muted || '#64748b',
              fontFamily: fonts.body?.family || 'system-ui',
              fontSize: '14px',
            }}
          >
            {error
              ? error
              : connected
                ? `Ready for ${workflowName || 'your workflow'}`
                : 'Connecting to Mozaiks...'}
          </div>
        )}
        {messages.map((message) => (
          <div
            key={message.id}
            style={{
              alignSelf: message.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '80%',
              padding: '10px 14px',
              borderRadius: '12px',
              fontSize: '14px',
              lineHeight: '1.5',
              fontFamily: fonts.body?.family || 'system-ui',
              ...(message.role === 'user'
                ? {
                    backgroundColor: colors.primary?.main || '#06b6d4',
                    color: colors.text?.onAccent || '#ffffff',
                  }
                : {
                    backgroundColor: colors.background?.surface || '#0f1724',
                    color: colors.text?.primary || '#e6eef8',
                    border: `1px solid ${colors.border?.subtle || '#1e293b'}`,
                  }),
            }}
          >
            {message.content}
          </div>
        ))}
        {isTyping && (
          <div
            style={{
              alignSelf: 'flex-start',
              padding: '10px 14px',
              borderRadius: '12px',
              backgroundColor: colors.background?.surface || '#0f1724',
              border: `1px solid ${colors.border?.subtle || '#1e293b'}`,
              color: colors.text?.muted || '#64748b',
              fontSize: '14px',
              fontFamily: fonts.body?.family || 'system-ui',
            }}
          >
            <span className="mozaiks-typing-dots">...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div
        style={{
          padding: '12px 16px',
          borderTop: `1px solid ${colors.border?.subtle || '#1e293b'}`,
          backgroundColor: colors.background?.surface || '#0f1724',
          display: 'flex',
          gap: '8px',
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={connected ? 'Type a message...' : 'Waiting for connection...'}
          style={{
            flex: 1,
            padding: '10px 14px',
            borderRadius: '8px',
            border: `1px solid ${colors.border?.subtle || '#1e293b'}`,
            backgroundColor: colors.background?.base || '#0b1220',
            color: colors.text?.primary || '#e6eef8',
            fontFamily: fonts.body?.family || 'system-ui',
            fontSize: '14px',
            outline: 'none',
          }}
        />
        <button
          onClick={sendMessage}
          disabled={!input.trim() || !connected}
          style={{
            padding: '10px 16px',
            borderRadius: '8px',
            border: 'none',
            cursor: input.trim() && connected ? 'pointer' : 'default',
            backgroundColor: input.trim() && connected
              ? (colors.primary?.main || '#06b6d4')
              : (colors.border?.subtle || '#1e293b'),
            color: colors.text?.onAccent || '#ffffff',
            fontFamily: fonts.body?.family || 'system-ui',
            fontSize: '14px',
            fontWeight: 600,
            transition: 'background-color 0.2s',
          }}
          aria-label="Send message"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </div>
  );
}

const MozaiksEmbed = ({
  appId,
  runtimeUrl,
  theme = null,
  themeUrl = null,
  workflowName = null,
  userId = null,
  authToken = null,
  initialContext = null,
  triggerMeta = null,
  position = 'bottom-right',
  mode = 'floating',
  width = '400px',
  height = '600px',
  defaultOpen = false,
  onReady = null,
  onMessage = null,
  onError = null,
  className = '',
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [resolvedTheme, setResolvedTheme] = useState(theme);
  const [resolvedWorkflowName, setResolvedWorkflowName] = useState(workflowName);
  const [chatId, setChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState(null);
  const [isTyping, setIsTyping] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState('disconnected');

  const containerRef = useRef(null);
  const connectionRef = useRef(null);
  const resolvingWorkflowRef = useRef(false);
  const startingChatRef = useRef(false);

  const runtimeBaseUrl = useMemo(() => normalizeRuntimeBaseUrl(runtimeUrl), [runtimeUrl]);
  const wsBaseUrl = useMemo(() => deriveWsBaseUrl(runtimeUrl), [runtimeUrl]);
  const effectiveUserId = useMemo(
    () => userId || getOrCreateGuestUserId(appId),
    [userId, appId]
  );
  const surfaceOpen = mode === 'inline' ? true : isOpen;

  const apiAdapter = useMemo(() => {
    if (!runtimeBaseUrl || !wsBaseUrl) {
      return null;
    }
    return new WebSocketApiAdapter({
      baseUrl: runtimeBaseUrl,
      wsUrl: wsBaseUrl,
      getAccessToken: () => authToken || null,
    });
  }, [authToken, runtimeBaseUrl, wsBaseUrl]);

  useEffect(() => {
    setResolvedWorkflowName(workflowName);
  }, [workflowName]);

  useEffect(() => {
    setChatId(null);
    setMessages([]);
    setError(null);
    setIsTyping(false);
  }, [appId, effectiveUserId, workflowName]);

  useEffect(() => {
    if (theme) {
      setResolvedTheme(theme);
      return;
    }
    if (!themeUrl) {
      return;
    }

    let cancelled = false;
    fetch(themeUrl)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Theme fetch failed: ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        if (!cancelled) {
          setResolvedTheme(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          console.warn('⚠️ [MozaiksEmbed] Failed to load theme:', err.message);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [theme, themeUrl]);

  useEffect(() => {
    if (containerRef.current && resolvedTheme) {
      applyThemeToContainer(containerRef.current, resolvedTheme);
    }
  }, [resolvedTheme]);

  useEffect(() => {
    if (!surfaceOpen || !apiAdapter || resolvedWorkflowName || resolvingWorkflowRef.current) {
      return;
    }

    resolvingWorkflowRef.current = true;
    let cancelled = false;

    const resolveWorkflow = async () => {
      try {
        const workflows = await apiAdapter.get('/api/workflows');
        const selectedWorkflow = chooseWorkflowName(workflows);
        if (!selectedWorkflow) {
          throw new Error('No workflows available for embed surface');
        }
        if (!cancelled) {
          setResolvedWorkflowName(selectedWorkflow);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          onError?.(err);
        }
      } finally {
        resolvingWorkflowRef.current = false;
      }
    };

    resolveWorkflow();
    return () => {
      cancelled = true;
    };
  }, [apiAdapter, onError, resolvedWorkflowName, surfaceOpen]);

  useEffect(() => {
    if (
      !surfaceOpen
      || !apiAdapter
      || !resolvedWorkflowName
      || chatId
      || startingChatRef.current
    ) {
      return;
    }

    startingChatRef.current = true;
    let cancelled = false;

    const startChat = async () => {
      try {
        setError(null);
        const result = await apiAdapter.startChat(
          appId,
          resolvedWorkflowName,
          effectiveUserId,
          {},
          initialContext || null,
          triggerMeta || null
        );

        if (cancelled) {
          return;
        }

        if (result?.chat_id) {
          setChatId(result.chat_id);
          if (result.workflow_name) {
            setResolvedWorkflowName(result.workflow_name);
          }
          return;
        }

        const detail = typeof result?.detail === 'string'
          ? result.detail
          : result?.detail?.message || result?.detail?.error;
        const message = detail || result?.error || 'Failed to start embedded workflow session';
        setError(message);
        onError?.(new Error(message));
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          onError?.(err);
        }
      } finally {
        startingChatRef.current = false;
      }
    };

    startChat();
    return () => {
      cancelled = true;
    };
  }, [
    apiAdapter,
    appId,
    chatId,
    effectiveUserId,
    initialContext,
    onError,
    resolvedWorkflowName,
    surfaceOpen,
    triggerMeta,
  ]);

  useEffect(() => {
    if (!surfaceOpen || !apiAdapter || !chatId || !resolvedWorkflowName) {
      return undefined;
    }

    setConnectionStatus('connecting');
    const connection = apiAdapter.createWebSocketConnection(
      appId,
      effectiveUserId,
      {
        onOpen: () => {
          setConnectionStatus('connected');
          onReady?.({ appId, chatId, workflowName: resolvedWorkflowName, userId: effectiveUserId });
        },
        onMessage: (rawEvent) => {
          const event = normalizeRuntimeEvent(rawEvent);
          if (!event) {
            return;
          }

          onMessage?.(event);
          const eventType = getEventType(event);
          switch (eventType) {
            case 'text':
            case 'print':
            case 'message':
            case 'agent_message':
              setIsTyping(false);
              appendAssistantMessage(setMessages, event);
              return;
            case 'stream_chunk':
              setIsTyping(true);
              appendStreamChunk(setMessages, event);
              return;
            case 'stream_end':
              setIsTyping(false);
              finalizeStream(setMessages, event);
              return;
            case 'run_complete':
            case 'workflow_complete':
              setIsTyping(false);
              if (
                event?.status === 1 ||
                String(event?.status ?? '').trim().toLowerCase() === '1' ||
                ['completed', 'complete', 'success', 'succeeded', 'done', 'ok'].includes(
                  String(event?.status ?? '').trim().toLowerCase()
                )
              ) {
                onComplete?.(event);
              }
              return;
            case 'error': {
              const message = event.message || 'Workflow error';
              setIsTyping(false);
              setError(message);
              onError?.(new Error(message));
              return;
            }
            default:
              return;
          }
        },
        onClose: () => {
          setConnectionStatus('disconnected');
          setIsTyping(false);
          connectionRef.current = null;
        },
        onError: (wsError) => {
          setConnectionStatus('error');
          setIsTyping(false);
          onError?.(wsError instanceof Error ? wsError : new Error('Embed websocket error'));
        },
      },
      resolvedWorkflowName,
      chatId
    );

    if (!connection) {
      setConnectionStatus('error');
      return undefined;
    }

    connectionRef.current = connection;

    return () => {
      if (connectionRef.current === connection) {
        connection.close();
        connectionRef.current = null;
      }
      setConnectionStatus('disconnected');
    };
  }, [
    apiAdapter,
    appId,
    chatId,
    effectiveUserId,
    onError,
    onMessage,
    onReady,
    resolvedWorkflowName,
    surfaceOpen,
  ]);

  const sendMessage = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || !apiAdapter || !chatId || !resolvedWorkflowName || connectionStatus !== 'connected') {
      return;
    }

    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: 'user',
        content: trimmed,
        timestamp: Date.now(),
      },
    ]);
    setInput('');
    setIsTyping(true);

    const result = await apiAdapter.sendMessageToWorkflow(
      trimmed,
      appId,
      effectiveUserId,
      resolvedWorkflowName,
      chatId
    );

    if (result === false || result?.success === false) {
      const message = result?.error || 'Failed to send embedded workflow message';
      setIsTyping(false);
      setError(message);
      onError?.(new Error(message));
    }
  }, [apiAdapter, appId, chatId, connectionStatus, effectiveUserId, input, onError, resolvedWorkflowName]);

  const toggleOpen = useCallback(() => {
    setIsOpen((prev) => !prev);
  }, []);

  const positionStyles = {
    'bottom-right': { bottom: '20px', right: '20px' },
    'bottom-left': { bottom: '20px', left: '20px' },
    'top-right': { top: '20px', right: '20px' },
    'top-left': { top: '20px', left: '20px' },
  };

  const containerPosition = positionStyles[position] || positionStyles['bottom-right'];
  const connected = connectionStatus === 'connected';
  const brandName = resolvedTheme?.identity?.app_name || resolvedTheme?.identity?.name || 'Mozaiks AI';
  const consoleBridgeMetadata = {
    app_id: appId,
    workflow_name: resolvedWorkflowName,
    surface: mode === 'inline' ? 'embed_inline' : `embed_${mode}`,
  };

  if (mode === 'inline') {
    return (
      <div
        ref={containerRef}
        className={`mozaiks-embed mozaiks-embed--inline ${className}`}
        style={{ width, height }}
        data-app-id={appId}
      >
        <BrowserConsoleBridge
          endpointUrl={runtimeBaseUrl}
          metadata={consoleBridgeMetadata}
        />
        <EmbedChatPanel
          messages={messages}
          input={input}
          setInput={setInput}
          sendMessage={sendMessage}
          isTyping={isTyping}
          connected={connected}
          theme={resolvedTheme}
          error={error}
          workflowName={resolvedWorkflowName}
        />
      </div>
    );
  }

  return (
    <>
      {!isOpen && (
        <button
          onClick={toggleOpen}
          className="mozaiks-embed-trigger"
          style={{
            ...containerPosition,
            position: 'fixed',
            zIndex: 9998,
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: resolvedTheme?.colors?.primary?.main || '#06b6d4',
            color: resolvedTheme?.colors?.text?.onAccent || '#ffffff',
            boxShadow: resolvedTheme?.shadows?.primary || '0 4px 12px rgba(0,0,0,0.3)',
            transition: 'transform 0.2s, box-shadow 0.2s',
          }}
          aria-label="Open Mozaiks AI assistant"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
      )}

      {isOpen && (
        <div
          ref={containerRef}
          className={`mozaiks-embed mozaiks-embed--${mode} ${className}`}
          style={{
            ...containerPosition,
            position: 'fixed',
            zIndex: 9999,
            width: mode === 'sidebar' ? '380px' : width,
            height: mode === 'sidebar' ? '100vh' : height,
            ...(mode === 'sidebar' ? { top: 0, bottom: 0, right: 0 } : {}),
            borderRadius: mode === 'sidebar' ? 0 : '12px',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            backgroundColor: resolvedTheme?.colors?.background?.base || '#0b1220',
            border: `1px solid ${resolvedTheme?.colors?.border?.subtle || '#1e293b'}`,
            boxShadow: resolvedTheme?.shadows?.elevated || '0 24px 60px rgba(0,0,0,0.55)',
          }}
          data-app-id={appId}
        >
          <BrowserConsoleBridge
            endpointUrl={runtimeBaseUrl}
            metadata={consoleBridgeMetadata}
          />
          <div
            style={{
              padding: '12px 16px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderBottom: `1px solid ${resolvedTheme?.colors?.border?.subtle || '#1e293b'}`,
              backgroundColor: resolvedTheme?.colors?.background?.surface || '#0f1724',
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              <span
                style={{
                  color: resolvedTheme?.colors?.text?.primary || '#e6eef8',
                  fontFamily: resolvedTheme?.fonts?.heading?.family || 'system-ui',
                  fontWeight: 600,
                  fontSize: '14px',
                  letterSpacing: '0.05em',
                }}
              >
                {brandName}
              </span>
              <span
                style={{
                  color: connected
                    ? (resolvedTheme?.colors?.success?.main || '#22c55e')
                    : (resolvedTheme?.colors?.text?.muted || '#94a3b8'),
                  fontSize: '11px',
                  fontFamily: resolvedTheme?.fonts?.body?.family || 'system-ui',
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                }}
              >
                {resolvedWorkflowName || 'Resolving workflow'}
              </span>
            </div>
            <button
              onClick={toggleOpen}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: resolvedTheme?.colors?.text?.muted || '#64748b',
                padding: '4px',
              }}
              aria-label="Close chat"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <EmbedChatPanel
            messages={messages}
            input={input}
            setInput={setInput}
            sendMessage={sendMessage}
            isTyping={isTyping}
            connected={connected}
            theme={resolvedTheme}
            error={error}
            workflowName={resolvedWorkflowName}
          />
        </div>
      )}
    </>
  );
};

export { applyThemeToContainer, MozaiksEmbed };
