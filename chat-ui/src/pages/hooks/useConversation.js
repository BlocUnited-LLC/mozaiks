import { useState, useRef, useEffect, useCallback } from 'react';

/**
 * Normalize a snapshot message from the backend to our internal format.
 */
const normalizeSnapshotMessage = (msg, index) => {
  if (!msg || typeof msg !== 'object') {
    return {
      id: `snapshot-${index}-${Date.now()}`,
      sender: 'user',
      agentName: 'user',
      content: '',
      isStreaming: false,
    };
  }

  const role = msg.role || 'user';
  const agentName =
    msg.agent || msg.agent_name || msg.name || (role === 'assistant' ? 'assistant' : 'user');
  const metadata = msg.metadata || null;
  const uiToolMeta = metadata?.ui_tool;
  const uiToolEvent =
    uiToolMeta && typeof uiToolMeta === 'object'
      ? {
          ui_tool_id: uiToolMeta.ui_tool_id,
          eventId: uiToolMeta.event_id,
          payload: uiToolMeta.payload || {},
          display: uiToolMeta.display || 'inline',
          workflow_name: msg.workflow_name || uiToolMeta.workflow_name,
        }
      : null;

  return {
    id: msg.id || `snapshot-${index}-${Date.now()}`,
    sender: role === 'assistant' ? 'agent' : 'user',
    agentName,
    content: msg.content || '',
    isStreaming: false,
    structuredOutput: msg.structured_output,
    structuredSchema: msg.structured_schema,
    uiToolEvent,
    ui_tool_completed: uiToolMeta?.ui_tool_completed || false,
    ui_tool_status: uiToolMeta?.ui_tool_status || null,
    metadata,
    timestamp: msg.timestamp || null,
  };
};

/**
 * Extract agent name from nested message structures.
 */
const extractAgentName = (data) => {
  try {
    if (data.agent && data.agent !== 'Unknown') return data.agent;
    if (data.agentName && data.agentName !== 'Unknown') return data.agentName;
    if (data.agent_name && data.agent_name !== 'Unknown') return data.agent_name;

    if (data.content && typeof data.content === 'string') {
      const parsed = JSON.parse(data.content);
      if (parsed?.data?.content?.sender) return parsed.data.content.sender;
      if (parsed?.data?.agent) return parsed.data.agent;
    }

    return 'Agent';
  } catch {
    return data.agent || data.agent_name || 'Agent';
  }
};

/**
 * Hook for managing conversation messages state.
 *
 * Extracts message state, streaming, and message processing from ChatPage.
 *
 * @param {Object} options
 * @param {string} options.chatId - Current chat session ID
 * @param {string} options.conversationMode - 'ask' or 'workflow'
 * @param {Function} options.setAskMessages - Context setter for ask messages
 * @param {Function} options.setWorkflowMessages - Context setter for workflow messages
 * @returns {Object} Conversation state and methods
 */
export function useConversation({
  chatId,
  conversationMode,
  setAskMessages,
  setWorkflowMessages,
}) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showInitSpinner, setShowInitSpinner] = useState(false);
  const [pendingInputRequestId, setPendingInputRequestId] = useState(null);

  // Refs
  const messagesRef = useRef([]);
  const messagesSnapshotAppliedRef = useRef(false);
  const initSpinnerShownRef = useRef(false);
  const initSpinnerHiddenOnceRef = useRef(false);
  const firstAgentMessageSuppressedRef = useRef(false);
  const workflowMessagesCacheRef = useRef([]);
  const generalMessagesCacheRef = useRef([]);

  // Keep messagesRef in sync
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Reset snapshot flag when chatId changes
  useEffect(() => {
    messagesSnapshotAppliedRef.current = false;
  }, [chatId]);

  // Sync messages to context based on conversation mode
  useEffect(() => {
    if (conversationMode === 'workflow') {
      workflowMessagesCacheRef.current = messages;
      if (setWorkflowMessages) setWorkflowMessages(messages);
    } else {
      generalMessagesCacheRef.current = messages;
    }
    if (conversationMode === 'ask' && setAskMessages) {
      setAskMessages(messages);
    }
  }, [messages, conversationMode, setAskMessages, setWorkflowMessages]);

  // Wrapped setter with logging capability
  const setMessagesWithLogging = useCallback((updater) => {
    setMessages((prev) => (typeof updater === 'function' ? updater(prev) : updater));
  }, []);

  // Add a new message
  const addMessage = useCallback((message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  // Update an existing message by ID
  const updateMessage = useCallback((messageId, updates) => {
    setMessages((prev) =>
      prev.map((msg) => (msg.id === messageId ? { ...msg, ...updates } : msg))
    );
  }, []);

  // Remove thinking/placeholder messages
  const removeThinkingMessages = useCallback(() => {
    setMessages((prev) => prev.filter((msg) => !msg.isThinking));
  }, []);

  // Handle stream chunk (append to existing streaming message or create new)
  const handleStreamChunk = useCallback((data) => {
    const agentName = extractAgentName(data);
    const chunkContent = data.content || '';
    if (!chunkContent) return;

    // Hide spinner on first streaming token
    if (showInitSpinner) setShowInitSpinner(false);

    setMessages((prev) => {
      const updated = [...prev];
      // Find existing streaming message from same agent
      for (let i = updated.length - 1; i >= 0; i--) {
        const m = updated[i];
        if (m.__streaming && m.agentName === agentName) {
          m.content += chunkContent;
          return updated;
        }
      }
      // Create new streaming message
      updated.push({
        id: `stream-chunk-${Date.now()}`,
        sender: 'agent',
        agentName,
        content: chunkContent,
        isStreaming: true,
        __streaming: true,
        __streamId: data.stream_id || null,
        isStructuredCapable: false,
        isVisual: true,
        isToolAgent: false,
      });
      return updated;
    });
  }, [showInitSpinner]);

  // Finalize streaming message
  const finalizeStreamingMessage = useCallback((agentName, finalContent) => {
    setMessages((prev) => {
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i--) {
        const m = updated[i];
        if (m.__streaming && m.agentName === agentName) {
          m.isStreaming = false;
          m.__streaming = false;
          if (finalContent) m.content = finalContent;
          break;
        }
      }
      return updated;
    });
  }, []);

  // Apply messages snapshot from backend
  const applyMessagesSnapshot = useCallback((snapshotMessages, options = {}) => {
    const { replace = false } = options;
    if (!Array.isArray(snapshotMessages)) return;

    if (!replace && messagesSnapshotAppliedRef.current) return;
    if (!replace && messagesRef.current.length > 0) return;

    const normalized = snapshotMessages.map((msg, idx) =>
      normalizeSnapshotMessage(msg, idx)
    );
    setMessagesWithLogging(normalized);
    messagesSnapshotAppliedRef.current = true;
  }, [setMessagesWithLogging]);

  // Restore workflow messages from cache
  const restoreWorkflowMessages = useCallback(() => {
    const cached = workflowMessagesCacheRef.current;
    if (cached && cached.length > 0) {
      setMessagesWithLogging(cached);
    }
  }, [setMessagesWithLogging]);

  // Restore general messages from cache
  const restoreGeneralMessages = useCallback(() => {
    const cached = generalMessagesCacheRef.current;
    if (cached && cached.length > 0) {
      setMessagesWithLogging(cached);
    }
  }, [setMessagesWithLogging]);

  // Clear messages
  const clearMessages = useCallback(() => {
    setMessagesWithLogging([]);
  }, [setMessagesWithLogging]);

  // Handle spinner visibility
  const showSpinner = useCallback(() => {
    if (!initSpinnerHiddenOnceRef.current && !initSpinnerShownRef.current) {
      setShowInitSpinner(true);
      initSpinnerShownRef.current = true;
    }
  }, []);

  const hideSpinner = useCallback(() => {
    if (initSpinnerShownRef.current && !initSpinnerHiddenOnceRef.current) {
      initSpinnerHiddenOnceRef.current = true;
      setShowInitSpinner(false);
    }
  }, []);

  return {
    // State
    messages,
    messagesRef,
    loading,
    showInitSpinner,
    pendingInputRequestId,

    // Setters
    setMessages: setMessagesWithLogging,
    setLoading,
    setPendingInputRequestId,

    // Actions
    addMessage,
    updateMessage,
    removeThinkingMessages,
    handleStreamChunk,
    finalizeStreamingMessage,
    applyMessagesSnapshot,
    restoreWorkflowMessages,
    restoreGeneralMessages,
    clearMessages,
    showSpinner,
    hideSpinner,

    // Refs
    workflowMessagesCacheRef,
    generalMessagesCacheRef,
    messagesSnapshotAppliedRef,
    firstAgentMessageSuppressedRef,

    // Utils
    extractAgentName,
    normalizeSnapshotMessage,
  };
}

export default useConversation;
