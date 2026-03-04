// ============================================================================
// FILE: chat-ui/src/styles/themeProvider.js
// PURPOSE: Dynamic theme system for multi-tenant branding
// USAGE: Theme is always loaded from a source — either the API (app-specific)
//        or the brand.json file (brand-level config). There is no hardcoded
//        default brand. The brand is defined in /brands/{brandId}/brand.json.
// ============================================================================

// ---------------------------------------------------------------------------
// BARE FALLBACK
// Minimal neutral structure used only when BOTH the API and the brand.json
// file fail to load. Contains no brand-specific values — just enough to
// prevent crashes. All CSS variables remain whatever the browser default is.
// ---------------------------------------------------------------------------

const BARE_FALLBACK_THEME = {
  fonts: {
    body:    { family: 'system-ui', fallbacks: 'sans-serif', tailwindClass: 'font-sans' },
    heading: { family: 'system-ui', fallbacks: 'sans-serif', tailwindClass: 'font-sans' },
  },
  colors: {
    primary:    { main: '#3b82f6', light: '#93c5fd', dark: '#1d4ed8', name: 'blue' },
    secondary:  { main: '#6366f1', light: '#a5b4fc', dark: '#4338ca', name: 'indigo' },
    accent:     { main: '#f59e0b', light: '#fbbf24', dark: '#d97706', name: 'amber' },
    success:    { main: '#10b981', light: '#34d399', dark: '#059669', name: 'emerald' },
    warning:    { main: '#f59e0b', light: '#fbbf24', dark: '#d97706', name: 'amber' },
    error:      { main: '#ef4444', light: '#f87171', dark: '#dc2626', name: 'red' },
    background: { base: '#0f172a', surface: '#1e293b', elevated: '#334155', overlay: 'rgba(0,0,0,0.5)' },
    border:     { subtle: '#334155', strong: '#475569', accent: '#3b82f6' },
    text:       { primary: '#f1f5f9', secondary: '#94a3b8', muted: '#64748b', onAccent: '#ffffff' },
  },
  shadows: {
    primary:   '0 20px 45px rgba(59,130,246,0.2)',
    secondary: '0 20px 45px rgba(99,102,241,0.2)',
    accent:    '0 18px 40px rgba(245,158,11,0.2)',
    success:   '0 18px 40px rgba(16,185,129,0.2)',
    warning:   '0 18px 45px rgba(245,158,11,0.2)',
    error:     '0 18px 45px rgba(239,68,68,0.2)',
    elevated:  '0 24px 60px rgba(0,0,0,0.4)',
    focus:     '0 0 0 3px rgba(59,130,246,0.5)',
  },
  branding: {
    name: 'App',
    backgroundImage: '/assets/chat_bg_template.png',
    loadingIcon: null,
  },
  profile: {
    icon:         null,
    show:         true,
    defaultLabel: 'User',
    sublabel:     null,
    menu:         [],
  },
  notifications: {
    icon:      null,
    show:      true,
    emptyText: 'No notifications',
  },
  chat: {
    modes: {
      ask:      { tint: '#3b82f6', label: 'Ask' },
      workflow: { tint: '#6366f1', label: 'Workflow' },
    },
    bubbleRadius: '18px',
  },
  header: {
    logo: { src: null, wordmark: null, alt: 'App', href: '/' },
    actions: [],
  },
  footer: {
    links: [],
    visible: true,
    poweredBy: null,
  },
};

// ---------------------------------------------------------------------------
// Helpers for loading brand.json
// ---------------------------------------------------------------------------

/**
 * Resolve a relative asset filename from a brand config to an absolute public URL.
 * If the value is already an absolute path (/...) or URL (http...) it passes through.
 */
function resolveBrandAsset(basePath, value) {
  if (!value) return null;
  if (value.startsWith('/') || value.startsWith('http')) return value;
  return `${basePath}/${value}`;
}

const HEADER_ICON_FILE_RE = /\.(svg|png|jpe?g|gif|webp|ico)$/i;

function resolveHeaderIconValue(basePath, value) {
  if (!value || typeof value !== 'string') return value ?? null;
  if (value.startsWith('/') || value.startsWith('http')) return value;
  if (HEADER_ICON_FILE_RE.test(value)) return resolveBrandAsset(basePath, value);
  return value;
}

/**
 * Convert brand.json + ui.json into the full theme shape.
 *
 * @param {object} brandConfig  — visual identity (colors, fonts, shadows, assets).
 * @param {object} uiConfig     — UI chrome (header, footer, profile menu, notifications, chat).
 * @param {string} basePath     — absolute URL prefix for relative asset filenames, e.g. '/assets'.
 */
function brandConfigToTheme(brandConfig, uiConfig, basePath) {
  const ui = uiConfig;
  const fallback = BARE_FALLBACK_THEME;

  // --- Visual assets (brand.json) ---
  const rawAssets = brandConfig.assets || {};
  const assets = {
    logo:            resolveBrandAsset(basePath, rawAssets.logo),
    wordmark:        resolveBrandAsset(basePath, rawAssets.wordmark),
    favicon:         resolveBrandAsset(basePath, rawAssets.favicon),
    backgroundImage: resolveBrandAsset(basePath, rawAssets.backgroundImage),
    loadingIcon:     resolveBrandAsset(basePath, rawAssets.loadingIcon),
  };

  // --- UI chrome (ui.json) ---
  const rawProfile       = ui.profile       || {};
  const rawNotifications = ui.notifications || {};
  const rawHeader        = ui.header        || {};
  const rawLogo          = rawHeader.logo   || {};

  const headerActions = Array.isArray(rawHeader.actions)
    ? rawHeader.actions.map((action) => {
        if (!action || typeof action !== 'object') return action;
        return { ...action, icon: resolveHeaderIconValue(basePath, action.icon) };
      })
    : fallback.header.actions;

  const profileIcon       = resolveBrandAsset(basePath, rawProfile.icon)       || null;
  const notificationsIcon = resolveBrandAsset(basePath, rawNotifications.icon) || null;

  // Resolve icons inside profile.menu items
  const profileMenu = Array.isArray(rawProfile.menu)
    ? rawProfile.menu.map((item) =>
        item && item.icon ? { ...item, icon: resolveBrandAsset(basePath, item.icon) } : item
      )
    : fallback.profile.menu;

  // --- Fallback warnings ---
  const _bn = brandConfig.name || 'brand';
  if (!assets.backgroundImage)
    console.warn(`⚠️ [THEME] [${_bn}] assets.backgroundImage not set — using fallback: chat_bg_template.png`);
  if (!assets.logo)
    console.warn(`⚠️ [THEME] [${_bn}] assets.logo not set — header logo will be missing`);
  if (!assets.favicon)
    console.warn(`⚠️ [THEME] [${_bn}] assets.favicon not set — browser tab icon will be missing`);
  if (!assets.wordmark)
    console.warn(`⚠️ [THEME] [${_bn}] assets.wordmark not set — header wordmark will be missing`);
  if (!brandConfig.fonts)
    console.warn(`⚠️ [THEME] [${_bn}] fonts block not configured — using system-ui fallback`);
  if (!brandConfig.colors)
    console.warn(`⚠️ [THEME] [${_bn}] colors block not configured — using bare fallback palette`);
  if (!brandConfig.shadows)
    console.warn(`⚠️ [THEME] [${_bn}] shadows block not configured — using bare fallback shadows`);
  if (!profileIcon)
    console.warn(`⚠️ [THEME] [${_bn}] profile.icon not set — set a filename in ui.json profile.icon or profile button will be hidden`);
  if (!notificationsIcon && rawNotifications.show !== false)
    console.warn(`⚠️ [THEME] [${_bn}] notifications.icon not set — set a filename in ui.json notifications.icon or notification button will be hidden`);

  const headerLogo = {
    src:      resolveBrandAsset(basePath, rawLogo.src) || assets.logo,
    wordmark: resolveBrandAsset(basePath, rawLogo.wordmark) || assets.wordmark,
    alt:      rawLogo.alt  || brandConfig.name || fallback.header.logo.alt,
    href:     rawLogo.href || fallback.header.logo.href,
  };

  return {
    fonts:   brandConfig.fonts   || fallback.fonts,
    colors:  brandConfig.colors  || fallback.colors,
    shadows: brandConfig.shadows || fallback.shadows,
    branding: {
      name:            brandConfig.name            || fallback.branding.name,
      backgroundImage: assets.backgroundImage      || fallback.branding.backgroundImage,
      loadingIcon:     assets.loadingIcon          || fallback.branding.loadingIcon,
      favicon:         assets.favicon,
      logo:            assets.logo,
    },
    profile: {
      icon:         profileIcon                         || fallback.profile.icon,
      show:         rawProfile.show                     ?? fallback.profile.show,
      defaultLabel: rawProfile.defaultLabel             || fallback.profile.defaultLabel,
      sublabel:     rawProfile.sublabel                 ?? fallback.profile.sublabel,
      menu:         profileMenu.length > 0 ? profileMenu : fallback.profile.menu,
    },
    notifications: {
      icon:      notificationsIcon                  || fallback.notifications.icon,
      show:      rawNotifications.show              ?? fallback.notifications.show,
      emptyText: rawNotifications.emptyText         || fallback.notifications.emptyText,
    },
    chat:   ui.chat   || fallback.chat,
    header: {
      logo:    headerLogo,
      actions: headerActions,
    },
    footer: ui.footer || fallback.footer,
    _brandId:  brandConfig._brandId,
    _basePath: basePath,
    _source:   'brand',
  };
}

/**
 * Load and convert /brand.json into a theme object.
 * brand.json is served at the publicDir root (brands/public/brand.json → /brand.json).
 * Images are served from /assets/, fonts from /fonts/.
 */
async function loadThemeFromBrand() {
  const assetsPath = '/assets';
  try {
    // brand.json is required; ui.json is optional and can safely fall back.
    const brandRes = await fetch('/brand.json');
    if (!brandRes.ok) throw new Error(`/brand.json not found (${brandRes.status})`);

    const brandConfig = await brandRes.json();
    let uiConfig = {};

    try {
      const uiRes = await fetch('/ui.json');
      if (uiRes.ok) {
        uiConfig = await uiRes.json();
        console.log('🎛️ [THEME] Loaded brand.json + ui.json');
      } else if (uiRes.status === 404) {
        console.warn('⚠️ [THEME] /ui.json not found — using default UI chrome fallback');
      } else {
        console.warn(`⚠️ [THEME] /ui.json load failed (${uiRes.status}) — using default UI chrome fallback`);
      }
    } catch (uiErr) {
      console.warn('⚠️ [THEME] Could not parse /ui.json — using default UI chrome fallback:', uiErr?.message || uiErr);
    }

    const theme = brandConfigToTheme(brandConfig, uiConfig, assetsPath);
    console.log(`🎨 [THEME] Loaded brand theme: ${brandConfig.name || 'default'}`);
    return { theme, meta: { source: 'brand', appId: 'default' } };
  } catch (err) {
    console.warn('⚠️ [THEME] Could not load brand.json:', err.message);
    return { theme: BARE_FALLBACK_THEME, meta: { source: 'fallback', appId: 'default' } };
  }
}

// ---------------------------------------------------------------------------
// Theme cache + ID helpers
// ---------------------------------------------------------------------------

const themeCache = new Map();
const CURRENT_APP_ID_STORAGE_KEY = 'mozaiks.current_app_id';

// The brand used when no app-specific theme exists.
// Apps can configure this via ChatUIProvider → uiConfig.brandId.
// Brand is determined by the deployment's publicDir; no runtime brand switching.
// setDefaultBrandId kept for API compatibility but is a no-op.
export function setDefaultBrandId(_brandId) { /* no-op: brand is set by deployment */ }

function normalizeAppId(appId) {
  if (appId == null) return 'default';
  if (typeof appId === 'string') { const t = appId.trim(); return t.length > 0 ? t : 'default'; }
  return String(appId) || 'default';
}

function cacheTheme(appId, theme, meta = null) {
  themeCache.set(appId, { theme, meta });
}

// ---------------------------------------------------------------------------
// Theme loaders
// ---------------------------------------------------------------------------

/**
 * Deep-merge utility: overlay values win; nested objects recurse.
 */
function deepMerge(base, overlay) {
  if (!overlay || typeof overlay !== 'object') return base;
  const result = { ...base };
  for (const key of Object.keys(overlay)) {
    if (
      overlay[key] &&
      typeof overlay[key] === 'object' &&
      !Array.isArray(overlay[key]) &&
      base[key] &&
      typeof base[key] === 'object' &&
      !Array.isArray(base[key])
    ) {
      result[key] = deepMerge(base[key], overlay[key]);
    } else {
      result[key] = overlay[key];
    }
  }
  return result;
}

/**
 * Try to fetch platform-level theme overrides from the API.
 * Returns the override theme object or null when unavailable.
 *
 * This only applies in multi-tenant platform mode — when the backend
 * mounts /api/themes (via RUNTIME_PLATFORM_EXTENSIONS).  In local/
 * single-app dev the endpoint won't exist and this returns null.
 */
async function fetchPlatformOverrides(appId) {
  const controller = new AbortController();
  const timeout    = globalThis.setTimeout(() => controller.abort(), 4000);

  try {
    const response = await fetch(`/api/themes/${encodeURIComponent(appId)}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });

    if (!response.ok) return null;   // 404 / 503 / etc — no overrides

    const data  = await response.json();
    const theme = data?.theme;

    if (!theme || typeof theme !== 'object') return null;

    return {
      theme,
      meta: {
        source:    data?.source    || 'api',
        appId:     data?.app_id    || appId,
        updatedAt: data?.updatedAt || null,
        updatedBy: data?.updatedBy || null,
      },
    };
  } catch {
    // Network error, timeout, JSON parse failure — all expected when
    // the platform API isn't available. Silently return null.
    return null;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Load the theme for a given app.
 *
 * Load order:
 *   1. brand.json + ui.json  — always the base (local, declarative, user-editable).
 *   2. /api/themes/{appId}   — platform overrides merged on top (multi-tenant only).
 *
 * This guarantees brand.json is the source of truth in local dev.  In
 * platform mode the API can overlay tenant-specific customisation on top
 * without wiping out the full brand definition.
 */
export async function getTheme(appId = 'default') {
  const normalizedId = normalizeAppId(appId);
  if (themeCache.has(normalizedId)) {
    return themeCache.get(normalizedId).theme;
  }

  // 1. brand.json + ui.json — always load as the base
  const brand = await loadThemeFromBrand();
  let   theme = brand.theme;
  let   meta  = brand.meta;

  // 2. Platform API overrides (only when the endpoint exists)
  const overrides = await fetchPlatformOverrides(normalizedId);
  if (overrides) {
    theme = deepMerge(theme, overrides.theme);
    meta  = { ...meta, ...overrides.meta, source: 'brand+api' };
    console.log(`🎨 [THEME] Platform overrides merged for app: ${normalizedId}`);
  }

  cacheTheme(normalizedId, theme, meta);
  return theme;
}

export function getThemeMetadata(appId = 'default') {
  const cached = themeCache.get(normalizeAppId(appId));
  return cached?.meta || null;
}

export function clearThemeCache(appId = null) {
  if (!appId) { themeCache.clear(); return; }
  themeCache.delete(normalizeAppId(appId));
}

export function getCurrentAppId() {
  try {
    const stored = localStorage.getItem(CURRENT_APP_ID_STORAGE_KEY);
    if (!stored) return 'default';
    const t = stored.trim();
    return t.length > 0 ? t : 'default';
  } catch (_) { return 'default'; }
}

export async function initializeTheme(appId = 'default') {
  const normalizedId = normalizeAppId(appId);
  try { localStorage.setItem(CURRENT_APP_ID_STORAGE_KEY, normalizedId); } catch (_) {}
  const theme = await getTheme(normalizedId);
  applyTheme(theme);
  return theme;
}

// ---------------------------------------------------------------------------
// applyTheme — injects CSS variables + loads fonts
// ---------------------------------------------------------------------------

export function applyTheme(theme) {
  try {
    const t = theme || BARE_FALLBACK_THEME;
    console.log(`🎨 [THEME] Applying theme: ${t.branding?.name || t.name || 'custom'}`);

    // Fonts
    const fonts = t.fonts || BARE_FALLBACK_THEME.fonts;
    Object.values(fonts).forEach((font) => {
      if (font?.googleFont && !font.localFont) loadGoogleFont(font.googleFont);
    });

    // CSS variables
    updateCSSVariables(t.colors, t.shadows, t.chat);

    // Branding
    const branding = t.branding || {};
    if (branding.name) document.title = branding.name;
    if (branding.favicon) updateFavicon(branding.favicon);

    // Asset CSS variables (used by components via var() references)
    const root = document.documentElement;
    if (branding.backgroundImage) {
      root.style.setProperty('--brand-bg-url', `url("${branding.backgroundImage}")`);
    } else {
      console.warn('⚠️ [THEME] branding.backgroundImage is empty after resolution — --brand-bg-url will not be set');
      root.style.removeProperty('--brand-bg-url');
    }
    if (branding.logo)            root.style.setProperty('--brand-logo-url', branding.logo);
    if (t.profile?.icon) root.style.setProperty('--brand-profile-icon-url', t.profile.icon);

    console.log('✅ [THEME] Theme applied');
  } catch (err) {
    console.error('❌ [THEME] Error applying theme:', err);
  }
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function loadGoogleFont(fontUrl) {
  if (document.querySelector(`link[href="${fontUrl}"]`)) return;
  const link = document.createElement('link');
  link.rel  = 'stylesheet';
  link.href = fontUrl;
  document.head.appendChild(link);
}

function updateFavicon(faviconUrl) {
  let link = document.querySelector("link[rel~='icon']");
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
  link.href = faviconUrl;
}

function hexToRgb(hex) {
  if (!hex || typeof hex !== 'string') return null;
  const n = hex.replace('#', '').trim();
  if (![3, 6].includes(n.length)) return null;
  const expanded = n.length === 3 ? n.split('').map((c) => `${c}${c}`).join('') : n;
  const int = Number.parseInt(expanded, 16);
  if (Number.isNaN(int)) return null;
  return `${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255}`;
}

function setColorVar(root, name, value, fallback) {
  const resolved = (value || fallback || '').trim();
  if (!resolved) return;
  root.style.setProperty(`--${name}`, resolved);
  const rgb = hexToRgb(resolved);
  if (rgb) root.style.setProperty(`--${name}-rgb`, rgb);
}

function setShadowVar(root, name, value, fallback) {
  const resolved = value || fallback;
  if (resolved) root.style.setProperty(`--${name}`, resolved);
}

function updateCSSVariables(themeColors, themeShadows, themeChat) {
  const root    = document.documentElement;
  const colors  = themeColors  || BARE_FALLBACK_THEME.colors;
  const shadows = themeShadows || BARE_FALLBACK_THEME.shadows;
  const chat    = themeChat    || BARE_FALLBACK_THEME.chat;
  const fb      = BARE_FALLBACK_THEME;

  setColorVar(root, 'color-primary',       colors.primary?.main,    fb.colors.primary.main);
  setColorVar(root, 'color-primary-light', colors.primary?.light,   fb.colors.primary.light);
  setColorVar(root, 'color-primary-dark',  colors.primary?.dark,    fb.colors.primary.dark);

  setColorVar(root, 'color-secondary',       colors.secondary?.main,  fb.colors.secondary.main);
  setColorVar(root, 'color-secondary-light', colors.secondary?.light, fb.colors.secondary.light);
  setColorVar(root, 'color-secondary-dark',  colors.secondary?.dark,  fb.colors.secondary.dark);

  setColorVar(root, 'color-accent',       colors.accent?.main,  fb.colors.accent.main);
  setColorVar(root, 'color-accent-light', colors.accent?.light, fb.colors.accent.light);
  setColorVar(root, 'color-accent-dark',  colors.accent?.dark,  fb.colors.accent.dark);

  setColorVar(root, 'color-success', colors.success?.main, fb.colors.success.main);
  setColorVar(root, 'color-warning', colors.warning?.main, fb.colors.warning.main);
  setColorVar(root, 'color-error',   colors.error?.main,   fb.colors.error.main);

  setColorVar(root, 'color-background',  colors.background?.base,     fb.colors.background.base);
  setColorVar(root, 'color-surface',     colors.background?.surface,  fb.colors.background.surface);
  setColorVar(root, 'color-surface-alt', colors.background?.elevated, fb.colors.background.elevated);
  const overlay = colors.background?.overlay || fb.colors.background.overlay;
  if (overlay) root.style.setProperty('--color-surface-overlay', overlay);

  setColorVar(root, 'color-border-subtle', colors.border?.subtle, fb.colors.border.subtle);
  setColorVar(root, 'color-border-strong', colors.border?.strong, fb.colors.border.strong);
  setColorVar(root, 'color-border-accent', colors.border?.accent, fb.colors.border.accent);

  setColorVar(root, 'color-text-primary',   colors.text?.primary,   fb.colors.text.primary);
  setColorVar(root, 'color-text-secondary', colors.text?.secondary, fb.colors.text.secondary);
  setColorVar(root, 'color-text-muted',     colors.text?.muted,     fb.colors.text.muted);
  setColorVar(root, 'color-text-on-accent', colors.text?.onAccent,  fb.colors.text.onAccent);

  // Alias tokens
  setColorVar(root, 'color-card',   colors.background?.surface, fb.colors.background.surface);
  setColorVar(root, 'color-border', colors.border?.subtle,      fb.colors.border.subtle);
  setColorVar(root, 'color-dark',   colors.background?.base,    fb.colors.background.base);
  setColorVar(root, 'color-light',  colors.text?.primary,       fb.colors.text.primary);

  // Core primitive tokens
  setColorVar(root, 'core-primitive-surface',     colors.background?.surface,  fb.colors.background.surface);
  setColorVar(root, 'core-primitive-surface-alt', colors.background?.elevated, fb.colors.background.elevated);
  setColorVar(root, 'core-primitive-border',      colors.border?.subtle,       fb.colors.border.subtle);
  setColorVar(root, 'core-primitive-text',        colors.text?.primary,        fb.colors.text.primary);
  setColorVar(root, 'core-primitive-muted',       colors.text?.muted,          fb.colors.text.muted);
  setColorVar(root, 'core-primitive-accent',      colors.primary?.main,        fb.colors.primary.main);
  setShadowVar(root, 'core-primitive-shadow',     shadows?.elevated,           fb.shadows.elevated);
  root.style.setProperty('--core-primitive-radius', '16px');

  // Shadow tokens
  setShadowVar(root, 'shadow-primary',   shadows?.primary,   fb.shadows.primary);
  setShadowVar(root, 'shadow-secondary', shadows?.secondary, fb.shadows.secondary);
  setShadowVar(root, 'shadow-accent',    shadows?.accent,    fb.shadows.accent);
  setShadowVar(root, 'shadow-success',   shadows?.success,   fb.shadows.success);
  setShadowVar(root, 'shadow-warning',   shadows?.warning,   fb.shadows.warning);
  setShadowVar(root, 'shadow-error',     shadows?.error,     fb.shadows.error);
  setShadowVar(root, 'shadow-elevated',  shadows?.elevated,  fb.shadows.elevated);
  setShadowVar(root, 'shadow-focus',     shadows?.focus,     fb.shadows.focus);

  // Chat mode tints
  setColorVar(root, 'chat-mode-ask-tint',      chat?.modes?.ask?.tint,      fb.chat.modes.ask.tint);
  setColorVar(root, 'chat-mode-workflow-tint', chat?.modes?.workflow?.tint, fb.chat.modes.workflow.tint);
  const bubbleRadius = chat?.bubbleRadius || fb.chat.bubbleRadius;
  if (bubbleRadius) root.style.setProperty('--chat-bubble-radius', bubbleRadius);
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

// Structural-only fallback — no brand data baked in.
export { BARE_FALLBACK_THEME };

// Structural fallback — no brand data baked in. Actual values come from brand.json + ui.json at runtime.
export const DEFAULT_THEME = BARE_FALLBACK_THEME;

export const DEFAULT_HEADER_CONFIG = BARE_FALLBACK_THEME.header;
export const DEFAULT_FOOTER_CONFIG = BARE_FALLBACK_THEME.footer;
export const DEFAULT_CHAT_CONFIG   = BARE_FALLBACK_THEME.chat;
