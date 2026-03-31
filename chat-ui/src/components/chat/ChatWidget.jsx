import React, { useState, useEffect, useCallback, useRef } from 'react';

/**
 * ChatWidget - Floating chat overlay for simple integration.
 *
 * This is the primary component for adding mozaiks workflows to any app.
 * It's a standalone widget that doesn't require any providers or routing.
 *
 * ## Usage
 * ```jsx
 * import { ChatWidget } from '@mozaiks/chat-ui';
 *
 * function App() {
 *   return (
 *     <>
 *       <YourApp />
 *       <ChatWidget
 *         endpoint="ws://localhost:8000/ai"
 *         userId={user.id}
 *       />
 *     </>
 *   );
 * }
 * ```
 *
 * ## With Branding
 * ```jsx
 * <ChatWidget
 *   endpoint="ws://localhost:8000/ai"
 *   userId={user.id}
 *   brandName="Acme Support"
 *   logo="/logo.svg"
 * />
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
 * @param {string} props.endpoint - WebSocket endpoint (e.g., "ws://localhost:8000/ai")
 * @param {string} props.userId - User ID for the session
 * @param {string} [props.appId] - App ID (defaults to "default")
 * @param {string} [props.brandName] - Brand name to display in header (defaults to "Chat")
 * @param {string} [props.logo] - Logo URL for minimized button and header
 * @param {string} [props.backgroundImage] - Background image URL for chat area
 * @param {string} [props.position] - Position: "bottom-right" (default), "bottom-left"
 * @param {Function} [props.onMessage] - Callback for incoming messages
 */
function ChatWidget({
  endpoint,
  userId,
  appId = 'default',
  brandName = 'Chat',
  logo,
  backgroundImage,
  position = 'bottom-right',
  onMessage,
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [connectionStatus, setConnectionStatus] = useState('disconnected');
  const [unreadCount, setUnreadCount] = useState(0);
  const wsRef = useRef(null);
  const messagesEndRef = useRef(null);

  // Parse endpoint to get base URL
  const getWsUrl = useCallback(() => {
    const base = endpoint.replace(/\/$/, '');
    // For now, connect to a general chat endpoint
    return `${base}/ws/general/${appId}/${userId}`;
  }, [endpoint, appId, userId]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Track unread when collapsed
  useEffect(() => {
    if (!isExpanded && messages.length > 0) {
      const lastMsg = messages[messages.length - 1];
      if (lastMsg.sender !== 'user') {
        setUnreadCount(prev => prev + 1);
      }
    }
  }, [messages, isExpanded]);

  // Clear unread when expanded
  useEffect(() => {
    if (isExpanded) {
      setUnreadCount(0);
    }
  }, [isExpanded]);

  // WebSocket connection
  useEffect(() => {
    if (!isExpanded || !endpoint || !userId) return;

    const wsUrl = getWsUrl();
    setConnectionStatus('connecting');

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectionStatus('connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'message' || data.type === 'agent_message' || data.content) {
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
        } catch (e) {
          console.warn('[ChatWidget] Failed to parse message:', e);
        }
      };

      ws.onclose = () => {
        setConnectionStatus('disconnected');
      };

      ws.onerror = (error) => {
        console.error('[ChatWidget] WebSocket error:', error);
        setConnectionStatus('error');
      };

      return () => {
        ws.close();
        wsRef.current = null;
      };
    } catch (error) {
      console.error('[ChatWidget] Failed to connect:', error);
      setConnectionStatus('error');
    }
  }, [isExpanded, endpoint, userId, getWsUrl, onMessage]);

  // Send message
  const handleSend = useCallback(() => {
    if (!inputValue.trim() || !wsRef.current) return;

    const userMessage = {
      id: Date.now(),
      sender: 'user',
      content: inputValue.trim(),
      timestamp: new Date().toISOString(),
    };

    setMessages(prev => [...prev, userMessage]);

    if (wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'message',
        content: inputValue.trim(),
      }));
    }

    setInputValue('');
  }, [inputValue]);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Position classes
  const positionClasses = position === 'bottom-left'
    ? 'left-4 bottom-4'
    : 'right-4 bottom-4';

  // CSS variable-based styles
  const containerStyle = {
    fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)',
  };

  const panelStyle = {
    backgroundColor: 'var(--mozaiks-bg, #ffffff)',
    color: 'var(--mozaiks-text, #111827)',
    borderColor: 'var(--mozaiks-border, #e5e7eb)',
  };

  const headerStyle = {
    backgroundColor: 'var(--mozaiks-bg-secondary, #f9fafb)',
    borderColor: 'var(--mozaiks-border, #e5e7eb)',
  };

  const messagesStyle = backgroundImage
    ? { backgroundImage: `url(${backgroundImage})`, backgroundSize: 'cover', backgroundPosition: 'center' }
    : {};

  // Default logo SVG
  const defaultLogo = (
    <svg className="w-8 h-8" viewBox="0 0 32 32" fill="none">
      <circle cx="16" cy="16" r="14" fill="var(--mozaiks-primary, #6366f1)" />
      <path d="M10 16h12M16 10v12" stroke="white" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );

  // Minimized button
  if (!isExpanded) {
    return (
      <div className={`fixed ${positionClasses} z-50`} style={containerStyle}>
        <button
          type="button"
          onClick={() => setIsExpanded(true)}
          className="relative w-14 h-14 rounded-full shadow-lg hover:shadow-xl transition-all duration-200 hover:scale-105 flex items-center justify-center"
          style={{ backgroundColor: 'var(--mozaiks-primary, #6366f1)' }}
          title={`Open ${brandName}`}
        >
          {logo ? (
            <img src={logo} alt={brandName} className="w-8 h-8" />
          ) : (
            defaultLogo
          )}
          {unreadCount > 0 && (
            <span
              className="absolute -top-1 -right-1 w-5 h-5 rounded-full text-white text-xs flex items-center justify-center"
              style={{ backgroundColor: '#ef4444' }}
            >
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          )}
        </button>
      </div>
    );
  }

  // Expanded panel
  return (
    <div className={`fixed ${positionClasses} z-50`} style={containerStyle}>
      <div
        className="w-96 max-w-[calc(100vw-2rem)] h-[500px] max-h-[calc(100vh-2rem)] rounded-2xl shadow-2xl border flex flex-col overflow-hidden"
        style={panelStyle}
      >
        {/* Header */}
        <div className="flex-shrink-0 p-4 border-b flex items-center justify-between" style={headerStyle}>
          <div className="flex items-center gap-3">
            {logo && <img src={logo} alt={brandName} className="h-8 w-auto" />}
            <div>
              <h3 className="font-semibold">{brandName}</h3>
              <span
                className="text-xs"
                style={{ color: connectionStatus === 'connected' ? '#22c55e' : 'var(--mozaiks-text-secondary, #6b7280)' }}
              >
                {connectionStatus}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setIsExpanded(false)}
            className="p-2 rounded-lg transition-colors hover:bg-black/5"
            style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}
            aria-label="Minimize chat"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3" style={messagesStyle}>
          {messages.length === 0 && (
            <div className="text-center py-8" style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}>
              <p className="text-sm">Start a conversation</p>
            </div>
          )}
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className="max-w-[80%] rounded-2xl px-4 py-2"
                style={
                  msg.sender === 'user'
                    ? { backgroundColor: 'var(--mozaiks-primary, #6366f1)', color: '#ffffff' }
                    : { backgroundColor: 'var(--mozaiks-bg-secondary, #f3f4f6)', color: 'var(--mozaiks-text, #111827)' }
                }
              >
                {msg.agentName && (
                  <p className="text-xs font-medium mb-1 opacity-70">{msg.agentName}</p>
                )}
                <p className="whitespace-pre-wrap text-sm">{msg.content}</p>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="flex-shrink-0 p-4 border-t" style={{ borderColor: 'var(--mozaiks-border, #e5e7eb)' }}>
          <div className="flex gap-2">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Type a message..."
              className="flex-1 px-4 py-2 rounded-full focus:outline-none focus:ring-2 text-sm"
              style={{
                border: '1px solid var(--mozaiks-border, #e5e7eb)',
                backgroundColor: 'var(--mozaiks-bg, #ffffff)',
                color: 'var(--mozaiks-text, #111827)',
              }}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!inputValue.trim()}
              className="px-4 py-2 rounded-full text-white disabled:opacity-50 transition-colors text-sm"
              style={{ backgroundColor: 'var(--mozaiks-primary, #6366f1)' }}
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ChatWidget;
