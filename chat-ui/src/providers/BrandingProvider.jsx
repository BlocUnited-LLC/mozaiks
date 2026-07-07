/**
 * Branding Provider
 *
 * Loads the unified theme contract from styles/themeProvider and exposes it
 * through the branding context consumed by chat-ui.
 */

import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import {
  getTheme as loadUnifiedTheme,
  applyTheme,
  DEFAULT_THEME,
  getCurrentAppId,
} from '../styles/themeProvider';

const BrandingContext = createContext(null);
const DEFAULT_BRANDING = DEFAULT_THEME;

export const useBranding = () => {
  const context = useContext(BrandingContext);
  if (!context) {
    throw new Error('useBranding must be used within a BrandingProvider');
  }
  return context;
};

export const BrandingProvider = ({
  children,
  config = null,
  onLoad = () => {},
  onError = () => {},
}) => {
  const [branding, setBranding] = useState(config || DEFAULT_BRANDING);
  const [loading, setLoading] = useState(!config);
  const [error, setError] = useState(null);

  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    let cancelled = false;

    if (config) {
      setBranding(config);
      setLoading(false);
      onLoadRef.current(config);
      applyTheme(config);
      return () => {
        cancelled = true;
      };
    }

    const loadBranding = async () => {
      try {
        const appId = getCurrentAppId();
        const unifiedTheme = await loadUnifiedTheme(appId);
        if (cancelled) return;

        setBranding(unifiedTheme);
        setLoading(false);
        onLoadRef.current(unifiedTheme);
        applyTheme(unifiedTheme);

        if (process.env.NODE_ENV === 'development') {
        }
      } catch (err) {
        if (cancelled) return;
        console.error('[BrandingProvider] Error loading theme:', err);
        setError(err);
        setBranding(DEFAULT_BRANDING);
        setLoading(false);
        onErrorRef.current(err);
        applyTheme(DEFAULT_BRANDING);
      }
    };

    loadBranding();

    return () => {
      cancelled = true;
    };
  }, [config]);

  const getAppInfo = () => ({
    name: branding?.branding?.name || 'Mozaiks',
    logo: branding?.branding?.logo || null,
    favicon: branding?.branding?.favicon || null,
  });

  const getThemeConfig = () => branding || {};
  const getLayout = () => ({});

  const setThemeMode = () => {};

  const contextValue = {
    branding,
    loading,
    error,
    version: '2.0.0',
    getAppInfo,
    getTheme: getThemeConfig,
    getLayout,
    setThemeMode,
    appName: branding?.branding?.name || 'Mozaiks',
    appTagline: '',
    logo: branding?.branding?.logo || null,
    theme: branding || {},
    layout: {},
  };

  return (
    <BrandingContext.Provider value={contextValue}>
      {children}
    </BrandingContext.Provider>
  );
};

export default BrandingProvider;
