import React, { useEffect, useState, useCallback } from 'react';
import { appApi } from '../../adapters/api';
import config from '../../config';
import { useChatWebSocket } from '../../pages/hooks';

/**
 * Standalone workflow chat component for route-based triggers.
 *
 * Usage:
 * ```jsx
 * import { WorkflowChat } from '@mozaiks/chat-ui';
 *
 * // Dedicated support page
 * function SupportPage() {
 *   return (
 *     <WorkflowChat
 *       workflow="CustomerSupport"
 *       userId={user.id}
 *     />
 *   );
 * }
 *
 * // With initial context and branding
 * function OrderHelpPage({ params }) {
 *   return (
 *     <WorkflowChat
 *       workflow="CustomerSupport"
 *       userId={user.id}
 *       initialContext={{ order_id: params.id }}
 *       brandName="Acme Support"
 *       logo="/logo.svg"
 *     />
 *   );
 * }
 * ```
 *
 * ## Theming
 *
 * Colors and fonts are controlled via CSS variables. Set these in your app:
 * ```css
 * :root {
 *   --mozaiks-primary: #6366f1;
 *   --mozaiks-primary-hover: #4f46e5;
 *   --mozaiks-bg: #ffffff;
 *   --mozaiks-bg-secondary: #f9fafb;
 *   --mozaiks-text: #111827;
 *   --mozaiks-text-secondary: #6b7280;
 *   --mozaiks-border: #e5e7eb;
 *   --mozaiks-font: system-ui, sans-serif;
 * }
 * ```
 *
 * @param {Object} props
 * @param {string} props.workflow - Workflow name to start
 * @param {string} props.userId - User ID for the session
 * @param {string} [props.appId] - App ID (defaults to config)
 * @param {Object} [props.initialContext] - Initial context variables
 * @param {Function} [props.onMessage] - Callback for incoming messages
 * @param {Function} [props.onComplete] - Callback when workflow completes
 * @param {Function} [props.onClose] - Callback when user closes the chat
 * @param {string} [props.className] - Additional CSS classes
 * @param {boolean} [props.showHeader] - Show header with close button (default: true)
 * @param {string} [props.brandName] - Brand name to display in header (defaults to workflow name)
 * @param {string} [props.logo] - Logo URL to display in header
 * @param {string} [props.backgroundImage] - Background image URL for chat area
 */
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

  const resolvedAppId = appId || config.get('chat.defaultAppId') || 'default';

  // Handle incoming WebSocket messages
  const handleMessage = useCallback((data) => {
    if (data.type === 'message' || data.type === 'agent_message') {
      const newMessage = {
        id: data.id || Date.now(),
        sender: data.sender || 'assistant',
        content: data.content || data.message,
        agentName: data.agent_name,
        timestamp: new Date().toISOString(),
      };
      setMessages(prev => [...prev, newMessage]);
      if (onMessage) onMessage(newMessage);
    }

    if (data.type === 'workflow_complete') {
      if (onComplete) onComplete(data);
    }
  }, [onMessage, onComplete]);

  // WebSocket connection
  const {
    connectionStatus,
    sendMessage: wsSendMessage,
  } = useChatWebSocket({
    api: appApi,
    appId: resolvedAppId,
    userId,
    chatId,
    workflowName: workflow,
    workflowConfigLoaded: true,
    onMessage: handleMessage,
  });

  // Start the workflow on mount
  useEffect(() => {
    let mounted = true;

    const startWorkflow = async () => {
      try {
        setLoading(true);
        setError(null);

        const result = await appApi.startChat(
          resolvedAppId,
          userId,
          workflow
        );

        if (!mounted) return;

        if (result.chat_id) {
          setChatId(result.chat_id);

          // If there's initial context, we might want to send it
          // This depends on how the backend handles context
          if (initialContext) {
            // Context is typically passed in the first message or via API
            console.log('[WorkflowChat] Initial context:', initialContext);
          }
        } else {
          setError(result.error || 'Failed to start workflow');
        }
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
  }, [workflow, userId, resolvedAppId, initialContext]);

  // Send a message
  const handleSendMessage = useCallback((e) => {
    e?.preventDefault();
    if (!inputValue.trim() || !chatId) return;

    const userMessage = {
      id: Date.now(),
      sender: 'user',
      content: inputValue.trim(),
      timestamp: new Date().toISOString(),
    };

    setMessages(prev => [...prev, userMessage]);
    wsSendMessage({ type: 'message', content: inputValue.trim() });
    setInputValue('');
  }, [inputValue, chatId, wsSendMessage]);

  // Handle enter key
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
        style={{ backgroundColor: 'var(--mozaiks-bg, #ffffff)', fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)' }}
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
        style={{ backgroundColor: 'var(--mozaiks-bg, #ffffff)', fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)' }}
      >
        <div className="text-center text-red-500">
          <p className="font-semibold">Failed to start workflow</p>
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  // Build inline styles for CSS variable theming
  const containerStyle = {
    fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)',
    backgroundColor: 'var(--mozaiks-bg, #ffffff)',
    color: 'var(--mozaiks-text, #111827)',
  };

  const headerStyle = {
    backgroundColor: 'var(--mozaiks-bg-secondary, #f9fafb)',
    borderColor: 'var(--mozaiks-border, #e5e7eb)',
  };

  const messagesStyle = backgroundImage
    ? { backgroundImage: `url(${backgroundImage})`, backgroundSize: 'cover', backgroundPosition: 'center' }
    : {};

  return (
    <div className={`flex flex-col h-full ${className}`} style={containerStyle}>
      {/* Header */}
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

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4" style={messagesStyle}>
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className="max-w-[80%] rounded-lg px-4 py-2"
              style={
                msg.sender === 'user'
                  ? { backgroundColor: 'var(--mozaiks-primary, #3b82f6)', color: '#ffffff' }
                  : { backgroundColor: 'var(--mozaiks-bg-secondary, #f3f4f6)', color: 'var(--mozaiks-text, #111827)' }
              }
            >
              {msg.agentName && (
                <p className="text-xs font-semibold mb-1 opacity-70">{msg.agentName}</p>
              )}
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Input */}
      <form
        onSubmit={handleSendMessage}
        className="flex-shrink-0 p-4 border-t"
        style={{ borderColor: 'var(--mozaiks-border, #e5e7eb)' }}
      >
        <div className="flex gap-2">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Type a message..."
            className="flex-1 px-4 py-2 rounded-lg focus:outline-none focus:ring-2"
            style={{
              border: '1px solid var(--mozaiks-border, #d1d5db)',
              backgroundColor: 'var(--mozaiks-bg, #ffffff)',
              color: 'var(--mozaiks-text, #111827)',
            }}
          />
          <button
            type="submit"
            disabled={!inputValue.trim()}
            className="px-4 py-2 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            style={{
              backgroundColor: inputValue.trim()
                ? 'var(--mozaiks-primary, #3b82f6)'
                : 'var(--mozaiks-primary, #3b82f6)',
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
