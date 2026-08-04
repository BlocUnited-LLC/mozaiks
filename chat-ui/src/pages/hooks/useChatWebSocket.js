import { useState, useRef, useEffect, useCallback } from 'react';
import resolveWorkflow from '../../utils/resolveWorkflow';

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
}) {
  const [ws, setWs] = useState(null);
  const [connectionStatus, setConnectionStatus] = useState('disconnected');
  const [transportType, setTransportType] = useState(null);
  const [connectionInitialized, setConnectionInitialized] = useState(false);
  const [currentWorkflowName, setCurrentWorkflowName] = useState(
    () => resolveWorkflow(urlWorkflowName) || ''
  );

  const wsRef = useRef(null);
  const connectionInProgressRef = useRef(false);

  // Update workflow name when URL changes
  useEffect(() => {
    if (urlWorkflowName && urlWorkflowName !== currentWorkflowName) {
      setCurrentWorkflowName(urlWorkflowName);
    }
  }, [urlWorkflowName, currentWorkflowName]);

  // Notify parent of connection status changes
  useEffect(() => {
    if (onConnectionChange) {
      onConnectionChange(connectionStatus);
    }
  }, [connectionStatus, onConnectionChange]);

  // Create WebSocket connection
  const connectWebSocket = useCallback(() => {
    if (!chatId) {
      console.error('❌ [WS] WebSocket requires existing chat ID');
      return () => {};
    }

    setConnectionStatus('connecting');
    setTransportType('websocket');

    const workflowName = resolveWorkflow(urlWorkflowName) || currentWorkflowName;
    if (!workflowName) {
      console.warn('⚠️ [WS] No workflow available to connect');
      return () => {};
    }

    const connection = api.createWebSocketConnection(
      appId,
      userId,
      {
        onOpen: () => {
          setConnectionStatus('connected');
        },
        onMessage: (data) => {
          if (onMessage) {
            onMessage(data);
          }
        },
        onError: (error) => {
          console.error('❌ [WS] WebSocket error:', error);
          setConnectionStatus('error');
        },
        onClose: () => {
          setConnectionStatus('disconnected');
          wsRef.current = null;
          setWs(null);
        },
      },
      workflowName,
      chatId
    );

    setWs(connection);
    wsRef.current = connection;

    return () => {
      if (connection) {
        connection.close();
      }
      wsRef.current = null;
    };
  }, [api, appId, userId, chatId, urlWorkflowName, currentWorkflowName, onMessage]);

  // Main connection effect
  useEffect(() => {
    if (!api) return;
    if (!workflowConfigLoaded) return;
    if (!chatId) return;
    if (connectionInitialized || connectionInProgressRef.current) return;

    connectionInProgressRef.current = true;
    setConnectionInitialized(true);

    const connectWithTransport = async () => {
      try {
        const workflowName = resolveWorkflow(urlWorkflowName) || currentWorkflowName;
        if (!workflowName) {
          throw new Error('No workflow available');
        }

        // Query transport info from backend
        const transportInfo = await api.getWorkflowTransport(workflowName);
        if (transportInfo?.transport) {
          setTransportType(transportInfo.transport);
        } else {
          setTransportType('websocket');
        }

        setCurrentWorkflowName(workflowName);
        return connectWebSocket();
      } catch (error) {
        console.error('❌ [WS] Error querying workflow transport:', error);
        const fallbackWf = resolveWorkflow();
        if (!fallbackWf) {
          console.warn('⚠️ [WS] No workflow available for fallback');
          return () => {};
        }
        setTransportType('websocket');
        setCurrentWorkflowName(fallbackWf);
        return connectWebSocket();
      }
    };

    let cleanup;
    connectWithTransport()
      .then((cleanupFn) => {
        cleanup = cleanupFn;
      })
      .catch((error) => {
        console.error('❌ [WS] Failed to connect:', error);
        setConnectionInitialized(false);
        connectionInProgressRef.current = false;
      });

    return () => {
      if (cleanup) cleanup();
      connectionInProgressRef.current = false;
    };
  }, [
    api,
    workflowConfigLoaded,
    chatId,
    connectionInitialized,
    urlWorkflowName,
    currentWorkflowName,
    connectWebSocket,
  ]);

  // Retry connection
  const retryConnection = useCallback(() => {
    setConnectionInitialized(false);
    connectionInProgressRef.current = false;
    setConnectionStatus('disconnected');

    setTimeout(() => {
      if (chatId && workflowConfigLoaded) {
        setConnectionStatus('connecting');
      }
    }, 1000);
  }, [chatId, workflowConfigLoaded]);

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
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setWs(null);
    setConnectionInitialized(false);
    connectionInProgressRef.current = false;
    setConnectionStatus('disconnected');
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
