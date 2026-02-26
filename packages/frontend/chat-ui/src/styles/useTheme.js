// ============================================================================
// FILE: chat-ui/src/styles/useTheme.js
// PURPOSE: React hook for accessing theme configuration in components
// USAGE: const theme = useTheme();
// ============================================================================

import { useState, useEffect } from 'react';
import {
  getTheme,
  DEFAULT_THEME,
  DEFAULT_HEADER_CONFIG,
  DEFAULT_FOOTER_CONFIG,
  DEFAULT_CHAT_CONFIG,
  getCurrentAppId,
} from './themeProvider';

// ─── CSS variable name map ────────────────────────────────────────────────────
// Maps semantic color type + variant → CSS variable name.
// These vars are always set at runtime by applyTheme() / applyBrand().
const COLOR_VAR_MAP = {
  primary:   { main: 'color-primary',       light: 'color-primary-light',   dark: 'color-primary-dark' },
  secondary: { main: 'color-secondary',     light: 'color-secondary-light', dark: 'color-secondary-dark' },
  accent:    { main: 'color-accent',         light: 'color-accent-light',    dark: 'color-accent-dark' },
  success:   { main: 'color-success' },
  warning:   { main: 'color-warning' },
  error:     { main: 'color-error' },
};

/**
 * THEME CONTEXT HOOK
 * Provides access to current theme configuration
 * 
 * @param {string} appId - Optional app ID (defaults to context)
 * @returns {Object} Theme configuration object
 * 
 * @example
 * const { theme } = useTheme();
 * console.log(theme.colors.primary.main); // from brand.json (e.g. '#06b6d4' for the mozaiks template)
 * console.log(theme.fonts.heading.family); // from brand.json (e.g. 'Orbitron')
 */
export function useTheme(appId = null) {
  const [theme, setTheme] = useState(DEFAULT_THEME);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadTheme() {
      try {
        // Get app ID from props or context
        const eid = appId || getCurrentAppId();
        
        console.log(`🎨 [useTheme] Loading theme for app: ${eid}`);
        const loadedTheme = await getTheme(eid);
        
        if (!cancelled) {
          setTheme(loadedTheme);
          setLoading(false);
        }
      } catch (error) {
        console.error('🎨 [useTheme] Failed to load theme:', error);
        if (!cancelled) {
          setTheme(DEFAULT_THEME);
          setLoading(false);
        }
      }
    }

    loadTheme();

    return () => {
      cancelled = true;
    };
  }, [appId]);

  return { theme, loading };
}

/**
 * THEME-AWARE COLOR HELPER
 * Maps semantic color names to actual hex values from theme
 * 
 * @param {Object} theme - Theme object from useTheme()
 * @param {string} colorKey - Semantic color key (e.g., 'primary', 'secondary')
 * @returns {Object} Color values { main, light, dark, name }
 * 
 * @example
 * const theme = useTheme();
 * const primaryColor = getThemeColor(theme, 'primary');
 * <div style={{ backgroundColor: primaryColor.main }}>...</div>
 */
export function getThemeColor(theme, colorKey = 'primary') {
  return theme?.colors?.[colorKey] || DEFAULT_THEME.colors.primary;
}

/**
 * THEME-AWARE FONT HELPER
 * Gets font configuration from theme
 * 
 * @param {Object} theme - Theme object from useTheme()
 * @param {string} fontKey - Font type ('body', 'heading', 'logo')
 * @returns {Object} Font configuration { family, fallbacks, tailwindClass }
 * 
 * @example
 * const theme = useTheme();
 * const headingFont = getThemeFont(theme, 'heading');
 * <h1 className={headingFont.tailwindClass}>Title</h1>
 */
export function getThemeFont(theme, fontKey = 'body') {
  return theme?.fonts?.[fontKey] || DEFAULT_THEME.fonts.body;
}

/**
 * DYNAMIC COLOR CLASS GENERATOR
 * Returns a single Tailwind arbitrary-value class backed by a CSS variable.
 * CSS variables are guaranteed to be set by applyTheme() / applyBrand()
 * before components render, so no competing fallback class is needed.
 *
 * @param {Object} theme - Theme object (not used for class selection, kept for API compat)
 * @param {string} type - Color type ('primary', 'secondary', 'accent', 'success', 'warning', 'error')
 * @param {string} variant - Variant ('main', 'light', 'dark')
 * @param {string} property - CSS property ('bg', 'text', 'border', 'ring', 'shadow')
 * @returns {string} Single Tailwind class string
 *
 * @example
 * const bgClass = getDynamicColorClass(theme, 'primary', 'main', 'bg');
 * // → 'bg-[var(--color-primary)]'
 */
export function getDynamicColorClass(theme, type = 'primary', variant = 'main', property = 'bg') {
  void theme; // CSS vars are set by applyTheme/applyBrand — no theme object lookup needed

  const varName = COLOR_VAR_MAP[type]?.[variant] || COLOR_VAR_MAP[type]?.main || 'color-primary';

  if (property === 'shadow') {
    return `[box-shadow:var(--shadow-${type})]`;
  }

  const propertyMap = {
    bg:     `bg-[var(--${varName})]`,
    text:   `text-[var(--${varName})]`,
    border: `border-[var(--${varName})]`,
    ring:   `ring-[var(--${varName})]`,
  };

  return propertyMap[property] || propertyMap.bg;
}

/**
 * CONFIG ACCESSOR HELPERS
 * Return theme config sections with defaults merged in
 */
export function getHeaderConfig(theme) {
  return { ...DEFAULT_HEADER_CONFIG, ...theme?.header };
}

export function getFooterConfig(theme) {
  return { ...DEFAULT_FOOTER_CONFIG, ...theme?.footer };
}

export function getChatConfig(theme) {
  return { ...DEFAULT_CHAT_CONFIG, ...theme?.chat };
}

export default useTheme;
