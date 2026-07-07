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
  profile: {
    icon: null,
    show: true,
    defaultLabel: 'User',
    sublabel: null,
    menu: [
      { id: 'profile', label: 'Account', action: 'navigate', path: '/profile' },
    ],
  },
  notifications: { icon: null, show: true, path: '/notifications', emptyText: 'No unread notifications' },
  footer: { links: [], visible: true, hideOnMobile: false },
  mobile: { bottomBar: { visible: 'auto', items: [] } },
  chrome: {
    defaultMode: 'standard',
    modes: {
      standard: {
        desktop: { header: true, footer: true, bottomBar: false, localNav: true },
        mobile: { header: true, footer: false, bottomBar: true, localNav: 'sheet' },
      },
      workspace: {
        desktop: { header: true, footer: false, bottomBar: false, localNav: true },
        mobile: { header: true, footer: false, bottomBar: true, localNav: 'sheet' },
      },
      conversation: {
        desktop: { header: true, footer: false, bottomBar: false, localNav: false },
        mobile: { header: true, footer: false, bottomBar: false, localNav: false },
      },
      focused: {
        desktop: { header: true, footer: false, bottomBar: false, localNav: false },
        mobile: { header: true, footer: false, bottomBar: false, localNav: false },
      },
      immersive: {
        desktop: { header: false, footer: false, bottomBar: false, localNav: false },
        mobile: { header: false, footer: false, bottomBar: false, localNav: false },
      },
      public: {
        desktop: { header: true, footer: true, bottomBar: false, localNav: false },
        mobile: { header: true, footer: false, bottomBar: false, localNav: false },
      },
    },
  },
  navigation: {
    policy: {
      desktop: { global: 'header', local: 'sidebar', footer: 'visible' },
      mobile: { global: 'bottomBar', local: 'sheet', footer: 'hidden' },
      maxMobileItems: 5,
      autoFromPages: false,
    },
    items: [],
    resolved: {
      desktop: { header: [], sidebar: [], rail: [] },
      mobile: { bottomBar: [], sheet: [], more: [] },
      local: { desktop: [], mobile: [] },
      profile: [],
      footer: [],
    },
  },
};

const sortNavigationItems = (items = []) => {
  return [...items].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
};

const normalizeHeaderConfig = (header = {}) => ({
  ...DEFAULT_NAVIGATION.header,
  ...header,
  logo: {
    ...DEFAULT_NAVIGATION.header.logo,
    ...(header.logo || {}),
  },
  pages: Array.isArray(header.pages) ? header.pages : DEFAULT_NAVIGATION.header.pages,
  actions: Array.isArray(header.actions) ? header.actions : DEFAULT_NAVIGATION.header.actions,
});

const normalizeProfileConfig = (profile = {}) => ({
  ...DEFAULT_NAVIGATION.profile,
  ...profile,
  menu: Array.isArray(profile.menu) ? profile.menu : DEFAULT_NAVIGATION.profile.menu,
});

const normalizeNotificationsConfig = (notifications = {}) => ({
  ...DEFAULT_NAVIGATION.notifications,
  ...notifications,
});

const normalizeFooterConfig = (footer = {}) => ({
  ...DEFAULT_NAVIGATION.footer,
  ...footer,
  links: Array.isArray(footer.links) ? footer.links : DEFAULT_NAVIGATION.footer.links,
});

const normalizeMobileConfig = (mobile = {}) => {
  const bottomBar = mobile?.bottomBar && typeof mobile.bottomBar === 'object' ? mobile.bottomBar : {};
  return {
    ...DEFAULT_NAVIGATION.mobile,
    ...mobile,
    bottomBar: {
      ...DEFAULT_NAVIGATION.mobile.bottomBar,
      ...bottomBar,
      items: Array.isArray(bottomBar.items) ? bottomBar.items : DEFAULT_NAVIGATION.mobile.bottomBar.items,
    },
  };
};

const normalizeNavigationModel = (navigation = {}) => {
  const policy = navigation?.policy && typeof navigation.policy === 'object' ? navigation.policy : {};
  const resolved = navigation?.resolved && typeof navigation.resolved === 'object' ? navigation.resolved : {};
  return {
    ...DEFAULT_NAVIGATION.navigation,
    ...navigation,
    policy: {
      ...DEFAULT_NAVIGATION.navigation.policy,
      ...policy,
      desktop: {
        ...DEFAULT_NAVIGATION.navigation.policy.desktop,
        ...(policy.desktop || {}),
      },
      mobile: {
        ...DEFAULT_NAVIGATION.navigation.policy.mobile,
        ...(policy.mobile || {}),
      },
    },
    items: Array.isArray(navigation?.items) ? navigation.items : DEFAULT_NAVIGATION.navigation.items,
    resolved: {
      ...DEFAULT_NAVIGATION.navigation.resolved,
      ...resolved,
      desktop: {
        ...DEFAULT_NAVIGATION.navigation.resolved.desktop,
        ...(resolved.desktop || {}),
      },
      mobile: {
        ...DEFAULT_NAVIGATION.navigation.resolved.mobile,
        ...(resolved.mobile || {}),
      },
      local: {
        ...DEFAULT_NAVIGATION.navigation.resolved.local,
        ...(resolved.local || {}),
      },
      profile: Array.isArray(resolved.profile) ? resolved.profile : DEFAULT_NAVIGATION.navigation.resolved.profile,
      footer: Array.isArray(resolved.footer) ? resolved.footer : DEFAULT_NAVIGATION.navigation.resolved.footer,
    },
  };
};

const normalizeChromeViewport = (viewport = {}, defaults = {}) => ({
  ...defaults,
  ...(viewport && typeof viewport === 'object' ? viewport : {}),
});

const normalizeChromeMode = (mode = {}, defaults = {}) => ({
  desktop: normalizeChromeViewport(mode?.desktop, defaults.desktop),
  mobile: normalizeChromeViewport(mode?.mobile, defaults.mobile),
});

const normalizeChromeConfig = (chrome = {}) => {
  const source = chrome && typeof chrome === 'object' ? chrome : {};
  const modes = source.modes && typeof source.modes === 'object' ? source.modes : {};
  const defaultModes = DEFAULT_NAVIGATION.chrome.modes;
  const normalizedModes = Object.fromEntries(
    Object.entries(defaultModes).map(([mode, defaults]) => [
      mode,
      normalizeChromeMode(modes[mode], defaults),
    ])
  );
  const requestedDefault = typeof source.defaultMode === 'string' ? source.defaultMode : null;
  return {
    ...DEFAULT_NAVIGATION.chrome,
    ...source,
    defaultMode: normalizedModes[requestedDefault] ? requestedDefault : DEFAULT_NAVIGATION.chrome.defaultMode,
    modes: normalizedModes,
  };
};

const normalizeNavigationConfig = (navigation = {}) => ({
  ...DEFAULT_NAVIGATION,
  ...navigation,
  pages: Array.isArray(navigation.pages) ? navigation.pages : DEFAULT_NAVIGATION.pages,
  header: normalizeHeaderConfig(navigation.header),
  profile: normalizeProfileConfig(navigation.profile),
  notifications: normalizeNotificationsConfig(navigation.notifications),
  footer: normalizeFooterConfig(navigation.footer),
  mobile: normalizeMobileConfig(navigation.mobile),
  chrome: normalizeChromeConfig(navigation.chrome),
  navigation: normalizeNavigationModel(navigation.navigation),
});

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
  const [navigation, setNavigation] = useState(() => normalizeNavigationConfig(config || DEFAULT_NAVIGATION));
  const [loading, setLoading] = useState(!config);
  const [error, setError] = useState(null);

  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    if (config) {
      const normalized = normalizeNavigationConfig(config);
      setNavigation(normalized);
      setLoading(false);
      onLoadRef.current(normalized);
      return;
    }

    const loadNavigation = async () => {
      let staticNav = { ...DEFAULT_NAVIGATION };

      // Resolve backend base URL
      const coreUrl = coreApiUrl || (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL);
      const baseUrl = coreUrl ? coreUrl.replace(/\/+$/, '') : '';

      // 0. Load declarative app-shell config (landing spot, shell UI, route projection).
      try {
        const configUrl = baseUrl ? `${baseUrl}/api/shell-config` : '/api/shell-config';
        const response = await fetch(configUrl);

        if (response.ok) {
          const shellConfig = await response.json();
          staticNav = normalizeNavigationConfig({
            ...staticNav,
            ...(shellConfig.appName ? { appName: shellConfig.appName } : {}),
            ...(shellConfig.appId ? { appId: shellConfig.appId } : {}),
            entry_point: shellConfig.entry_point,
            chat_startup_mode: shellConfig.chat_startup_mode || 'ask',
            ...(shellConfig.pages?.length ? { pages: shellConfig.pages } : {}),
            ...(shellConfig.landing_spot ? { landing_spot: shellConfig.landing_spot } : {}),
            ...(shellConfig.header ? { header: shellConfig.header } : {}),
            ...(shellConfig.profile ? { profile: shellConfig.profile } : {}),
            ...(shellConfig.notifications ? { notifications: shellConfig.notifications } : {}),
            ...(shellConfig.footer ? { footer: shellConfig.footer } : {}),
            ...(shellConfig.mobile ? { mobile: shellConfig.mobile } : {}),
            ...(shellConfig.chrome ? { chrome: shellConfig.chrome } : {}),
            ...(shellConfig.navigation ? { navigation: shellConfig.navigation } : {}),
          });
        } else if (response.status !== 404) {
          throw new Error(`Failed to load shell config: ${response.status}`);
        } else {
        }
      } catch (err) {
        console.error('[NavigationProvider] Error loading shell config:', err);
        onErrorRef.current(err);
      }

      const normalized = normalizeNavigationConfig(staticNav);
      setNavigation(normalized);
      setLoading(false);
      onLoadRef.current(normalized);

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
    header: navigation.header,
    profile: navigation.profile,
    notifications: navigation.notifications,
    footer: navigation.footer,
    mobile: navigation.mobile,
    chrome: navigation.chrome,
    navigationModel: navigation.navigation,
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
