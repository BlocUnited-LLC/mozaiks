/**
 * Navigation Provider
 *
 * Loads navigation config from the mozaikscore /api/navigation-config endpoint
 * (backed by app/config/navigation_config.json) and provides it to the Shell
 * via React Context.
 *
 * The config uses a flat "pages" array. Each page can set
 * `showInHeader: true` to appear as a header pill on wider screens —
 * but ALL pages always appear in the Discover dropdown regardless of
 * screen size.
 *
 * Core routes (ChatPage, AdminPortal, SettingsPage, NotificationsPage)
 * are hardcoded in RouteRenderer — the config only defines
 * EXTRA pages beyond the core shell.
 *
 * When `coreApiUrl` is provided (or VITE_CORE_URL is set), the provider
 * also fetches `/api/navigation` from mozaikscore and merges those pages
 * into the navigation context (static pages take precedence on path
 * collision).
 *
 * @module @mozaiks/chat-ui/providers/NavigationProvider
 */

import React, { createContext, useContext, useState, useEffect, useRef, useMemo } from 'react';

const NavigationContext = createContext(null);

export { NavigationContext };

const DEFAULT_NAVIGATION = {
  version: '1.0.0',
  landing_spot: '/',
  pages: [],
};

/**
 * Hook to access navigation configuration.
 * @returns {Object} Navigation context value
 */
export const useNavigation = () => {
  const context = useContext(NavigationContext);
  if (!context) {
    throw new Error('useNavigation must be used within a NavigationProvider');
  }
  return context;
};

/**
 * NavigationProvider Component
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children
 * @param {Object} props.config - Static navigation config (skips API fetch)
 * @param {string} props.coreApiUrl - Optional mozaikscore base URL for dynamic nav pages
 * @param {Function} props.onLoad - Callback when navigation is loaded
 * @param {Function} props.onError - Callback on loading error
 */
export const NavigationProvider = ({
  children,
  config = null,
  coreApiUrl = null,
  onLoad = () => {},
  onError = () => {}
}) => {
  const [navigation, setNavigation] = useState(config || DEFAULT_NAVIGATION);
  const [loading, setLoading] = useState(!config);
  const [error, setError] = useState(null);

  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    if (config) {
      setNavigation(config);
      setLoading(false);
      onLoadRef.current(config);
      return;
    }

    const loadNavigation = async () => {
      let staticNav = DEFAULT_NAVIGATION;

      // Resolve mozaikscore base URL
      const coreUrl = coreApiUrl || (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL);
      const baseUrl = coreUrl ? coreUrl.replace(/\/+$/, '') : '';

      // 1. Load declarative navigation_config.json from mozaikscore API
      try {
        const configUrl = baseUrl ? `${baseUrl}/api/navigation-config` : '/api/navigation-config';
        const response = await fetch(configUrl);

        if (response.ok) {
          staticNav = await response.json();
        } else if (response.status !== 404) {
          throw new Error(`Failed to load navigation config: ${response.status}`);
        } else {
          console.log('[NavigationProvider] No navigation config found, using defaults');
        }
      } catch (err) {
        console.error('[NavigationProvider] Error loading navigation config:', err);
        onErrorRef.current(err);
      }

      // 1b. Convert modules[] from static config into pages[] entries.
      // Each module entry needs path + component to be routable.
      const configModules = staticNav.modules || [];
      if (configModules.length > 0) {
        const existingPaths = new Set((staticNav.pages || []).map((p) => p.path));
        const modulePages = configModules
          .filter((m) => m.path && m.component && !existingPaths.has(m.path))
          .map((m) => ({
            path: m.path,
            component: m.component,
            label: m.label || m.module_name,
            icon: m.icon || 'puzzle',
            order: m.order ?? 50,
            showInHeader: m.showInHeader || false,
            meta: m.meta || { title: m.label || m.module_name, requiresAuth: true },
          }));
        if (modulePages.length > 0) {
          staticNav = {
            ...staticNav,
            pages: [...(staticNav.pages || []), ...modulePages],
          };
        }
      }

      // 2. Optionally fetch filtered navigation entries from mozaikscore
      if (baseUrl || coreUrl) {
        const mergeBaseUrl = baseUrl || '';

        // 2a. Merge explicit navigation entries from core
        try {
          const coreRes = await fetch(`${mergeBaseUrl}/api/navigation`);
          if (coreRes.ok) {
            const coreNav = await coreRes.json();
            const corePages = coreNav?.navigation || coreNav?.pages || coreNav?.default || [];
            if (corePages.length > 0) {
              const existingPaths = new Set((staticNav.pages || []).map((p) => p.path));
              const newPages = corePages
                .filter((p) => p.path && !existingPaths.has(p.path))
                .map((p) => ({
                  ...p,
                  // Map module nav entries to the dynamic ModulePage route
                  path: p.path || `/modules/${p.module_name || p.plugin_name}`,
                  component: p.component || 'ModulePage',
                }));
              staticNav = {
                ...staticNav,
                pages: [...(staticNav.pages || []), ...newPages],
              };
              if (process.env.NODE_ENV === 'development') {
                console.log('[NavigationProvider] Merged %d core nav pages', newPages.length);
              }
            }
          }
        } catch (coreErr) {
          console.log('[NavigationProvider] mozaikscore navigation unavailable:', coreErr.message);
        }

        // 2b. Auto-discover enabled modules from API — only add those that define
        // their own component (i.e. have a full-page UI registered via @modules).
        try {
          const modsRes = await fetch(`${mergeBaseUrl}/api/available-modules`);
          if (modsRes.ok) {
            const modsData = await modsRes.json();
            const modules = modsData?.modules || [];
            const existingPaths = new Set((staticNav.pages || []).map((p) => p.path));
            const modulePages = modules
              .filter((m) => m.enabled !== false && m.component)
              .map((m) => ({
                path: m.path || `/modules/${m.name}`,
                component: m.component,
                label: m.display_name || m.name,
                icon: m.icon || 'puzzle',
                order: 50,
                showInHeader: false,
                meta: { title: m.display_name || m.name, requiresAuth: true },
              }))
              .filter((p) => !existingPaths.has(p.path));

            if (modulePages.length > 0) {
              staticNav = {
                ...staticNav,
                pages: [...(staticNav.pages || []), ...modulePages],
              };
              if (process.env.NODE_ENV === 'development') {
                console.log('[NavigationProvider] Auto-discovered %d module pages', modulePages.length);
              }
            }
          }
        } catch (modsErr) {
          console.log('[NavigationProvider] Module discovery unavailable:', modsErr.message);
        }
      }

      setNavigation(staticNav);
      setLoading(false);
      onLoadRef.current(staticNav);

      if (process.env.NODE_ENV === 'development') {
        console.log('[NavigationProvider] Loaded navigation config:', staticNav);
      }
    };

    loadNavigation();
  }, [config, coreApiUrl]);

  // Sorted pages array
  const pages = useMemo(() => {
    const raw = navigation.pages || [];
    return [...raw].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  }, [navigation.pages]);

  // Pages flagged to show as header pills on wider screens
  const headerPages = useMemo(() => {
    return pages.filter((p) => p.showInHeader === true);
  }, [pages]);

  const findPage = (path) => pages.find((p) => p.path === path) || null;

  const pageRequiresAuth = (path) => {
    const page = findPage(path);
    return page?.meta?.requiresAuth !== false;
  };

  const contextValue = {
    navigation,
    loading,
    error,
    version: navigation.version,
    landing_spot: navigation.landing_spot || '/',
    startup_mode: navigation.startup_mode || null,
    pages,
    headerPages,
    findPage,
    pageRequiresAuth,
  };

  return (
    <NavigationContext.Provider value={contextValue}>
      {children}
    </NavigationContext.Provider>
  );
};

export default NavigationProvider;
