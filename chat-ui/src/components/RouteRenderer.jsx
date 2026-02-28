/**
 * Route Renderer
 *
 * Dynamically renders routes based on navigation.json configuration.
 * Resolves components from the component registry.
 *
 * @module @mozaiks/chat-ui/components/RouteRenderer
 */

import React, { Suspense, useMemo } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useNavigation } from '../providers/NavigationProvider';
import { getComponent, hasComponent } from '../registry/componentRegistry';

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
 * Renders routes dynamically based on navigation configuration.
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
  const { routes, loading } = useNavigation();

  // Build route elements from configuration
  const routeElements = useMemo(() => {
    if (!routes || routes.length === 0) {
      return [];
    }

    return routes.map((route, index) => {
      const { path, component: componentName, exact } = route;

      // Resolve component from registry
      if (!hasComponent(componentName)) {
        console.warn(`[RouteRenderer] Component "${componentName}" not found in registry for route "${path}"`);
        return (
          <Route
            key={`route-${index}-${path}`}
            path={path}
            element={<ComponentNotFound componentName={componentName} />}
          />
        );
      }

      const Component = getComponent(componentName);

      return (
        <Route
          key={`route-${index}-${path}`}
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
    });
  }, [routes, isAuthenticated, onAuthRequired, LoadingFallback]);

  // Show loading state while navigation config is loading
  if (loading) {
    return <LoadingFallback />;
  }

  return (
    <Routes>
      {routeElements}
      {/* Catch-all 404 route */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
};

export default RouteRenderer;
