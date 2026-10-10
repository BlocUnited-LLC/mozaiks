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
import { matchRoutes, useLocation } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import { useNavigation } from '../providers/NavigationProvider';
import PersistentChatWidget from '../components/chat/PersistentChatWidget';
import { getShellRoutes } from '../navigation/shellRoutes';
import { getUserRoles, roleMatches } from '../navigation/shellActions';

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
  const { pages, loading, landing_spot, navigation } = useNavigation();
  const {
    user,
    loading: authLoading,
    isInWidgetMode,
    setIsInWidgetMode,
    isWidgetVisible,
    setIsWidgetVisible,
    activeWorkflowName,
  } = useChatUI();

  const matchedPage = useMemo(() => {
    if (loading || (location.pathname === '/' && landing_spot && landing_spot !== '/')) return null;
    const { coreRoutes, pageRoutes } = getShellRoutes(pages);
    const matches = matchRoutes(
      [...coreRoutes, ...pageRoutes].map(route => ({ path: route.path, handle: route })),
      location.pathname,
    );
    return matches?.[matches.length - 1]?.route.handle || null;
  }, [pages, loading, landing_spot, location.pathname]);
  const pageContext = matchedPage?.meta?.ai_context || null;

  // The declared fresh-start entrypoint (extension_registry.json entrypoints[]
  // with meta.freshStart, projected into shell config). This is where a user
  // with no running workflow should begin — the app's own start-a-build
  // surface, whatever it is, rather than a guessed workflow.
  const freshStartPath = useMemo(() => {
    if (!Array.isArray(pages)) return null;
    const entry = pages.find((p) => p?.path && p?.meta?.freshStart);
    return entry?.path || null;
  }, [pages]);
  // Stable page identity (the route pattern) so the backend can resolve the
  // page's declared ask-context actions server-side.
  const pagePath = matchedPage?.path || null;

  // RouteRenderer gives transition/workflow entries precedence over component.
  const isPrimaryChatRoute = matchedPage?.component === 'ChatPage'
    && !matchedPage.transition && !matchedPage.workflow;
  const authRoutes = navigation?.auth?.contract?.routes;
  const isAuthRoute = ['LoginPage', 'AuthCallbackPage'].includes(matchedPage?.component)
    || [authRoutes?.login, authRoutes?.callback].some(path => path && path === matchedPage?.path);
  const meta = matchedPage?.meta || {};
  const canViewPage = !authLoading && (meta.requiresAuth === false || Boolean(user))
    && roleMatches(meta.requiresRole || meta.requiredRole || meta.roles, getUserRoles(user));
  const isAppRoute = Boolean(matchedPage) && !isPrimaryChatRoute && !isAuthRoute && canViewPage;

  // Ensure widget mode is active on non-chat routes.
  // This keeps the persistent widget available across module/admin/discovery pages
  // without requiring each page to call useWidgetMode().
  useEffect(() => {
    if (!isAppRoute) return;
    if (!isInWidgetMode) {
      setIsInWidgetMode(true);
    }
    if (!isWidgetVisible) {
      setIsWidgetVisible(true);
    }
  }, [isAppRoute, isInWidgetMode, isWidgetVisible, setIsInWidgetMode, setIsWidgetVisible]);

  // Chat and unresolved/redirecting routes have no app-page widget owner.
  if (!isAppRoute) {
    return null;
  }

  // Don't render if not in widget mode or widget is hidden
  if (!isInWidgetMode || !isWidgetVisible) {
    return null;
  }

  return (
    <>
      <PersistentChatWidget
        workflowName={activeWorkflowName}
        pageContext={pageContext}
        pagePath={pagePath}
        freshStartPath={freshStartPath}
      />
    </>
  );
};

export default GlobalChatWidgetWrapper;
