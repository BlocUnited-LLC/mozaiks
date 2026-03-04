/**
 * Route Renderer
 *
 * Core routes (ChatPage, AdminPortal) are always mounted — they are the shell.
 * navigation.json defines EXTRA routes beyond the core shell.
 * `landing_spot` (from navigation config) controls the default redirect.
 * All routes require auth unless explicitly opted out via meta.requiresAuth: false.
 *
 * @module @mozaiks/chat-ui/components/RouteRenderer
 */

import React, { Suspense, useMemo } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useNavigation } from '../providers/NavigationProvider';
import { getComponent, hasComponent } from '../registry/componentRegistry';

/**
 * Core routes that are ALWAYS mounted — not driven by navigation.json.
 * These are the agentic shell pages every app gets out of the box.
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
  {
    path: '/admin',
    component: 'DashboardPage',
    meta: { title: 'Admin Portal', requiresAuth: true },
  },
];

/**
 * Default loading component for lazy-loaded routes
 */
const DefaultLoadingFallback = () => (
  <div className="flex items-center justify-center min-h-screen">
    <div className="text-center">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto mb-4"></div>
      <p className="text-gray-500">Loading...</p>
    </div>
  </div>
);

/**
 * Component for rendering 404 / not found pages
 */
const NotFoundPage = () => (
  <div className="flex items-center justify-center min-h-screen">
    <div className="text-center">
      <h1 className="text-4xl font-bold text-gray-800 dark:text-gray-200 mb-4">404</h1>
      <p className="text-gray-600 dark:text-gray-400">Page not found</p>
    </div>
  </div>
);

/**
 * Component for rendering when a registered component is not found
 */
const ComponentNotFound = ({ componentName }) => (
  <div className="flex items-center justify-center min-h-screen">
    <div className="text-center">
      <h1 className="text-2xl font-bold text-red-600 mb-4">Component Not Registered</h1>
      <p className="text-gray-600 dark:text-gray-400">
        The component "{componentName}" is referenced in navigation.json but not registered.
      </p>
      <p className="text-sm text-gray-500 mt-2">
        Register it using: registerComponent('{componentName}', YourComponent)
      </p>
    </div>
  </div>
);

/**
 * RouteWrapper - Handles auth checks and meta for individual routes
 */
const RouteWrapper = ({
  route,
  component: Component,
  isAuthenticated,
  onAuthRequired
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
  React.useEffect(() => {
    if (meta.title) {
      const appName = document.title.split(' - ')[0] || 'Mozaiks';
      document.title = `${meta.title} - ${appName}`;
    }
  }, [meta.title]);

  return <Component route={route} />;
};

/**
 * RouteRenderer Component
 *
 * Always mounts core shell routes (ChatPage, AdminPortal).
 * Extra routes from navigation.json are appended after.
 * All routes require auth by default; opt out with meta.requiresAuth: false.
 * Supports landing_spot from navigation config for default redirect.
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

  // Build extra route elements from navigation.json pages[] (beyond core shell)
  const extraRouteElements = useMemo(() => {
    // Only pages that define both path + component can be SPA routes
    const routablePages = (pages || []).filter(p => p.path && p.component);
    if (routablePages.length === 0) return [];

    // Filter out any pages that overlap with core paths (safety net)
    const corePaths = new Set(CORE_ROUTES.map(r => r.path));
    const extraRoutes = routablePages.filter(r => !corePaths.has(r.path));

    return extraRoutes.map((route, index) => {
      const { path, component: componentName } = route;

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
      {coreRouteElements}
      {extraRouteElements}
      {/* Catch-all: redirect to landing_spot or show 404 */}
      <Route path="*" element={<Navigate to={landingSpot} replace />} />
    </Routes>
  );
};

export default RouteRenderer;
