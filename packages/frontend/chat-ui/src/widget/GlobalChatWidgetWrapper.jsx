/**
 * GlobalChatWidgetWrapper
 * 
 * Renders the persistent chat widget (PersistentChatWidget) and overlay (ChatOverlay)
 * when the user is in widget mode (navigating outside of ChatPage).
 * 
 * This component should be rendered at the root level, INSIDE the ChatUIProvider,
 * so it has access to the chat context.
 * 
 * Usage:
 * ```jsx
 * <ChatUIProvider>
 *   <Router>
 *     <GlobalChatWidgetWrapper />
 *     <Routes>...</Routes>
 *   </Router>
 * </ChatUIProvider>
 * ```
 */
import React from 'react';
import { useLocation } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import PersistentChatWidget from '../components/chat/PersistentChatWidget';

/**
 * GlobalChatWidgetWrapper
 * 
 * Conditionally renders the chat widget UI based on:
 * - isInWidgetMode: User is on a non-ChatPage route
 * - isWidgetVisible: Page hasn't suppressed the widget
 * - isChatOverlayOpen: User expanded the widget to overlay mode
 */
const GlobalChatWidgetWrapper = () => {
  const location = useLocation();
  const {
    isInWidgetMode,
    isWidgetVisible,
    activeChatId,
    activeWorkflowName,
    conversationMode,
  } = useChatUI();

  // Determine if we're on the primary chat routes (don't show widget there)
  const pathSegments = location.pathname.split('/').filter(Boolean);
  const isPrimaryChatRoute =
    pathSegments.length === 0 ||
    pathSegments[0] === 'chat' ||
    (pathSegments[0] === 'app' && pathSegments.length >= 3); // /app/:appId/:workflow pattern

  // Don't render anything on primary chat routes
  if (isPrimaryChatRoute) {
    return null;
  }

  // Don't render if not in widget mode or widget is hidden
  if (!isInWidgetMode || !isWidgetVisible) {
    return null;
  }

  return (
    <>
      <PersistentChatWidget
        chatId={activeChatId}
        workflowName={activeWorkflowName}
        conversationMode={conversationMode}
      />
    </>
  );
};

export default GlobalChatWidgetWrapper;
