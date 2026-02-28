import React from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * ChatOverlay - Full-screen chat interface that appears when bubble is clicked
 * 
 * Allows users to continue their chat session from any page.
 * Can be minimized back to bubble or navigated to full chat page.
 */
const ChatOverlay = ({ 
  isOpen = false,
  onClose = () => {},
  chatId = null,
  workflowName = null
}) => {
  const navigate = useNavigate();

  if (!isOpen) return null;

  const handleGoToFullChat = () => {
    onClose();
    navigate(`/chat${chatId ? `?chat_id=${chatId}` : ''}`);
  };

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 animate-fadeIn"
        onClick={onClose}
      />

      {/* Chat Overlay Panel */}
      <div className="fixed inset-y-0 right-0 w-full md:w-[600px] lg:w-[700px] bg-gradient-to-br from-gray-900 via-gray-800 to-black border-l border-gray-700 shadow-2xl z-50 flex flex-col animate-slideInRight">
        {/* Header */}
        <div className="flex-shrink-0 bg-gradient-to-r from-gray-900 to-gray-800 border-b border-gray-700 px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)] flex items-center justify-center shadow-lg">
                <span className="text-xl">💬</span>
              </div>
              <div>
                <h3 className="text-lg font-bold text-white">
                  {workflowName || 'Chat Session'}
                </h3>
                <p className="text-xs text-gray-400">AI Assistant</p>
              </div>
            </div>
            
            <div className="flex items-center gap-2">
              {/* Expand to Full Chat */}
              <button
                onClick={handleGoToFullChat}
                className="p-2 rounded-lg bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 hover:border-gray-600 transition-all"
                title="Open in full chat page"
              >
                <svg className="w-5 h-5 text-gray-300 hover:text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </button>
              
              {/* Minimize */}
              <button
                onClick={onClose}
                className="p-2 rounded-lg bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 hover:border-gray-600 transition-all"
                title="Minimize to bubble"
              >
                <svg className="w-5 h-5 text-gray-300 hover:text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        {/* Chat Content */}
        <div className="flex-1 overflow-hidden">
          <div className="h-full flex items-center justify-center p-8">
            <div className="text-center space-y-4 max-w-md">
              <div className="w-20 h-20 mx-auto rounded-full bg-gradient-to-br from-[var(--color-primary)]/20 to-[var(--color-secondary)]/20 flex items-center justify-center">
                <img src="/assets/mozaik_logo.svg" alt="Mozaik" className="w-12 h-12 opacity-50" />
              </div>
              <div>
                <h4 className="text-lg font-semibold text-white mb-2">
                  Quick Chat Access
                </h4>
                <p className="text-sm text-gray-400 mb-6">
                  Your chat session is ready. Click the button below to continue in the full chat interface.
                </p>
                <button
                  onClick={handleGoToFullChat}
                  className="px-6 py-3 bg-gradient-to-r from-[var(--color-primary)] to-[var(--color-secondary)] text-white rounded-xl hover:shadow-lg hover:shadow-[var(--color-primary)]/50 transition-all duration-300 font-semibold"
                >
                  Open Full Chat
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <style jsx>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        
        @keyframes slideInRight {
          from { 
            transform: translateX(100%);
            opacity: 0;
          }
          to { 
            transform: translateX(0);
            opacity: 1;
          }
        }
        
        .animate-fadeIn {
          animation: fadeIn 0.2s ease-out;
        }
        
        .animate-slideInRight {
          animation: slideInRight 0.3s ease-out;
        }
      `}</style>
    </>
  );
};

export default ChatOverlay;
