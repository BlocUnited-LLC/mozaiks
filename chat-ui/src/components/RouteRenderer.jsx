/**
 * Route Renderer
 *
 * ChatPage is the only hardcoded core route — it is the agentic shell.
 * Platform extensions are registered explicitly at shell bootstrap and routed
 * through backend navigation entries composed from page/UI/workflow owner manifests.
 * app.json controls `landing_spot`; shell.json controls header chrome.
 * All routes require auth unless explicitly opted out via meta.requiresAuth: false.
 *
 * @module @mozaiks/chat-ui/components/RouteRenderer
 */

import { Suspense, useMemo, useEffect, useState, useCallback } from 'react';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { useNavigation } from '../providers/NavigationProvider';
import { getComponent, hasComponent } from '../registry/componentRegistry';
import { TransitionScreen } from '../ui/screens/TransitionScreen';
import { useChatUI } from '../context/ChatUIContext';
import Header from './layout/Header';
import Footer from './layout/Footer';
import MobileBottomBar from './layout/MobileBottomBar';
import { useTheme } from '../styles/useTheme';
import { getChatBackgroundSrc } from '../styles/brandAssets';

/**
 * Core routes that are ALWAYS mounted — not driven by owner manifests.
 * Only ChatPage is a true core route. All platform modules/adapters
 * (including AdminPortal) are registered through explicit extension barrels and routed
 * through navigation entries.
 */
const CORE_ROUTES = [
  {
    path: '/',
    component: 'ChatPage',
    meta: { title: 'Chat', requiresAuth: true },
  },
  {
    // Wildcard so /chat/:appId/:workflow also matches
    path: '/chat/*',
    component: 'ChatPage',
    meta: { title: 'Chat', requiresAuth: true },
  },
  {
    path: '/app/*',
    component: 'ChatPage',
    meta: { title: 'Chat', requiresAuth: true },
  },
];

const resolveRouteAppId = (config, user) => {
  return (
    user?.app_id ||
    config?.chat?.defaultAppId ||
    config?.appId ||
    config?.app_id ||
    'default'
  );
};

const resolveRouteUserId = (user) => {
  return (
    user?.id ||
    user?.user_id ||
    user?.email ||
    null
  );
};

const buildWorkflowChatPath = (workflowId, chatId) => {
  const params = new URLSearchParams({
    mode: 'workflow',
    workflow: String(workflowId),
    chat_id: String(chatId),
  });
  return `/chat?${params.toString()}`;
};

const resolveShellMode = (route, chrome) => {
  const declared =
    route?.meta?.shellMode ||
    route?.meta?.shell_mode ||
    route?.shellMode ||
    route?.shell_mode;
  if (declared && chrome?.modes?.[declared]) return declared;
  if (chrome?.defaultMode && chrome?.modes?.[chrome.defaultMode]) return chrome.defaultMode;
  return 'standard';
};

const isChromeSlotVisible = (value, fallback = false) => {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'string') return !['hidden', 'false', 'none'].includes(value.toLowerCase());
  return value !== false;
};

const viewportClassName = ({ desktop, mobile }) => {
  if (desktop && mobile) return '';
  if (desktop && !mobile) return 'hidden md:block';
  if (!desktop && mobile) return 'md:hidden';
  return 'hidden';
};

/**
 * Default loading component for lazy-loaded routes
 */
const DefaultLoadingFallback = () => (
  <div className="flex items-center justify-center min-h-screen bg-background">
    <div className="flex flex-col items-center gap-3">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      <p className="text-sm text-muted-foreground">Loading…</p>
    </div>
  </div>
);

/**
 * Component for rendering 404 / not found pages
 */
const NotFoundPage = () => (
  <div className="flex items-center justify-center min-h-screen bg-background">
    <div className="text-center">
      <h1 className="text-4xl font-black text-foreground mb-4">404</h1>
      <p className="text-muted-foreground">Page not found</p>
    </div>
  </div>
);

/**
 * Component for rendering when a registered component is not found
 */
const ComponentNotFound = ({ componentName }) => (
  <div className="flex items-center justify-center min-h-screen bg-background">
    <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center max-w-md">
      <h1 className="text-lg font-black text-destructive mb-3">Component Not Registered</h1>
      <p className="text-sm text-muted-foreground mb-2">
        <code className="font-mono text-foreground">{componentName}</code> is referenced in navigation but not registered.
      </p>
      <p className="text-xs text-muted-foreground">
        Register it using: <code className="font-mono">registerComponent('{componentName}', YourComponent)</code>
      </p>
    </div>
  </div>
);

/**
 * TransitionRoute — mounts a shell transition directly.
 *
 * When the user resolves:
 *   - backend resolves the transition against SessionRouter
 *   - next transition -> mount it
 *   - workflow target -> backend creates the chat session and returns chat_id
 *
 * Shell entry points use route.transition. When a route enters a workflow
 * sequence, the route-level sequence id is forwarded so branch transitions can
 * keep or override the active journey explicitly.
 */
function TransitionRoute({ route }) {
  const navigate = useNavigate();
  const { user, config } = useChatUI();
  const [currentTransitionId, setCurrentTransitionId] = useState(null);
  const [accumulatedContext, setAccumulatedContext] = useState({});
  const entryTransitionId = route.transition || null;
  const resolvedAppId = resolveRouteAppId(config, user);
  const resolvedUserId = resolveRouteUserId(user);

  useEffect(() => {
    if (entryTransitionId) {
      setCurrentTransitionId(entryTransitionId);
    }
  }, [entryTransitionId]);

  const handleNavigate = useCallback(
    async (option_id = null, contextVariables = {}) => {
      const mergedContext = { ...accumulatedContext, ...contextVariables };

      try {
        const res = await fetch('/api/transitions/resolve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            transition_id: currentTransitionId,
            option_id,
            journey_id: route.sequence || null,
            context_variables: mergedContext,
            app_id: resolvedAppId,
            user_id: resolvedUserId,
          }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || 'Failed to resolve transition');
        }

        const data = await res.json();
        if (data.resolution_type === 'transition' && data.transition?.id) {
          setAccumulatedContext(data.context_variables ?? mergedContext);
          setCurrentTransitionId(data.transition.id);
          return;
        }

        if (data.resolution_type === 'workflow' && data.chat_id && data.workflow_id) {
          setAccumulatedContext({});
          navigate(buildWorkflowChatPath(data.workflow_id, data.chat_id));
          return;
        }

        throw new Error('Transition resolution returned an unsupported response');
      } catch (err) {
        console.error('[TransitionRoute] transition resolution failed:', err);
      }
    },
    [accumulatedContext, currentTransitionId, navigate, resolvedAppId, resolvedUserId, route]
  );

  if (!currentTransitionId) return null;

  return <TransitionScreen transitionId={currentTransitionId} onNavigate={handleNavigate} />;
}

function WorkflowEntryRoute({ route }) {
  const navigate = useNavigate();
  const { user, config } = useChatUI();
  const [error, setError] = useState(null);
  const workflowId = route.workflow || null;
  const resolvedAppId = resolveRouteAppId(config, user);
  const resolvedUserId = resolveRouteUserId(user);

  useEffect(() => {
    if (!workflowId) return undefined;
    let cancelled = false;

    async function startWorkflow() {
      try {
        const res = await fetch('/api/workflows/trigger', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workflow_id: workflowId,
            trigger_source: 'route',
            journey_id: route.sequence || null,
            context_variables: route.context_variables || {},
            app_id: resolvedAppId,
            user_id: resolvedUserId,
          }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || 'Failed to start workflow');
        }

        const data = await res.json();
        if (!cancelled && data.chat_id && data.workflow_id) {
          navigate(buildWorkflowChatPath(data.workflow_id, data.chat_id), { replace: true });
        }
      } catch (err) {
        if (!cancelled) setError(err);
      }
    }

    startWorkflow();
    return () => {
      cancelled = true;
    };
  }, [workflowId, route, navigate, resolvedAppId, resolvedUserId]);

  if (error) {
    return <ComponentNotFound componentName={`Workflow route ${workflowId}: ${error.message}`} />;
  }
  return <DefaultLoadingFallback />;
}

function ShellChromeLayout({ children, route }) {
  const { user, config } = useChatUI();
  const { chrome } = useNavigation();
  const defaultAppId = config?.chat?.defaultAppId || null;
  const { theme: chatTheme, loading: themeLoading } = useTheme(defaultAppId);
  const chatBackgroundSrc = getChatBackgroundSrc(chatTheme);
  const shellMode = resolveShellMode(route, chrome);
  const modeChrome = chrome?.modes?.[shellMode] || chrome?.modes?.standard || {};
  const desktopChrome = modeChrome.desktop || {};
  const mobileChrome = modeChrome.mobile || {};

  const showHeaderDesktop = isChromeSlotVisible(desktopChrome.header, true);
  const showHeaderMobile = isChromeSlotVisible(mobileChrome.header, true);
  const showFooterDesktop = isChromeSlotVisible(desktopChrome.footer, false);
  const showFooterMobile = isChromeSlotVisible(mobileChrome.footer, false);
  const showBottomBarMobile = isChromeSlotVisible(mobileChrome.bottomBar, false);

  const frameClassName = [
    'relative z-0 flex min-h-screen flex-col',
    showHeaderMobile ? 'pt-14' : 'pt-0',
    showHeaderDesktop ? 'md:pt-16' : 'md:pt-0',
    showBottomBarMobile ? 'pb-20' : 'pb-0',
    'md:pb-0',
  ].join(' ');
  const headerClassName = viewportClassName({ desktop: showHeaderDesktop, mobile: showHeaderMobile });
  const footerClassName = viewportClassName({ desktop: showFooterDesktop, mobile: showFooterMobile });

  return (
    <div className="relative min-h-screen overflow-hidden bg-[var(--color-background)] text-[var(--color-text-primary)]">
      <img
        src={chatBackgroundSrc}
        alt=""
        className="fixed inset-0 -z-10 h-full w-full object-cover"
      />
      {(showHeaderDesktop || showHeaderMobile) && (
        <div className={headerClassName}>
          <Header user={user} chatTheme={chatTheme} themeLoading={themeLoading} route={route} shellMode={shellMode} />
        </div>
      )}
      <div className={frameClassName}>
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
        {(showFooterDesktop || showFooterMobile) && (
          <div className={footerClassName}>
            <Footer />
          </div>
        )}
      </div>
      {showBottomBarMobile && (
        <div className="md:hidden">
          <MobileBottomBar route={route} shellMode={shellMode} />
        </div>
      )}
    </div>
  );
}

/**
 * RouteWrapper - Handles auth checks and meta for individual routes
 */
const RouteWrapper = ({
  route,
  component: Component,
  isAuthenticated,
  onAuthRequired,
  wrapInAppShell = false,
}) => {
  const { meta = {} } = route;

  // Check auth requirement
  if (meta.requiresAuth && !isAuthenticated) {
    if (onAuthRequired) {
      onAuthRequired(route.path);
    }
    return <Navigate to={meta.authRedirect || '/login'} replace />;
  }

  // Update document title if specified
  useEffect(() => {
    if (meta.title) {
      const appName = document.title.split(' - ')[0] || 'Mozaiks';
      document.title = `${meta.title} - ${appName}`;
    }
  }, [meta.title]);

  const content = <Component route={route} />;
  if (wrapInAppShell) {
    return <ShellChromeLayout route={route}>{content}</ShellChromeLayout>;
  }
  return content;
};

/**
 * RouteRenderer Component
 *
 * Always mounts core shell routes (ChatPage).
 * Module/adapter routes (AdminPortal etc.) come from backend navigation.
 * Extra routes from backend-composed navigation are appended after.
 * All routes require auth by default; opt out with meta.requiresAuth: false.
 * Supports landing_spot from app-shell config for default redirect.
 *
 * @param {Object} props
 * @param {React.ComponentType} props.LoadingFallback - Component to show while loading
 * @param {React.ComponentType} props.NotFound - Component to show for 404
 * @param {boolean} props.isAuthenticated - Current auth state
 * @param {Function} props.onAuthRequired - Callback when auth is required but user is not authenticated
 */
const RouteRenderer = ({
  LoadingFallback = DefaultLoadingFallback,
  NotFound = NotFoundPage,
  isAuthenticated = false,
  onAuthRequired = null
}) => {
  const { pages, loading, landing_spot } = useNavigation();
  const landingSpot = landing_spot || '/';

  // Build core route elements (always present)
  const coreRouteElements = useMemo(() => {
    return CORE_ROUTES.map((route, index) => {
      const { path, component: componentName } = route;
      if (!hasComponent(componentName)) {
        console.warn(`[RouteRenderer] Core component "${componentName}" not found in registry for route "${path}"`);
        return null;
      }
      const Component = getComponent(componentName);
      return (
        <Route
          key={`core-${index}-${path}`}
          path={path}
          element={
            <Suspense fallback={<LoadingFallback />}>
              <RouteWrapper
                route={route}
                component={Component}
                isAuthenticated={isAuthenticated}
                onAuthRequired={onAuthRequired}
              />
            </Suspense>
          }
        />
      );
    }).filter(Boolean);
  }, [isAuthenticated, onAuthRequired, LoadingFallback]);

  // Build extra route elements from backend-composed navigation (beyond core shell)
  const extraRouteElements = useMemo(() => {
    // Pages need at least a path; transition/workflow routes don't require a component.
    const routablePages = (pages || []).filter(p => p.path && (p.component || p.transition || p.workflow));
    if (routablePages.length === 0) return [];

    // Filter out any pages that overlap with core paths (safety net)
    const corePaths = new Set(CORE_ROUTES.map(r => r.path));
    const extraRoutes = routablePages.filter(r => !corePaths.has(r.path));

    return extraRoutes.map((route, index) => {
      const { path, component: componentName, transition, workflow } = route;

      // Transition routes — no registered component needed; TransitionScreen handles rendering.
      if (transition) {
        const routeWithAuthDefault = {
          ...route,
          meta: {
            ...route.meta,
            requiresAuth: route.meta?.requiresAuth !== false,
          },
        };
        return (
          <Route
            key={`transition-${index}-${path}`}
            path={path}
            element={
              <Suspense fallback={<LoadingFallback />}>
                <RouteWrapper
                  route={routeWithAuthDefault}
                  component={TransitionRoute}
                  isAuthenticated={isAuthenticated}
                  onAuthRequired={onAuthRequired}
                  wrapInAppShell={route.meta?.appShell !== false}
                />
              </Suspense>
            }
          />
        );
      }

      if (workflow) {
        const routeWithAuthDefault = {
          ...route,
          meta: {
            ...route.meta,
            requiresAuth: route.meta?.requiresAuth !== false,
          },
        };
        return (
          <Route
            key={`workflow-entry-${index}-${path}`}
            path={path}
            element={
              <Suspense fallback={<LoadingFallback />}>
                <RouteWrapper
                  route={routeWithAuthDefault}
                  component={WorkflowEntryRoute}
                  isAuthenticated={isAuthenticated}
                  onAuthRequired={onAuthRequired}
                  wrapInAppShell={route.meta?.appShell !== false}
                />
              </Suspense>
            }
          />
        );
      }

      if (!hasComponent(componentName)) {
        console.warn(`[RouteRenderer] Component "${componentName}" not found in registry for route "${path}"`);
        return (
          <Route
            key={`extra-${index}-${path}`}
            path={path}
            element={<ComponentNotFound componentName={componentName} />}
          />
        );
      }

      const Component = getComponent(componentName);

      // Auth is required by default unless explicitly set to false
      const routeWithAuthDefault = {
        ...route,
        meta: {
          ...route.meta,
          requiresAuth: route.meta?.requiresAuth !== false,
        },
      };

      return (
        <Route
          key={`extra-${index}-${path}`}
          path={path}
          element={
            <Suspense fallback={<LoadingFallback />}>
                <RouteWrapper
                  route={routeWithAuthDefault}
                  component={Component}
                  isAuthenticated={isAuthenticated}
                  onAuthRequired={onAuthRequired}
                  wrapInAppShell={route.meta?.appShell !== false}
                />
              </Suspense>
            }
          />
      );
    });
  }, [pages, isAuthenticated, onAuthRequired, LoadingFallback]);

  // Show loading state while navigation config is loading
  if (loading) {
    return <LoadingFallback />;
  }

  return (
    <Routes>
      {/* When a non-root landing_spot is declared (platform mode), redirect / there */}
      {landingSpot !== '/' && (
        <Route path="/" element={<Navigate to={landingSpot} replace />} />
      )}
      {coreRouteElements}
      {extraRouteElements}
      {/* Catch-all: redirect to landing_spot or show 404 */}
      <Route path="*" element={<Navigate to={landingSpot} replace />} />
    </Routes>
  );
};

export default RouteRenderer;
