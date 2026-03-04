/**
 * Navigation Provider
 *
 * Loads navigation.json and provides it to the Shell via React Context.
 *
 * navigation.json uses a flat "pages" array. Each page can set
 * `showInHeader: true` to appear as a header pill on wider screens —
 * but ALL pages always appear in the Discover dropdown regardless of
 * screen size.
 *
 * Core routes (ChatPage, AdminPortal) are hardcoded in RouteRenderer —
 * navigation.json only defines EXTRA pages beyond the core shell.
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
 * @param {Object} props.config - Static navigation config (skips JSON fetch)
 * @param {string} props.configPath - Path to navigation.json (default: '/navigation.json')
 * @param {Function} props.onLoad - Callback when navigation is loaded
 * @param {Function} props.onError - Callback on loading error
 */
export const NavigationProvider = ({
  children,
  config = null,
  configPath = '/navigation.json',
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
      try {
        const response = await fetch(configPath);

        if (!response.ok) {
          if (response.status === 404) {
            console.log('[NavigationProvider] No navigation.json found, using defaults');
            setNavigation(DEFAULT_NAVIGATION);
            setLoading(false);
            onLoadRef.current(DEFAULT_NAVIGATION);
            return;
          }
          throw new Error(`Failed to load navigation config: ${response.status}`);
        }

        const navConfig = await response.json();
        setNavigation(navConfig);
        setLoading(false);
        onLoadRef.current(navConfig);

        if (process.env.NODE_ENV === 'development') {
          console.log('[NavigationProvider] Loaded navigation config:', navConfig);
        }
      } catch (err) {
        console.error('[NavigationProvider] Error loading navigation:', err);
        setError(err);
        setNavigation(DEFAULT_NAVIGATION);
        setLoading(false);
        onErrorRef.current(err);
      }
    };

    loadNavigation();
  }, [config, configPath]);

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
