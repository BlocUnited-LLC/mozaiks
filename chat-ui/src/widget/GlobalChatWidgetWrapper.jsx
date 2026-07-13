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
import React, { useEffect, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import { useNavigation } from '../providers/NavigationProvider';
import PersistentChatWidget from '../components/chat/PersistentChatWidget';

/**
 * Match a route pattern (e.g. /apps/:appId/overview) against a concrete pathname.
 * Returns true if every segment matches (param segments start with :).
 */
function matchRoutePattern(pattern, pathname) {
  const patternParts = pattern.split('/').filter(Boolean);
  const pathParts = pathname.split('/').filter(Boolean);
  if (patternParts.length !== pathParts.length) return false;
  return patternParts.every((part, i) => part.startsWith(':') || part === pathParts[i]);
}

/**
 * GlobalChatWidgetWrapper
 *
 * Conditionally renders the chat widget UI based on:
 * - isInWidgetMode: User is on a non-ChatPage route
 * - isWidgetVisible: Page hasn't suppressed the widget
 * - isChatOverlayOpen: User expanded the widget to overlay mode
 *
 * Resolves page-level AI context from route_manifest meta.ai_context so the
 * widget can surface what page the user is on when starting an Ask conversation.
 */
const GlobalChatWidgetWrapper = () => {
  const location = useLocation();
  const { pages } = useNavigation();
  const {
    isInWidgetMode,
    setIsInWidgetMode,
    isWidgetVisible,
    setIsWidgetVisible,
    activeChatId,
    activeWorkflowName,
    conversationMode,
  } = useChatUI();

  const pageContext = useMemo(() => {
    if (!Array.isArray(pages) || !location.pathname) return null;
    const matched = pages.find((p) => p.path && matchRoutePattern(p.path, location.pathname));
    return matched?.meta?.ai_context || null;
  }, [pages, location.pathname]);

  // Determine if we're on the primary chat routes (don't show widget there)
  const pathSegments = location.pathname.split('/').filter(Boolean);
  const isPrimaryChatRoute =
    pathSegments.length === 0 ||
    pathSegments[0] === 'chat' ||
    (pathSegments[0] === 'app' && pathSegments.length >= 3); // /app/:appId/:workflow pattern

  // Ensure widget mode is active on non-chat routes.
  // This keeps the persistent widget available across module/admin/discovery pages
  // without requiring each page to call useWidgetMode().
  useEffect(() => {
    if (isPrimaryChatRoute) return;
    if (!isInWidgetMode) {
      setIsInWidgetMode(true);
    }
    if (!isWidgetVisible) {
      setIsWidgetVisible(true);
    }
  }, [isPrimaryChatRoute, isInWidgetMode, isWidgetVisible, setIsInWidgetMode, setIsWidgetVisible]);

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
        pageContext={pageContext}
      />
    </>
  );
};

export default GlobalChatWidgetWrapper;
