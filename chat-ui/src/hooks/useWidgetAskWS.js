import { useEffect, useRef, useState, useCallback } from 'react';
import {
  getStoredActiveGeneralChatId,
  setStoredActiveGeneralChatId,
} from '../session/chatSessionStorage';

// Fallback: generate a stable UUID-style widget chat_id when no workflow
// session exists. Stored in localStorage so it survives refreshes and the
// session_router doesn't redirect it to a different session.
const WIDGET_CHAT_ID_KEY = 'mozaiks.widget_chat_id';

function getOrCreateFallbackChatId() {
  try {
    let id = localStorage.getItem(WIDGET_CHAT_ID_KEY);
    if (!id) {
      // Use a proper UUID so the session_router doesn't try to map it
      id = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem(WIDGET_CHAT_ID_KEY, id);
    }
    return id;
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }
}

/**
 * Normalise a raw WS envelope the same way ChatPage does:
 * promote fields from the nested data object to the top level so callers
 * can read data.content, data.agent, data.stream_id, data.full_content directly.
 */
function normalise(raw) {
  const d = { ...raw };
  try {
    if (d.data && typeof d.data === 'object') {
      const inner = d.data;
      if (!d.agent && !d.agent_name) d.agent = inner.agent || inner.agent_name || inner.sender || null;
      if (!d.content && inner.content) d.content = inner.content;
      if (d.stream_id === undefined) d.stream_id = inner.stream_id ?? null;
      if (d.full_content === undefined) d.full_content = inner.full_content ?? null;
      if (d.role === undefined) d.role = inner.role ?? null;
      if (d.general_chat_id === undefined) d.general_chat_id = inner.general_chat_id ?? null;
    }
  } catch (_) {}
  return d;
}

function fieldValue(data, key) {
  const value = data?.[key] ?? data?.data?.[key];
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

function isUserReplay(data) {
  const values = [
    fieldValue(data, 'role'),
    fieldValue(data, 'sender'),
    fieldValue(data, 'sender_role'),
    fieldValue(data, 'message_from'),
    fieldValue(data, 'agent'),
    fieldValue(data, 'agent_name'),
  ].filter(Boolean);
  return values.some((value) => value === 'user' || value === 'you');
}

/**
 * Manages a lightweight WebSocket connection for the floating widget in ask/general mode.
 *
 * Uses its own persistent chat_id (mozaiks.widget_chat_id in localStorage) so it never
 * conflicts with ChatPage's WS. Connects lazily on first widget open.
 *
 * Streaming: accumulates chat.stream_chunk events in a buffer; fires onAgentMessage
 * once with the complete text when chat.stream_end arrives. This keeps the widget
 * simple — no partial-text updates, just the finished message.
 *
 * @param {object}   opts
 * @param {object}   opts.api                     API adapter (WebSocketApiAdapter)
 * @param {string}   opts.appId                   App ID
 * @param {string}   opts.userId                  User ID
 * @param {string}   [opts.chatId]                Optional explicit carrier chat_id. The widget
 *                                                normally uses its own persisted carrier id so
 *                                                workflow sessions cannot leak into ask mode.
 * @param {string}   [opts.workflowName]           Workflow name for the WS URL
 * @param {string}   [opts.activeGeneralChatId]    General chat session to resume
 * @param {function} [opts.setActiveGeneralChatId]
 * @param {function} [opts.onAgentMessage]         Called with a finished agent message object
 * @param {boolean}  [opts.enabled=false]          Connect only when true (lazy on first open)
 */
export function useWidgetAskWS({
  api,
  appId,
  userId,
  chatId: chatIdProp,
  workflowName,
  activeGeneralChatId,
  setActiveGeneralChatId,
  onAgentMessage,
  enabled = false,
}) {
  const wsRef = useRef(null);
  const [status, setStatus] = useState('disconnected');
  const [isAgentTyping, setIsAgentTyping] = useState(false);
  const [generalModeReady, setGeneralModeReady] = useState(false);
  const enteredModeRef = useRef(false);
  // Resolve the widget carrier id once on mount. Do not fall back to the
  // globally active workflow chat id; ask mode must remain independent.
  const chatIdRef = useRef(chatIdProp || getOrCreateFallbackChatId());
  // Accumulate streaming chunks per stream_id (or agent-name fallback)
  const streamBufferRef = useRef({});

  const doEnterGeneralMode = useCallback((conn) => {
    if (!conn || enteredModeRef.current) return;
    const gid = activeGeneralChatId || getStoredActiveGeneralChatId();
    conn.send({
      type: 'chat.enter_general_mode',
      chat_id: chatIdRef.current,
      ...(gid ? { general_chat_id: gid } : {}),
    });
    enteredModeRef.current = true;
  }, [activeGeneralChatId]);

  useEffect(() => {
    if (!enabled || !api?.createWebSocketConnection || !appId || !userId) return;

    enteredModeRef.current = false;
    streamBufferRef.current = {};
    setStatus('connecting');
    setIsAgentTyping(false);
    setGeneralModeReady(false);

    const conn = api.createWebSocketConnection(
      appId,
      userId,
      {
        onOpen: () => {
          setStatus('connected');
          doEnterGeneralMode(conn);
        },

        onMessage: (raw) => {
          const data = normalise(raw);
          const type = typeof data.type === 'string' ? data.type : '';
          const evt = type.startsWith('chat.') ? type.slice(5) : type;

          if (evt === 'stream_chunk') {
            if (isUserReplay(data)) return;
            const chunk = data.content || '';
            if (!chunk) return;
            const key = data.stream_id || data.agent || 'agent';
            streamBufferRef.current[key] = (streamBufferRef.current[key] || '') + chunk;
            setIsAgentTyping(true);
            return;
          }

          if (evt === 'stream_end') {
            const key = data.stream_id || data.agent || 'agent';
            if (isUserReplay(data)) {
              delete streamBufferRef.current[key];
              setIsAgentTyping(Object.keys(streamBufferRef.current).length > 0);
              return;
            }
            const final = data.full_content || data.content || streamBufferRef.current[key] || '';
            delete streamBufferRef.current[key];
            setIsAgentTyping(Object.keys(streamBufferRef.current).length > 0);
            if (final) {
              onAgentMessage?.({
                id: `ws_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
                sender: 'agent',
                agentName: data.agent || data.agent_name || 'Assistant',
                content: final,
                timestamp: new Date().toISOString(),
              });
            }
            return;
          }

          // Fallback: plain chat.text (non-streamed or replay messages)
          if (evt === 'text' && !isUserReplay(data)) {
            const content = data.content || data.full_content || '';
            if (content) {
              onAgentMessage?.({
                id: `ws_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
                sender: 'agent',
                agentName: data.agent || data.agent_name || 'Assistant',
                content,
                timestamp: new Date().toISOString(),
              });
            }
            return;
          }

          if (evt === 'mode_changed') {
            const gid = data.general_chat_id || data.data?.general_chat_id;
            if (gid) {
              setActiveGeneralChatId?.(gid);
              setStoredActiveGeneralChatId(gid);
              setGeneralModeReady(true);
            }
          }
        },

        onError: () => {
          setStatus('error');
          setGeneralModeReady(false);
        },
        onClose: () => {
          setStatus('disconnected');
          setIsAgentTyping(false);
          setGeneralModeReady(false);
          enteredModeRef.current = false;
          streamBufferRef.current = {};
        },
      },
      workflowName || null,
      chatIdRef.current,
    );

    wsRef.current = conn;
    return () => {
      conn?.close();
      wsRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, api, appId, userId, workflowName]);

  // Re-enter general mode if the connection is up but mode wasn't entered yet
  useEffect(() => {
    if (status === 'connected' && !enteredModeRef.current && wsRef.current) {
      doEnterGeneralMode(wsRef.current);
    }
  }, [status, doEnterGeneralMode]);

  const send = useCallback((text) => {
    if (!wsRef.current || !generalModeReady) return false;
    const gid = activeGeneralChatId || getStoredActiveGeneralChatId();
    return wsRef.current.send({
      type: 'user.input.submit',
      chat_id: chatIdRef.current,
      text,
      context: {
        source: 'widget',
        conversation_mode: 'ask',
        ...(gid ? { general_chat_id: gid } : {}),
        app_id: appId,
        user_id: userId,
      },
    });
  }, [activeGeneralChatId, appId, generalModeReady, userId]);

  return { send, status, isAgentTyping, generalModeReady };
}
