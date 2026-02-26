import React from 'react';

/**
 * ChatBubble - Floating chat button that appears on non-chat pages
 * 
 * Shows when user navigates away from chat page but has an active session.
 * Displays unread message count and expands to full chat overlay when clicked.
 */
const ChatBubble = ({ 
  unreadCount = 0, 
  isAiTyping = false,
  onClick = () => {},
  workflowName = null 
}) => {
  return (
    <div className="fixed bottom-6 right-6 z-50">
      {/* Main Chat Bubble */}
      <button
        onClick={onClick}
        className="group relative w-16 h-16 bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)] rounded-full shadow-2xl hover:shadow-[var(--color-primary)]/50 transition-all duration-300 hover:scale-110 flex items-center justify-center"
        title={workflowName ? `Continue chat: ${workflowName}` : 'Open chat'}
      >
        {/* Chat Icon */}
        <svg 
          className="w-8 h-8 text-white" 
          fill="none" 
          stroke="currentColor" 
          viewBox="0 0 24 24"
        >
          <path 
            strokeLinecap="round" 
            strokeLinejoin="round" 
            strokeWidth={2} 
            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" 
          />
        </svg>

        {/* Unread Badge */}
        {unreadCount > 0 && (
          <div className="absolute -top-1 -right-1 w-6 h-6 bg-red-500 rounded-full flex items-center justify-center text-white text-xs font-bold shadow-lg animate-pulse">
            {unreadCount > 9 ? '9+' : unreadCount}
          </div>
        )}

        {/* AI Typing Indicator */}
        {isAiTyping && (
          <div className="absolute -bottom-1 -right-1 w-4 h-4 bg-green-500 rounded-full border-2 border-white shadow-lg">
            <div className="w-full h-full bg-green-400 rounded-full animate-ping"></div>
          </div>
        )}

        {/* Glow Effect */}
        <div className="absolute inset-0 rounded-full bg-gradient-to-br from-[var(--color-primary)]/50 to-[var(--color-secondary)]/50 blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 -z-10"></div>
      </button>

      {/* Workflow Name Tooltip (appears on hover) */}
      {workflowName && (
        <div className="absolute bottom-20 right-0 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
          <div className="bg-gray-900 text-white text-sm px-4 py-2 rounded-lg shadow-xl border border-gray-700 whitespace-nowrap">
            <div className="text-xs text-gray-400 mb-1">Continue working on:</div>
            <div className="font-semibold">{workflowName}</div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatBubble;
