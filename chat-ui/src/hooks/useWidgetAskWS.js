import { useEffect, useRef, useState, useCallback } from 'react';
import { mapGeneralMessage } from '../session/generalTranscript';
import {
  getStoredActiveGeneralChatId,
  setStoredActiveGeneralChatId,
} from '../session/chatSessionStorage';

// Stable carrier chat_id for the widget's own ask connection, stored in
// localStorage so it survives refreshes. Isolation from workflow sessions is
// guaranteed by the connect-time transport_purpose=ask_carrier declaration,
// not by the id shape.
const WIDGET_CHAT_ID_KEY = 'mozaiks.widget_chat_id';

function getOrCreateFallbackChatId(appId, userId) {
  const key = `${WIDGET_CHAT_ID_KEY}:${encodeURIComponent(appId)}:${encodeURIComponent(userId)}`;
  try {
    let id = localStorage.getItem(key);
    if (!id) {
      // Use a proper UUID so the session_router doesn't try to map it
      id = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem(key, id);
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
 * Ask-only widget transport. The server acknowledgement owns the selected
 * conversation; the existing transcript API restores it before queued sends.
 */
export function useWidgetAskWS({
  api, appId, userId, chatId: chatIdProp, workflowName,
  activeGeneralChatId, setActiveGeneralChatId,
  messages, setMessages, getPendingMessageIds, onConversationAcknowledged,
  enabled = false, pageContext = null, pagePath = null,
}) {
  const wsRef = useRef(null);
  const acknowledgedIdRef = useRef(null);
  const requestedIdRef = useRef(null);
  // Provisional scope is local to one pending request; never a wire/session ID.
  const pendingScopeRef = useRef(Symbol('pending Ask conversation'));
  const readyRef = useRef(false);
  const outstandingSendsRef = useRef(0);
  const newConversationRef = useRef(false);
  const retryRef = useRef(null);
  const requestRef = useRef(0);
  const identityRef = useRef(null);
  const latestRef = useRef(null);
  latestRef.current = { appId, userId, messages, setMessages, getPendingMessageIds, onConversationAcknowledged, activeGeneralChatId, setActiveGeneralChatId };
  const [status, setStatus] = useState('disconnected');
  const [isAgentTyping, setIsAgentTyping] = useState(false);
  const [generalModeReady, setGeneralModeReady] = useState(false);
  const [historyStatus, setHistoryStatus] = useState('idle');
  const [selectingNew, setSelectingNew] = useState(false);
  const [connectionVersion, setConnectionVersion] = useState(0);

  // An external conversation selection needs a new carrier connection because
  // enter_general_mode reuses a session already bound to an open connection.
  useEffect(() => {
    if (!wsRef.current || activeGeneralChatId === (acknowledgedIdRef.current || requestedIdRef.current)) return;
    acknowledgedIdRef.current = null;
    newConversationRef.current = false;
    pendingScopeRef.current = activeGeneralChatId || Symbol('pending Ask conversation');
    readyRef.current = false;
    requestRef.current += 1;
    setGeneralModeReady(false);
    latestRef.current.setMessages([]);
    setConnectionVersion(value => value + 1);
  }, [activeGeneralChatId]);

  useEffect(() => {
    const identity = JSON.stringify([appId, userId]);
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      acknowledgedIdRef.current = null;
      newConversationRef.current = false;
      pendingScopeRef.current = latestRef.current.activeGeneralChatId || Symbol('pending Ask conversation');
      latestRef.current.setMessages([]);
    }
    readyRef.current = false;
    setGeneralModeReady(false);
    setHistoryStatus('idle');
    if (!enabled || !api?.createWebSocketConnection || !appId || !userId) return undefined;

    let disposed = false;
    let connected = false;
    let entered = false;
    let awaitingEntry = false;
    const requestedSelection = latestRef.current.activeGeneralChatId;
    requestedIdRef.current = requestedSelection;
    const streams = {};
    const completedIds = new Set();
    const carrierId = chatIdProp || getOrCreateFallbackChatId(appId, userId);
    setSelectingNew(newConversationRef.current);
    setStatus('connecting');
    outstandingSendsRef.current = 0;
    setIsAgentTyping(false);

    const current = () => !disposed && identityRef.current === identity
      && latestRef.current.appId === appId && latestRef.current.userId === userId
      && (latestRef.current.activeGeneralChatId === requestedSelection
        || latestRef.current.activeGeneralChatId === acknowledgedIdRef.current);
    const invalidate = () => {
      requestRef.current += 1;
      readyRef.current = false;
      setGeneralModeReady(false);
    };
    const pendingIds = gid => new Set(latestRef.current.getPendingMessageIds?.(gid, pendingScopeRef.current) || []);

    const restore = async (gid) => {
      const request = ++requestRef.current;
      const baselineIds = new Set((latestRef.current.messages || []).map(message => message.id));
      const queuedIds = pendingIds(gid);
      readyRef.current = false;
      setGeneralModeReady(false);
      setHistoryStatus('loading');
      const valid = () => current() && connected && requestRef.current === request
        && acknowledgedIdRef.current === gid && !newConversationRef.current;
      try {
        const transcript = await api.fetchGeneralChatTranscript(appId, gid);
        if (!valid()) return;
        if (!transcript || transcript.found !== true || transcript.app_id !== appId
            || transcript.chat_id !== gid || transcript.user_id !== userId
            || !Array.isArray(transcript.messages)) throw new Error('History unavailable');
        const restored = transcript.messages.map(mapGeneralMessage).filter(Boolean);
        restored.filter(message => message.sender === 'agent').forEach(message => completedIds.add(message.id));
        latestRef.current.setMessages(previous => {
          if (!valid()) return previous;
          const ids = new Set(restored.map(message => message.id));
          const additions = previous.filter(message =>
            (!baselineIds.has(message.id) || queuedIds.has(message.id)) && !ids.has(message.id));
          return [...restored, ...additions];
        });
        readyRef.current = true;
        setGeneralModeReady(true);
        setHistoryStatus('ready');
      } catch (_) {
        if (!valid()) return;
        setHistoryStatus('error');
      }
    };
    retryRef.current = () => {
      const gid = acknowledgedIdRef.current;
      if (current() && connected && gid && !newConversationRef.current) void restore(gid);
    };

    const conn = api.createWebSocketConnection(appId, userId, {
      onOpen: () => {
        if (!current()) return;
        connected = true;
        setStatus('connected');
        if (entered) return;
        entered = true;
        if (newConversationRef.current) {
          conn.send({ type: 'chat.start_general_chat', chat_id: carrierId });
          return;
        }
        awaitingEntry = true;
        const gid = acknowledgedIdRef.current || latestRef.current.activeGeneralChatId || getStoredActiveGeneralChatId();
        conn.send({ type: 'chat.enter_general_mode', chat_id: carrierId, ...(gid ? { general_chat_id: gid } : {}) });
      },
      onMessage: raw => {
        if (!current() || !connected) return;
        const data = normalise(raw);
        const type = typeof data.type === 'string' ? data.type.replace(/^chat\./, '') : '';
        const gid = data.general_chat_id || data.metadata?.general_chat_id || data.data?.metadata?.general_chat_id;
        if (type === 'mode_changed' || type === 'general_session_created') {
          if (!gid || (type === 'mode_changed' && data.mode !== 'general' && data.data?.mode !== 'general')) return;
          const isNew = type === 'general_session_created';
          if (!isNew && !awaitingEntry) return;
          if ((newConversationRef.current && !isNew) || (isNew && !newConversationRef.current)) return;
          if (isNew && gid === acknowledgedIdRef.current) return;
          awaitingEntry = false;
          if (isNew || (acknowledgedIdRef.current && acknowledgedIdRef.current !== gid)) {
            const queuedIds = pendingIds(gid);
            latestRef.current.setMessages(previous => previous.filter(message => queuedIds.has(message.id)));
          }
          newConversationRef.current = false;
          if (isNew) completedIds.clear();
          setSelectingNew(false);
          acknowledgedIdRef.current = gid;
          latestRef.current.onConversationAcknowledged?.(gid, pendingScopeRef.current);
          pendingScopeRef.current = gid;
          latestRef.current.setActiveGeneralChatId?.(gid);
          setStoredActiveGeneralChatId(gid);
          void restore(gid);
          return;
        }
        // Ignore delayed events from a replaced conversation or a pending switch.
        if (newConversationRef.current || !acknowledgedIdRef.current
            || gid !== acknowledgedIdRef.current) return;
        if (!['stream_chunk', 'stream_end', 'text'].includes(type) || isUserReplay(data)) return;
        const key = data.stream_id || data.agent || 'agent';
        if (type === 'stream_chunk') {
          streams[key] = (streams[key] || '') + (data.content || '');
          setIsAgentTyping(true);
          return;
        }
        const content = data.full_content || data.content || streams[key] || '';
        delete streams[key];
        if (!content) return;
        const eventId = data.metadata?.general_message_id || data.data?.metadata?.general_message_id;
        if (eventId && completedIds.has(eventId)) return;
        if (eventId) completedIds.add(eventId);
        outstandingSendsRef.current = Math.max(0, outstandingSendsRef.current - 1);
        setIsAgentTyping(outstandingSendsRef.current > 0 || Object.keys(streams).length > 0);
        const message = {
          id: eventId || `ws_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          sender: 'agent', agentName: data.agent || data.agent_name || 'Assistant',
          content, timestamp: new Date().toISOString(),
        };
        latestRef.current.setMessages(previous => previous.some(item => item.id === message.id)
          ? previous : [...previous, message]);
      },
      onError: () => {
        if (!current()) return;
        invalidate();
        setStatus('error');
      },
      onClose: () => {
        if (!current()) return;
        connected = false;
        entered = false;
        awaitingEntry = false;
        invalidate();
        setStatus('disconnected');
        outstandingSendsRef.current = 0;
        setIsAgentTyping(false);
      },
    }, workflowName || null, carrierId, { transportPurpose: 'ask_carrier', suppressHistoryReplay: true });
    wsRef.current = { conn, carrierId, appId, userId };
    return () => {
      disposed = true;
      requestRef.current += 1;
      readyRef.current = false;
      retryRef.current = null;
      wsRef.current = null;
      conn?.close();
    };
  }, [enabled, api, appId, userId, workflowName, chatIdProp, connectionVersion]);

  const retryHistory = useCallback(() => retryRef.current?.(), []);
  const retryConnection = useCallback(() => setConnectionVersion(value => value + 1), []);
  const startNewConversation = useCallback(() => {
    if (!wsRef.current || status !== 'connected' || newConversationRef.current) return false;
    newConversationRef.current = true;
    const previousScope = pendingScopeRef.current;
    pendingScopeRef.current = Symbol('pending new Ask conversation');
    readyRef.current = false;
    requestRef.current += 1;
    setGeneralModeReady(false);
    outstandingSendsRef.current = 0;
    setIsAgentTyping(false);
    setSelectingNew(true);
    const sent = wsRef.current.conn.send({ type: 'chat.start_general_chat', chat_id: wsRef.current.carrierId });
    if (!sent) {
      newConversationRef.current = false;
      pendingScopeRef.current = previousScope;
      setSelectingNew(false);
      setHistoryStatus('error');
    }
    return sent;
  }, [status]);

  const send = useCallback(text => {
    if (!wsRef.current || !readyRef.current || !acknowledgedIdRef.current) return false;
    if (wsRef.current.appId !== appId || wsRef.current.userId !== userId) return false;
    const sent = wsRef.current.conn.send({
      type: 'user.input.submit', chat_id: wsRef.current.carrierId, text,
      context: {
        source: 'widget', conversation_mode: 'ask',
        general_chat_id: acknowledgedIdRef.current,
        ...(pageContext ? { page_context: pageContext } : {}),
        ...(pagePath ? { page_path: pagePath } : {}),
        app_id: appId, user_id: userId,
      },
    });
    if (sent) {
      outstandingSendsRef.current += 1;
      setIsAgentTyping(true);
    }
    return sent;
  }, [appId, pageContext, pagePath, userId]);

  const getQueueScope = useCallback(() => newConversationRef.current
    ? pendingScopeRef.current : acknowledgedIdRef.current || pendingScopeRef.current, []);
  return { send, status, isAgentTyping, generalModeReady, historyStatus, retryHistory, retryConnection, startNewConversation, selectingNew, getQueueScope };
}
