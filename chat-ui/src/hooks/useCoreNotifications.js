/**
 * useCoreNotifications — Real-time notification state from mozaikscore
 *
 * **WebSocket-first**: When a `wsConnection` (from useCoreWebSocket) is
 * provided and connected, the hook subscribes to push events and updates
 * the count instantly.  Falls back to HTTP polling (default 30 s) when
 * WebSocket is unavailable.
 *
 * Exposes:
 *   - count: number of unread notifications
 *   - refresh(): force a re-fetch
 *   - loading: true until first data arrives
 *   - source: 'ws' | 'poll' — how the count is currently being updated
 *
 * @module @mozaiks/chat-ui/hooks/useCoreNotifications
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchNotificationCount } from '../coreBridge';

const DEFAULT_POLL_MS = 30_000; // 30 seconds

/**
 * @param {Object} [options]
 * @param {number}  [options.pollInterval] — ms between polls (default 30 000)
 * @param {boolean} [options.enabled]      — set false to disable entirely
 * @param {Object}  [options.wsConnection] — return value of useCoreWebSocket()
 * @returns {{ count: number, refresh: () => void, loading: boolean, source: string }}
 */
export function useCoreNotifications(options = {}) {
  const { pollInterval = DEFAULT_POLL_MS, enabled = true, wsConnection } = options;
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState('poll');
  const mountedRef = useRef(true);

  const doFetch = useCallback(async () => {
    try {
      const res = await fetchNotificationCount();
      if (mountedRef.current) {
        setCount(res?.count ?? res?.unread_count ?? 0);
        setLoading(false);
      }
    } catch {
      // Non-fatal — core may not be running
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // ── WebSocket event listeners ──────────────────────────────────────────
  useEffect(() => {
    if (!enabled || !wsConnection?.on || !wsConnection.connected) return;

    setSource('ws');

    const unsubs = [
      wsConnection.on('notification_created', () => {
        setCount((c) => c + 1);
      }),
      wsConnection.on('notification_read', () => {
        setCount((c) => Math.max(0, c - 1));
      }),
      wsConnection.on('all_notifications_read', () => {
        setCount(0);
      }),
      wsConnection.on('notification_deleted', () => {
        // Re-fetch to get accurate count after deletion
        doFetch();
      }),
    ];

    return () => {
      unsubs.forEach((fn) => fn && fn());
      if (mountedRef.current) setSource('poll');
    };
  }, [enabled, wsConnection?.on, wsConnection?.connected, doFetch]);

  // ── HTTP polling fallback ──────────────────────────────────────────────
  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) {
      setLoading(false);
      return undefined;
    }

    // Always do initial fetch for accurate count
    doFetch();

    // Only poll if WebSocket is NOT connected
    if (wsConnection?.connected) return undefined;

    const timer = setInterval(doFetch, pollInterval);

    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [doFetch, pollInterval, enabled, wsConnection?.connected]);

  return { count, refresh: doFetch, loading, source };
}

export default useCoreNotifications;
