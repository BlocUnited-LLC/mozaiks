// ============================================================================
// BRAND LOADER
// ============================================================================
// brand.json served at /brand.json (brands/public/ is the Vite publicDir)
// Images served at /assets/{file}, fonts at /fonts/{file}
// ============================================================================

const BRAND_JSON_URL = '/brand.json';
const ASSETS_PATH    = '/assets';

/**
 * Load the active brand config.
 * brand.json lives at the publicDir root → served at /brand.json.
 * Asset filenames declared in brand.json are resolved under /assets/.
 */
export async function loadBrand() {
  // Load brand.json
  const response = await fetch(BRAND_JSON_URL);
  if (!response.ok) {
    throw new Error(`brand.json not found (${response.status})`);
  }

  const config = await response.json();

  // Resolve assets — bare filenames are resolved under /assets/
  const declared = config.assets || {};
  const logoFile   = declared.logo             || 'logo.svg';
  const fgFile     = declared.wordmark         || null;
  const faviconFile= declared.favicon          || 'favicon.ico';
  const bgFile     = declared.backgroundImage  || null;
  const loadingFile= declared.loadingIcon      || null;
  const profileFile= declared.profileFallback  || null;

  function resolveAsset(file) { return file ? `${ASSETS_PATH}/${file}` : null; }

  const assets = {
    logo:            resolveAsset(logoFile),
    wordmark:        fgFile && await assetExists(`${ASSETS_PATH}/${fgFile}`)       ? `${ASSETS_PATH}/${fgFile}`        : null,
    favicon:         await assetExists(`${ASSETS_PATH}/${faviconFile}`)             ? `${ASSETS_PATH}/${faviconFile}`   : resolveAsset(logoFile),
    backgroundImage: bgFile && await assetExists(`${ASSETS_PATH}/${bgFile}`)       ? `${ASSETS_PATH}/${bgFile}`        : null,
    loadingIcon:     loadingFile && await assetExists(`${ASSETS_PATH}/${loadingFile}`) ? `${ASSETS_PATH}/${loadingFile}` : null,
    profileFallback: profileFile && await assetExists(`${ASSETS_PATH}/${profileFile}`) ? `${ASSETS_PATH}/${profileFile}` : null,
  };

  return {
    ...config,
    assets,
    _basePath: ASSETS_PATH,
  };
}

/**
 * Check if an asset exists
 */
async function assetExists(path) {
  try {
    const response = await fetch(path, { method: 'HEAD' });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Apply brand.json data to the page — sets ALL CSS variables consumed by
 * the design system (colors, surfaces, borders, text, shadows, fonts, assets).
 *
 * brand.json color fields are objects { main, light, dark } so we always
 * read .main/.light/.dark explicitly.
 */
export function applyBrand(brand) {
  const root = document.documentElement;
  const c    = brand.colors || {};

  // ─── Helper ──────────────────────────────────────────────────────────────
  function setVar(name, value) {
    if (!value) return;
    root.style.setProperty(`--${name}`, value);
    const rgb = hexToRgb(value);
    if (rgb) root.style.setProperty(`--${name}-rgb`, rgb);
  }
  function setShadow(name, value) {
    if (value) root.style.setProperty(`--${name}`, value);
  }

  // ─── Brand colors (main + light/dark variants) ────────────────────────────
  // brand.json stores each color as { main, light, dark } — NOT as bare hex
  const primary   = c.primary?.main   || '#06b6d4';
  const secondary = c.secondary?.main || '#8b5cf6';
  const accent    = c.accent?.main    || '#f59e0b';
  const success   = c.success?.main   || '#10b981';
  const warning   = c.warning?.main   || '#f59e0b';
  const error     = c.error?.main     || '#ef4444';

  setVar('color-primary',       primary);
  setVar('color-primary-light', c.primary?.light   || lighten(primary, 20));
  setVar('color-primary-dark',  c.primary?.dark    || darken(primary, 15));

  setVar('color-secondary',       secondary);
  setVar('color-secondary-light', c.secondary?.light || lighten(secondary, 15));
  setVar('color-secondary-dark',  c.secondary?.dark  || darken(secondary, 15));

  setVar('color-accent',       accent);
  setVar('color-accent-light', c.accent?.light || lighten(accent, 15));
  setVar('color-accent-dark',  c.accent?.dark  || darken(accent, 15));

  setVar('color-success', success);
  setVar('color-warning', warning);
  setVar('color-error',   error);

  // ─── Surface / background ────────────────────────────────────────────────
  const bg = c.background || {};
  setVar('color-background',  bg.base     || '#0b1220');
  setVar('color-surface',     bg.surface  || '#0f1724');
  setVar('color-surface-alt', bg.elevated || '#131d33');
  if (bg.overlay) root.style.setProperty('--color-surface-overlay', bg.overlay);

  // Alias tokens used by some components
  setVar('color-card',  bg.surface  || '#0f1724');
  setVar('color-dark',  bg.base     || '#0b1220');

  // ─── Border ──────────────────────────────────────────────────────────────
  const border = c.border || {};
  setVar('color-border-subtle', border.subtle || '#1e293b');
  setVar('color-border-strong', border.strong || '#334155');
  setVar('color-border-accent', border.accent || primary);
  setVar('color-border',        border.subtle || '#1e293b');

  // ─── Text ────────────────────────────────────────────────────────────────
  const text = c.text || {};
  setVar('color-text-primary',   text.primary   || '#e6eef8');
  setVar('color-text-secondary', text.secondary || '#94a3b8');
  setVar('color-text-muted',     text.muted     || '#64748b');
  setVar('color-text-on-accent', text.onAccent  || '#f8fafc');
  setVar('color-light',          text.primary   || '#e6eef8');

  // Core primitive aliases
  setVar('core-primitive-surface',     bg.surface  || '#0f1724');
  setVar('core-primitive-surface-alt', bg.elevated || '#131d33');
  setVar('core-primitive-border',      border.subtle || '#1e293b');
  setVar('core-primitive-text',        text.primary  || '#e6eef8');
  setVar('core-primitive-muted',       text.muted    || '#64748b');
  setVar('core-primitive-accent',      primary);
  root.style.setProperty('--core-primitive-radius', '16px');

  // ─── Shadows ─────────────────────────────────────────────────────────────
  const sh = brand.shadows || {};
  setShadow('shadow-primary',   sh.primary   || `0 20px 45px rgba(${hexToRgb(primary)},0.24)`);
  setShadow('shadow-secondary', sh.secondary || `0 20px 45px rgba(${hexToRgb(secondary)},0.24)`);
  setShadow('shadow-accent',    sh.accent    || `0 18px 40px rgba(${hexToRgb(accent)},0.32)`);
  setShadow('shadow-success',   sh.success   || `0 18px 40px rgba(${hexToRgb(success)},0.24)`);
  setShadow('shadow-warning',   sh.warning   || `0 18px 45px rgba(${hexToRgb(warning)},0.34)`);
  setShadow('shadow-error',     sh.error     || `0 18px 45px rgba(${hexToRgb(error)},0.3)`);
  setShadow('shadow-elevated',  sh.elevated  || '0 24px 60px rgba(11,18,32,0.55)');
  setShadow('shadow-focus',     sh.focus     || `0 0 0 3px rgba(${hexToRgb(primary)},0.5)`);
  setShadow('core-primitive-shadow', sh.elevated || '0 24px 60px rgba(11,18,32,0.55)');

  // ─── Chat mode tints ─────────────────────────────────────────────────────
  const chat = brand.chat?.modes || {};
  if (chat.ask?.tint)      setVar('chat-mode-ask-tint',      chat.ask.tint);
  if (chat.workflow?.tint) setVar('chat-mode-workflow-tint', chat.workflow.tint);
  if (brand.chat?.bubbleRadius) root.style.setProperty('--chat-bubble-radius', brand.chat.bubbleRadius);

  // ─── Fonts ───────────────────────────────────────────────────────────────
  // brand.json fonts are objects: { family, fallbacks, googleFont, localFont, src, tailwindClass }
  const bodyFontObj    = brand.fonts?.body    || {};
  const headingFontObj = brand.fonts?.heading || bodyFontObj;

  function applyFont(fontObj, cssVar) {
    if (!fontObj?.family) return;
    if (fontObj.localFont && fontObj.src) {
      injectLocalFont(fontObj.family, fontObj.src, fontObj);
    } else if (fontObj.googleFont) {
      loadGoogleFont(fontObj.googleFont);
    } else {
      loadGoogleFont(fontObj.family);
    }
    const stack = fontObj.fallbacks
      ? `"${fontObj.family}", ${fontObj.fallbacks}`
      : `"${fontObj.family}", system-ui, sans-serif`;
    root.style.setProperty(cssVar, stack);
  }

  applyFont(bodyFontObj,    '--font-body');
  applyFont(headingFontObj, '--font-heading');

  // Additional local fonts (e.g. logo font)
  Object.values(brand.fonts || {}).forEach((fontObj) => {
    if (fontObj?.localFont && fontObj?.src
        && fontObj.family !== bodyFontObj.family
        && fontObj.family !== headingFontObj.family) {
      injectLocalFont(fontObj.family, fontObj.src, fontObj);
    }
  });

  // ─── Document meta ────────────────────────────────────────────────────────
  document.title = brand.name || 'App';

  let faviconEl = document.querySelector("link[rel~='icon']");
  if (!faviconEl) {
    faviconEl = document.createElement('link');
    faviconEl.rel = 'icon';
    document.head.appendChild(faviconEl);
  }
  faviconEl.href = brand.assets?.favicon || brand.assets?.logo || '';

  // ─── Asset CSS variables ──────────────────────────────────────────────────
  if (brand.assets?.backgroundImage) {
    root.style.setProperty('--brand-bg-url', `url("${brand.assets.backgroundImage}")`);
  } else {
    root.style.removeProperty('--brand-bg-url');
  }
  if (brand.assets?.logo)            root.style.setProperty('--brand-logo-url', brand.assets.logo);
  if (brand.assets?.profileFallback) root.style.setProperty('--brand-profile-fallback-url', brand.assets.profileFallback);

  console.log(`🎨 Brand applied: ${brand.name}`);
}

// ─── Helpers ──────────────────────────────────────────────────────────────

/**
 * Load a Google Font. Accepts either:
 *  - a full URL (from brand.json googleFont field), or
 *  - a raw font-family name (auto-builds a basic Google Fonts URL).
 */
function loadGoogleFont(fontOrUrl) {
  if (!fontOrUrl) return;
  const url = fontOrUrl.startsWith('http')
    ? fontOrUrl
    : `https://fonts.googleapis.com/css2?family=${fontOrUrl.replace(/\s+/g, '+')}:wght@300;400;500;600;700;800&display=swap`;
  if (!document.querySelector(`link[href="${url}"]`)) {
    const link = document.createElement('link');
    link.rel  = 'stylesheet';
    link.href = url;
    document.head.appendChild(link);
  }
}

function injectLocalFont(family, src, descriptors = {}) {
  const id = `local-font-${family.replace(/\s+/g, '-').toLowerCase()}`;
  if (document.getElementById(id)) return;
  const weight = descriptors.weight || '400';
  const style  = descriptors.style  || 'normal';
  const format = src.endsWith('.otf') ? 'opentype' : src.endsWith('.woff2') ? 'woff2' : src.endsWith('.woff') ? 'woff' : 'truetype';
  const css = `@font-face { font-family: "${family}"; src: url("${src}") format("${format}"); font-weight: ${weight}; font-style: ${style}; font-display: swap; }`;
  const style_el = document.createElement('style');
  style_el.id = id;
  style_el.textContent = css;
  document.head.appendChild(style_el);
}

function hexToRgb(hex) {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return '0, 0, 0';
  return `${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}`;
}

function lighten(hex, percent) {
  const num = parseInt(hex.replace('#', ''), 16);
  const amt = Math.round(2.55 * percent);
  const R = Math.min(255, (num >> 16) + amt);
  const G = Math.min(255, ((num >> 8) & 0x00FF) + amt);
  const B = Math.min(255, (num & 0x0000FF) + amt);
  return `#${(0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1)}`;
}

function darken(hex, percent) {
  const num = parseInt(hex.replace('#', ''), 16);
  const amt = Math.round(2.55 * percent);
  const R = Math.max(0, (num >> 16) - amt);
  const G = Math.max(0, ((num >> 8) & 0x00FF) - amt);
  const B = Math.max(0, (num & 0x0000FF) - amt);
  return `#${(0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1)}`;
}

export default loadBrand;
