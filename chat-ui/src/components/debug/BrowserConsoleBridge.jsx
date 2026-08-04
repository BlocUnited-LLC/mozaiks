import { useEffect, useMemo } from 'react';

const GLOBAL_KEY = '__mozaiksBrowserConsoleBridgeState__';
const DEFAULT_BATCH_SIZE = 25;
const DEFAULT_FLUSH_INTERVAL_MS = 500;
const MAX_SAFE_STRING_LENGTH = 4000;

function getGlobalState() {
  if (typeof window === 'undefined') {
    return null;
  }

  if (!window[GLOBAL_KEY]) {
    window[GLOBAL_KEY] = {
      refCount: 0,
      patched: false,
      enabled: false,
      endpointUrl: null,
      metadata: {},
      buffer: [],
      flushTimer: null,
      originalConsole: null,
      originalOnError: null,
      originalOnUnhandledRejection: null,
    };
  }

  return window[GLOBAL_KEY];
}

function normalizeEndpointUrl(endpointUrl) {
  if (typeof endpointUrl !== 'string' || !endpointUrl.trim()) {
    return null;
  }

  const trimmed = endpointUrl.trim().replace(/\/+$/, '');
  return `${trimmed}/api/debug/client-console`;
}

function safeString(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === 'string') {
    return value.length > MAX_SAFE_STRING_LENGTH
      ? `${value.slice(0, MAX_SAFE_STRING_LENGTH)}…[truncated]`
      : value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'bigint') {
    return value.toString();
  }
  if (value instanceof Error) {
    return {
      name: value.name,
      message: value.message,
      stack: value.stack || null,
    };
  }
  if (value instanceof Date) {
    return value.toISOString();
  }
  if (typeof value === 'function') {
    return '[Function]';
  }
  if (typeof value === 'symbol') {
    return value.toString();
  }
  return value;
}

function safeSerialize(value, depth = 2, seen = new WeakSet()) {
  if (value === null || value === undefined) {
    return value;
  }
  if (typeof value === 'string') {
    return safeString(value);
  }
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return safeString(value);
  }
  if (typeof value === 'function' || typeof value === 'symbol') {
    return safeString(value);
  }
  if (value instanceof Error) {
    return safeString(value);
  }
  if (Array.isArray(value)) {
    if (depth <= 0) {
      return '[Array]';
    }
    return value.slice(0, 20).map((item) => safeSerialize(item, depth - 1, seen));
  }
  if (typeof value === 'object') {
    if (seen.has(value)) {
      return '[Circular]';
    }
    if (depth <= 0) {
      return '[Object]';
    }
    seen.add(value);
    const out = {};
    for (const [key, item] of Object.entries(value)) {
      if (key === 'stack' && typeof item === 'string') {
        out[key] = safeString(item);
      } else {
        out[key] = safeSerialize(item, depth - 1, seen);
      }
    }
    seen.delete(value);
    return out;
  }
  return safeString(value);
}

function captureWindowMetadata() {
  if (typeof window === 'undefined') {
    return {};
  }

  const url = new URL(window.location.href);
  return {
    app_id: url.searchParams.get('app_id') || url.searchParams.get('appId') || null,
    chat_id: url.searchParams.get('chat_id') || url.searchParams.get('chatId') || null,
    workflow_name: url.searchParams.get('workflow') || url.searchParams.get('workflow_name') || null,
    conversation_mode: url.searchParams.get('mode') || url.searchParams.get('conversation_mode') || null,
    pathname: url.pathname,
    href: url.href,
    origin: url.origin,
    title: typeof document !== 'undefined' ? document.title || null : null,
  };
}

function createRecord(level, method, args, metadata, category = 'console') {
  const timestamp = new Date().toISOString();
  const safeArgs = Array.isArray(args) ? args.map((arg) => safeSerialize(arg)) : [];
  const message = safeArgs
    .map((arg) => {
      if (typeof arg === 'string') return arg;
      try {
        return JSON.stringify(arg);
      } catch {
        return String(arg);
      }
    })
    .join(' ');

  return {
    category,
    level,
    method,
    message,
    args: safeArgs,
    timestamp,
    ...metadata,
  };
}

function scheduleFlush(state, reason = 'timer') {
  if (!state || !state.enabled || !state.endpointUrl) {
    return;
  }

  if (state.flushTimer) {
    return;
  }

  state.flushTimer = window.setTimeout(() => {
    state.flushTimer = null;
    void flushBuffer(state, reason);
  }, DEFAULT_FLUSH_INTERVAL_MS);
}

async function postPayload(state, payload) {
  const json = JSON.stringify(payload);
  const blob = new Blob([json], { type: 'application/json' });

  if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
    try {
      const sent = navigator.sendBeacon(state.endpointUrl, blob);
      if (sent) {
        return true;
      }
    } catch {
      // Fall through to fetch.
    }
  }

  const response = await fetch(state.endpointUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: json,
    keepalive: true,
    credentials: 'include',
  });
  return response.ok;
}

async function flushBuffer(state, reason = 'manual') {
  if (!state || !state.enabled || !state.endpointUrl || !state.buffer.length) {
    return;
  }

  const entries = state.buffer.splice(0, state.buffer.length);
  const payload = {
    source: 'browser_console',
    reason,
    metadata: {
      ...captureWindowMetadata(),
      ...state.metadata,
    },
    entries,
    sent_at: new Date().toISOString(),
  };

  try {
    const ok = await postPayload(state, payload);
    if (!ok && entries.length) {
      state.buffer.unshift(...entries.slice(0, DEFAULT_BATCH_SIZE));
    }
  } catch {
    if (entries.length) {
      state.buffer.unshift(...entries.slice(0, DEFAULT_BATCH_SIZE));
    }
  }
}

function patchConsole(state) {
  if (!state || state.patched || !state.enabled || !state.endpointUrl || typeof window === 'undefined') {
    return;
  }

  const originalConsole = { ...window.console };
  state.originalConsole = originalConsole;

  const consoleMethods = ['log', 'info', 'warn', 'error', 'debug', 'trace'];
  consoleMethods.forEach((method) => {
    const original = typeof originalConsole[method] === 'function'
      ? originalConsole[method].bind(window.console)
      : null;

    window.console[method] = (...args) => {
      try {
        original?.(...args);
      } catch {
        // Preserve the page even if console is unavailable.
      }

      try {
        state.buffer.push(createRecord(method, method, args, state.metadata, 'console'));
        if (state.buffer.length >= DEFAULT_BATCH_SIZE) {
          void flushBuffer(state, 'batch');
        } else {
          scheduleFlush(state, 'timer');
        }
      } catch {
        // Never break the app from logging.
      }
    };
  });

  state.originalOnError = window.onerror;
  state.originalOnUnhandledRejection = window.onunhandledrejection;

  window.onerror = (message, source, lineno, colno, error) => {
    try {
      state.buffer.push({
        category: 'error',
        level: 'error',
        method: 'window.onerror',
        message: safeString(message),
        args: [],
        timestamp: new Date().toISOString(),
        source: safeString(source),
        lineno: lineno ?? null,
        colno: colno ?? null,
        error: safeSerialize(error),
        ...state.metadata,
      });
      void flushBuffer(state, 'window.onerror');
    } catch {
      // ignore
    }

    if (typeof state.originalOnError === 'function') {
      try {
        return state.originalOnError(message, source, lineno, colno, error);
      } catch {
        return false;
      }
    }
    return false;
  };

  window.onunhandledrejection = (event) => {
    try {
      state.buffer.push({
        category: 'error',
        level: 'error',
        method: 'unhandledrejection',
        message: 'Unhandled promise rejection',
        args: [safeSerialize(event?.reason)],
        timestamp: new Date().toISOString(),
        reason: safeSerialize(event?.reason),
        ...state.metadata,
      });
      void flushBuffer(state, 'unhandledrejection');
    } catch {
      // ignore
    }

    if (typeof state.originalOnUnhandledRejection === 'function') {
      try {
        return state.originalOnUnhandledRejection(event);
      } catch {
        return undefined;
      }
    }
    return undefined;
  };

  state.patched = true;
}

function restoreConsole(state) {
  if (!state || !state.patched || typeof window === 'undefined') {
    return;
  }

  if (state.flushTimer) {
    window.clearTimeout(state.flushTimer);
    state.flushTimer = null;
  }

  if (state.originalConsole) {
    const consoleMethods = ['log', 'info', 'warn', 'error', 'debug', 'trace'];
    consoleMethods.forEach((method) => {
      if (typeof state.originalConsole[method] === 'function') {
        window.console[method] = state.originalConsole[method];
      }
    });
  }
  if (state.originalOnError !== undefined) {
    window.onerror = state.originalOnError;
  }
  if (state.originalOnUnhandledRejection !== undefined) {
    window.onunhandledrejection = state.originalOnUnhandledRejection;
  }

  state.patched = false;
}

function useBrowserConsoleBridge(endpointUrl, metadata, enabled) {
  const stableMetadata = useMemo(() => metadata || {}, [JSON.stringify(metadata || {})]);

  useEffect(() => {
    const state = getGlobalState();
    if (!state) {
      return undefined;
    }

    state.refCount += 1;
    state.enabled = enabled !== false;
    state.endpointUrl = normalizeEndpointUrl(endpointUrl);
    state.metadata = {
      ...stableMetadata,
      bridge: 'browser_console',
    };

    if (state.enabled && state.endpointUrl) {
      patchConsole(state);
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        void flushBuffer(state, 'visibilitychange');
      }
    };
    const handlePageHide = () => {
      void flushBuffer(state, 'pagehide');
    };

    window.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('pagehide', handlePageHide);

    return () => {
      window.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('pagehide', handlePageHide);

      state.refCount = Math.max(0, state.refCount - 1);
      if (state.refCount === 0) {
        void flushBuffer(state, 'teardown');
        restoreConsole(state);
        state.buffer = [];
        state.endpointUrl = null;
        state.metadata = {};
        state.enabled = false;
      }
    };
  }, [enabled, endpointUrl, stableMetadata]);
}

export default function BrowserConsoleBridge({
  endpointUrl = null,
  metadata = {},
  enabled = true,
}) {
  useBrowserConsoleBridge(endpointUrl, metadata, enabled);
  return null;
}
