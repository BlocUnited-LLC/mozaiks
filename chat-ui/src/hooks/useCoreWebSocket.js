/**
 * useCoreWebSocket — Persistent WebSocket connection to the backend.
 *
 * Connects to ws://host:8000/ws/{userId}, auto-reconnects on drop,
 * dispatches incoming events to registered listeners, and exposes
 * connection state to the UI.
 *
 * Usage:
 *   const { connected, on, off, lastEvent } = useCoreWebSocket(userId);
 *   useEffect(() => {
 *     const unsub = on('notification_created', (data) => { ... });
 *     return unsub;
 *   }, [on]);
 *
 * @module @mozaiks/chat-ui/hooks/useCoreWebSocket
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import platform from '../platform/index.js';

const RECONNECT_BASE_DELAY = 1000;
const RECONNECT_MAX_DELAY = 30000;
const PING_INTERVAL = 25000;

function getCoreWsUrl(userId) {
  // Explicit VITE_CORE_URL override (browser/Vite builds)
  const explicitUrl =
    typeof import.meta !== 'undefined' ? (import.meta.env?.VITE_CORE_URL ?? '') : '';
  if (explicitUrl) {
    const base = explicitUrl.replace(/^http/, 'ws').replace(/\/+$/, '');
    return `${base}/ws/${encodeURIComponent(userId)}`;
  }

  // Derive from platform bridge — portable to React Native.
  // RN apps: configure getBaseUrls() to return the correct backend URL including port.
  const { wsUrl } = platform.getBaseUrls();
  const corePort =
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_PORT) || '8000';
  // Replace existing port (or append) with the core WebSocket port.
  const base = wsUrl.replace(/\/+$/, '').replace(/(:\d+)?$/, `:${corePort}`);
  return `${base}/ws/${encodeURIComponent(userId)}`;
}

export function useCoreWebSocket(userId, { enabled = true } = {}) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);

  const wsRef = useRef(null);
  const listenersRef = useRef(new Map());  // event_name -> Set<callback>
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef(null);
  const pingTimer = useRef(null);
  const unmountedRef = useRef(false);

  // -----------------------------------------------------------------------
  // Event listener registration
  // -----------------------------------------------------------------------
  const on = useCallback((eventName, callback) => {
    if (!listenersRef.current.has(eventName)) {
      listenersRef.current.set(eventName, new Set());
    }
    listenersRef.current.get(eventName).add(callback);

    // Return unsubscribe function
    return () => {
      const set = listenersRef.current.get(eventName);
      if (set) {
        set.delete(callback);
        if (set.size === 0) listenersRef.current.delete(eventName);
      }
    };
  }, []);

  const off = useCallback((eventName, callback) => {
    const set = listenersRef.current.get(eventName);
    if (set) {
      set.delete(callback);
      if (set.size === 0) listenersRef.current.delete(eventName);
    }
  }, []);

  // -----------------------------------------------------------------------
  // Dispatch incoming messages to listeners
  // -----------------------------------------------------------------------
  const dispatch = useCallback((message) => {
    setLastEvent(message);

    const eventName = message.event;
    if (!eventName) return;

    const listeners = listenersRef.current.get(eventName);
    if (listeners) {
      for (const cb of listeners) {
        try {
          cb(message.data || {});
        } catch (err) {
          console.error(`[useCoreWebSocket] listener error for '${eventName}':`, err);
        }
      }
    }

    // Also fire a wildcard '*' event for global listeners
    const wildcardListeners = listenersRef.current.get('*');
    if (wildcardListeners) {
      for (const cb of wildcardListeners) {
        try {
          cb(message);
        } catch (err) {
          console.error('[useCoreWebSocket] wildcard listener error:', err);
        }
      }
    }
  }, []);

  // -----------------------------------------------------------------------
  // Connection lifecycle
  // -----------------------------------------------------------------------
  const connect = useCallback(() => {
    if (!userId || !enabled || unmountedRef.current) return;

    const url = getCoreWsUrl(userId);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) { ws.close(); return; }
      setConnected(true);
      reconnectAttempt.current = 0;

      // Start keepalive pings
      pingTimer.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send('ping');
        }
      }, PING_INTERVAL);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'ack') return; // Ping response
        dispatch(msg);
      } catch {
        // Non-JSON message — ignore
      }
    };

    ws.onclose = () => {
      setConnected(false);
      clearInterval(pingTimer.current);
      if (!unmountedRef.current && enabled) {
        scheduleReconnect();
      }
    };

    ws.onerror = () => {
      // onclose will fire after onerror — reconnect handled there
    };
  }, [userId, enabled, dispatch]);

  const scheduleReconnect = useCallback(() => {
    if (unmountedRef.current) return;
    const delay = Math.min(
      RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempt.current),
      RECONNECT_MAX_DELAY
    );
    reconnectAttempt.current += 1;
    reconnectTimer.current = setTimeout(() => {
      if (!unmountedRef.current) connect();
    }, delay);
  }, [connect]);

  // -----------------------------------------------------------------------
  // Mount / unmount
  // -----------------------------------------------------------------------
  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      clearInterval(pingTimer.current);
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { connected, on, off, lastEvent };
}

export default useCoreWebSocket;
