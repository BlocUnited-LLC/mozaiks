import { useState, useRef, useEffect, useCallback } from 'react';
import resolveWorkflow from '../../utils/resolveWorkflow';
import { shouldReconnectAfterClose } from '../../adapters/socketClose.js';

/**
 * Hook for managing WebSocket connection to the chat backend.
 *
 * Extracts connection lifecycle, reconnection logic, and transport management
 * from ChatPage to reduce complexity.
 *
 * @param {Object} options
 * @param {Object} options.api - API adapter instance
 * @param {string} options.appId - Current application ID
 * @param {string} options.userId - Current user ID
 * @param {string} options.chatId - Current chat session ID
 * @param {string} options.workflowName - Workflow name from URL or config
 * @param {boolean} options.workflowConfigLoaded - Whether workflow config has loaded
 * @param {Function} options.onMessage - Callback for incoming messages
 * @param {Function} options.onConnectionChange - Callback for connection status changes
 * @returns {Object} WebSocket state and controls
 */
export function useChatWebSocket({
  api,
  appId,
  userId,
  chatId,
  workflowName: urlWorkflowName,
  workflowConfigLoaded,
  onMessage,
  onConnectionChange,
  recoverOnDrop = false,
  onInterrupted,
}) {
  const [ws, setWs] = useState(null);
  const [connectionStatus, setConnectionStatus] = useState('disconnected');
  const [transportType, setTransportType] = useState(null);
  const [connectionInitialized, setConnectionInitialized] = useState(false);
  const [currentWorkflowName, setCurrentWorkflowName] = useState(
    () => resolveWorkflow(urlWorkflowName) || ''
  );

  const wsRef = useRef(null);
  const [attempt, setAttempt] = useState(0);
  const [paused, setPaused] = useState(false);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onInterruptedRef = useRef(onInterrupted);
  onInterruptedRef.current = onInterrupted;
  const recoverOnDropRef = useRef(recoverOnDrop);
  recoverOnDropRef.current = recoverOnDrop;
  const automaticRetryAvailable = useRef(true);

  useEffect(() => {
    setCurrentWorkflowName(resolveWorkflow(urlWorkflowName) || '');
    setPaused(false);
  }, [urlWorkflowName, appId, userId, chatId]);

  useEffect(() => {
    onConnectionChange?.(connectionStatus);
  }, [connectionStatus, onConnectionChange]);

  useEffect(() => {
    if (!api || !workflowConfigLoaded || !chatId || !currentWorkflowName || paused) return;
    let closed = false;
    let interrupted = false;
    let retryTimer;
    let socket;
    setConnectionInitialized(true);
    setConnectionStatus('connecting');
    async function connect() {
      let transport;
      try {
        transport = await api.getWorkflowTransport(currentWorkflowName);
      } catch {
        // Discovery is optional for a named workflow with a WebSocket endpoint.
      }
      if (closed) return;
      setTransportType(transport?.transport || 'websocket');
      socket = api.createWebSocketConnection(appId, userId, {
        onOpen: () => { if (!closed && !interrupted) setConnectionStatus('connected'); },
        onMessage: data => {
          if (closed || interrupted) return;
          automaticRetryAvailable.current = true;
          onMessageRef.current?.(data);
        },
        onError: () => { if (!closed && !interrupted) setConnectionStatus('error'); },
        onClose: info => {
          if (closed || interrupted) return;
          interrupted = true;
          wsRef.current = null;
          setWs(null);
          onInterruptedRef.current?.();
          if (recoverOnDropRef.current && automaticRetryAvailable.current && shouldReconnectAfterClose(info)) {
            automaticRetryAvailable.current = false;
            setConnectionStatus('reconnecting');
            retryTimer = setTimeout(() => setAttempt(value => value + 1), 250);
          } else {
            setConnectionStatus('disconnected');
          }
        },
      }, currentWorkflowName, chatId, attempt > 0 ? { resumeSession: true, suppressHistoryReplay: true } : undefined);
      wsRef.current = socket;
      setWs(socket);
    }
    connect().catch(() => { if (!closed) setConnectionStatus('error'); });
    return () => {
      closed = true;
      clearTimeout(retryTimer);
      socket?.close();
      wsRef.current = null;
      setWs(null);
      setConnectionInitialized(false);
      setConnectionStatus('disconnected');
    };
  }, [api, workflowConfigLoaded, appId, userId, chatId, currentWorkflowName, paused, attempt]);

  const retryConnection = useCallback(() => {
    setPaused(false);
    setAttempt(value => value + 1);
  }, []);

  // Send message through WebSocket
  const sendMessage = useCallback(
    (data) => {
      const activeWs = wsRef.current;
      if (activeWs && activeWs.send) {
        return activeWs.send(data);
      }
      console.warn('⚠️ [WS] No WebSocket connection available');
      return false;
    },
    []
  );

  // Close connection
  const disconnect = useCallback(() => {
    setPaused(true);
  }, []);

  // Reset connection state (for navigation triggers)
  const resetConnection = useCallback(() => {
    disconnect();
    setCurrentWorkflowName('');
  }, [disconnect]);

  return {
    // State
    ws,
    wsRef,
    connectionStatus,
    transportType,
    connectionInitialized,
    currentWorkflowName,

    // Actions
    sendMessage,
    retryConnection,
    disconnect,
    resetConnection,
    setCurrentWorkflowName,
  };
}

export default useChatWebSocket;
