/**
 * Navigation Provider
 *
 * Loads app-shell config from the backend /api/shell-config endpoint.
 *
 * Navigation is event-driven. The mozaiks-header provides persistent UI
 * (user menu, notifications, admin). Routes are discovered from pages,
 * modules, and components for deep-linking support.
 *
 * @module @mozaiks/chat-ui/providers/NavigationProvider
 */

import React, { createContext, useContext, useState, useEffect, useRef, useMemo } from 'react';

const NavigationContext = createContext(null);

export { NavigationContext };

const DEFAULT_NAVIGATION = {
  version: '1.0.0',
  appName: null,
  appId: null,
  landing_spot: '/',
  pages: [],
  header: { logo: { src: null, wordmark: null, alt: 'App', href: '/' }, pages: [], actions: [] },
  profile: { icon: null, show: true, defaultLabel: 'User', sublabel: null, menu: [] },
  notifications: { icon: null, show: true, emptyText: 'No notifications' },
  footer: { links: [], visible: true },
};

const sortNavigationItems = (items = []) => {
  return [...items].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
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
 * @param {string} props.coreApiUrl - Optional backend base URL for dynamic nav pages
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

      // Resolve backend base URL
      const coreUrl = coreApiUrl || (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL);
      const baseUrl = coreUrl ? coreUrl.replace(/\/+$/, '') : '';

      // 0. Load declarative app-shell config (landing spot, shell UI, route projection).
      try {
        const configUrl = baseUrl ? `${baseUrl}/api/shell-config` : '/api/shell-config';
        const response = await fetch(configUrl);

        if (response.ok) {
          const shellConfig = await response.json();
          staticNav = {
            ...staticNav,
            ...(shellConfig.appName ? { appName: shellConfig.appName } : {}),
            ...(shellConfig.appId ? { appId: shellConfig.appId } : {}),
            entry_point: shellConfig.entry_point,
            chat_startup_mode: shellConfig.chat_startup_mode || 'ask',
            resume_policy: shellConfig.resume_policy,
            ...(shellConfig.pages?.length ? { pages: shellConfig.pages } : {}),
            ...(shellConfig.landing_spot ? { landing_spot: shellConfig.landing_spot } : {}),
            ...(shellConfig.header ? { header: shellConfig.header } : {}),
            ...(shellConfig.profile ? { profile: shellConfig.profile } : {}),
            ...(shellConfig.notifications ? { notifications: shellConfig.notifications } : {}),
            ...(shellConfig.footer ? { footer: shellConfig.footer } : {}),
          };
        } else if (response.status !== 404) {
          throw new Error(`Failed to load shell config: ${response.status}`);
        } else {
          console.log('[NavigationProvider] No shell config found, using defaults');
        }
      } catch (err) {
        console.error('[NavigationProvider] Error loading shell config:', err);
        onErrorRef.current(err);
      }

      setNavigation(staticNav);
      setLoading(false);
      onLoadRef.current(staticNav);

    };

    loadNavigation();
  }, [config, coreApiUrl]);

  // Sorted pages array
  const pages = useMemo(() => {
    return sortNavigationItems(navigation.pages || []);
  }, [navigation.pages]);

  // Header pills are shell-owned; route pages stay independent.
  const headerPages = useMemo(() => {
    return sortNavigationItems(navigation.header?.pages || []);
  }, [navigation.header?.pages]);

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
    appName: navigation.appName || null,
    appId: navigation.appId || null,
    landing_spot: navigation.landing_spot || '/',
    chat_startup_mode: navigation.chat_startup_mode || 'ask',  // "ask" or "workflow"
    entry_point: navigation.entry_point || null,
    pages,
    headerPages,
    header: navigation.header || DEFAULT_NAVIGATION.header,
    profile: navigation.profile || DEFAULT_NAVIGATION.profile,
    notifications: navigation.notifications || DEFAULT_NAVIGATION.notifications,
    footer: navigation.footer || DEFAULT_NAVIGATION.footer,
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
