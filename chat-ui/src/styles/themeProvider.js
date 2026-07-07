import { palettes } from '../ui/theme/palettes.js';
import { fontImports } from '../ui/theme/fonts.js';

const THEME_TOKEN_DEFAULTS = {
  primitives: {
    radius: {
      surface: '1rem',
      control: '1rem',
      bubble: '18px',
    },
    measure: {
      shell: '1440px',
      chat_feed: '1040px',
    },
    spacing: {
      tight: '0.5rem',
      base: '1rem',
      loose: '1.5rem',
    },
  },
  ui: {
    shell: {
      frame: {
        maxWidth: '1440px',
      },
      header: {
        height: '4rem',
        paddingX: '1.5rem',
        gap: '1rem',
        clusterGap: '0.875rem',
        navGap: '0.5rem',
        navPaddingLeft: '0.5rem',
        controlGap: '0.75rem',
        actionHeight: '2.75rem',
        actionPaddingX: '1rem',
        utilitySize: '2.75rem',
        utilityPadding: '0.375rem',
        avatarSize: '2rem',
        avatarLargeSize: '3rem',
        profileHeight: '2.875rem',
        profilePaddingX: '0.75rem',
        panelRadius: '1rem',
      },
      footer: {
        maxWidth: '1440px',
        paddingY: '0.9rem',
        paddingX: '1rem',
        gap: '0.75rem',
      },
    },
    page: {
      maxWidth: {
        grid: '1280px',
        sidebar: '1360px',
        'full-width': '1200px',
        split: '1280px',
      },
      paddingX: {
        base: '1rem',
        md: '2rem',
        xl: '2.5rem',
      },
      paddingY: {
        base: '2rem',
        md: '2.5rem',
      },
      sectionGap: '2rem',
      titlePaddingBottom: '1rem',
    },
    chat: {
      modes: {
        ask: { tint: '#3b82f6', label: 'Ask' },
        workflow: { tint: '#6366f1', label: 'Workflow' },
      },
      bubbleRadius: '18px',
      feedMaxWidth: '1040px',
      feedPaddingTop: {
        base: '1rem',
        sm: '1.25rem',
        md: '1.75rem',
      },
      feedPaddingBottom: {
        base: '3.25rem',
        sm: '3.75rem',
        md: '4rem',
      },
      feedPaddingX: {
        base: '1rem',
        sm: '1.5rem',
        md: '1.75rem',
      },
      bubblePaddingY: '0.95rem',
      bubblePaddingX: '1.15rem',
      userBubblePaddingY: '0.7rem',
      userBubblePaddingX: '0.95rem',
      bubbleGap: '0.85rem',
      bubbleHeaderGap: '0.35rem',
      namePillPaddingY: '1px',
      namePillPaddingX: '0.55rem',
      namePillFontSize: '10px',
    },
  },
};

const DENSITY_UI_PRESETS = {
  compact: {
    ui: {
      shell: {
        header: {
          height: '3.75rem',
          paddingX: '1.25rem',
          gap: '0.75rem',
          clusterGap: '0.75rem',
          navGap: '0.4rem',
          navPaddingLeft: '0.375rem',
          controlGap: '0.625rem',
          actionHeight: '2.5rem',
          actionPaddingX: '0.875rem',
          utilitySize: '2.5rem',
          utilityPadding: '0.3rem',
          avatarSize: '1.9rem',
          avatarLargeSize: '2.75rem',
          profileHeight: '2.625rem',
          profilePaddingX: '0.625rem',
        },
        footer: {
          paddingY: '0.75rem',
          paddingX: '0.875rem',
          gap: '0.625rem',
        },
      },
      page: {
        paddingX: { base: '0.875rem', md: '1.5rem', xl: '2rem' },
        paddingY: { base: '1.5rem', md: '2rem' },
        sectionGap: '1.5rem',
        titlePaddingBottom: '0.875rem',
      },
      chat: {
        feedPaddingTop: { base: '0.875rem', sm: '1rem', md: '1.5rem' },
        feedPaddingBottom: { base: '2.5rem', sm: '3rem', md: '3.25rem' },
        feedPaddingX: { base: '0.875rem', sm: '1.125rem', md: '1.5rem' },
        bubblePaddingY: '0.8rem',
        bubblePaddingX: '1rem',
        userBubblePaddingY: '0.625rem',
        userBubblePaddingX: '0.875rem',
        bubbleGap: '0.7rem',
      },
    },
  },
  comfortable: {},
  spacious: {
    ui: {
      shell: {
        header: {
          height: '4.5rem',
          paddingX: '1.75rem',
          gap: '1.125rem',
          clusterGap: '1rem',
          navGap: '0.625rem',
          navPaddingLeft: '0.625rem',
          controlGap: '0.875rem',
          actionHeight: '3rem',
          actionPaddingX: '1.125rem',
          utilitySize: '3rem',
          utilityPadding: '0.45rem',
          avatarSize: '2.15rem',
          avatarLargeSize: '3.25rem',
          profileHeight: '3rem',
          profilePaddingX: '0.875rem',
        },
        footer: {
          paddingY: '1rem',
          paddingX: '1.25rem',
          gap: '0.875rem',
        },
      },
      page: {
        paddingX: { base: '1.125rem', md: '2.25rem', xl: '2.75rem' },
        paddingY: { base: '2.25rem', md: '2.75rem' },
        sectionGap: '2.25rem',
        titlePaddingBottom: '1.125rem',
      },
      chat: {
        feedPaddingTop: { base: '1.125rem', sm: '1.5rem', md: '2rem' },
        feedPaddingBottom: { base: '3.5rem', sm: '4rem', md: '4.5rem' },
        feedPaddingX: { base: '1.125rem', sm: '1.75rem', md: '2rem' },
        bubblePaddingY: '1rem',
        bubblePaddingX: '1.25rem',
        userBubblePaddingY: '0.8rem',
        userBubblePaddingX: '1rem',
        bubbleGap: '1rem',
      },
    },
  },
};

// ============================================================================
// FILE: chat-ui/src/styles/themeProvider.js
// PURPOSE: Dynamic theme system for multi-tenant branding
// USAGE: Theme is loaded from the /api/theme-config endpoint
//        (backed by brand/theme_config.json). In platform mode,
//        /api/themes/{appId} overrides are deep-merged on top.
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
    logo:    { family: 'system-ui', fallbacks: 'sans-serif', tailwindClass: 'font-sans' },
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
    chatbackgroundImage: '/assets/chat_bg_template.png',
    loadingIcon: null,
  },
  primitives: THEME_TOKEN_DEFAULTS.primitives,
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
  chat: THEME_TOKEN_DEFAULTS.ui.chat,
  ui: THEME_TOKEN_DEFAULTS.ui,
  header: {
    logo: { src: null, wordmark: null, alt: 'App', href: '/' },
    actions: [],
  },
  footer: {
    links: [],
    visible: true,
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

const LEGACY_FONT_PRESETS = {
  system: {
    family: 'system-ui',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: null,
  },
  rajdhani: {
    family: 'Rajdhani',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.rajdhani,
  },
  orbitron: {
    family: 'Orbitron',
    fallbacks: "'Rajdhani', ui-sans-serif, system-ui, sans-serif",
    googleFont: fontImports.orbitron,
  },
  oxanium: {
    family: 'Oxanium',
    fallbacks: "'Rajdhani', ui-sans-serif, system-ui, sans-serif",
    googleFont: fontImports.oxanium,
  },
  inter: {
    family: 'Inter',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.inter,
  },
  roboto: {
    family: 'Roboto',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.roboto,
  },
  opensans: {
    family: 'Open Sans',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.opensans,
  },
  lato: {
    family: 'Lato',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.lato,
  },
  poppins: {
    family: 'Poppins',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.poppins,
  },
  nunito: {
    family: 'Nunito',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.nunito,
  },
  montserrat: {
    family: 'Montserrat',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.montserrat,
  },
  raleway: {
    family: 'Raleway',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
    googleFont: fontImports.raleway,
  },
  playfair: {
    family: 'Playfair Display',
    fallbacks: 'Georgia, serif',
    googleFont: fontImports.playfair,
  },
  merriweather: {
    family: 'Merriweather',
    fallbacks: 'Georgia, serif',
    googleFont: fontImports.merriweather,
  },
  'source-code-pro': {
    family: 'Source Code Pro',
    fallbacks: "'Courier New', monospace",
    googleFont: fontImports['source-code-pro'],
  },
  logo: {
    family: 'Fagrak Inline',
    fallbacks: "'Rajdhani', ui-sans-serif, system-ui, sans-serif",
    localFont: true,
    src: '/fonts/Fagrak Inline.otf',
  },
};

const CHAT_BUBBLE_RADIUS_BY_SCALE = {
  none: '12px',
  small: '14px',
  medium: '18px',
  large: '22px',
  full: '999px',
};

const BUILTIN_LOCAL_FONT_PRESETS = [
  {
    family: 'Fagrak',
    src: '/fonts/Fagrak.otf',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
  },
  {
    family: 'Techfont',
    src: '/fonts/Techfont.woff',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
  },
  {
    family: 'Fueled by Schlitz',
    src: '/fonts/FBS.otf',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
  },
  {
    family: 'Hyperjump',
    src: '/fonts/Hyperjump Bold.otf',
    fallbacks: 'ui-sans-serif, system-ui, sans-serif',
  },
  {
    family: 'Oxanium',
    src: '/fonts/Oxanium-VariableFont_wght.ttf',
    fallbacks: "'Rajdhani', ui-sans-serif, system-ui, sans-serif",
  },
];

const localFontAvailabilityCache = new Map();
const localFontRegistrationCache = new Map();
const localFontMissingNotice = new Set();

function normalizePageMaxWidthKeys(maxWidth) {
  if (!maxWidth || typeof maxWidth !== 'object') return {};
  const normalized = { ...maxWidth };
  if (normalized.full_width && !normalized['full-width']) {
    normalized['full-width'] = normalized.full_width;
  }
  return normalized;
}

function resolveThemeSurfaceTokens({ themeConfig = {}, primitives = {}, ui = {} } = {}) {
  const density = themeConfig?.density || 'comfortable';
  const densityPreset = DENSITY_UI_PRESETS[density] || DENSITY_UI_PRESETS.comfortable;
  const resolvedPrimitives = deepMerge(
    deepMerge(THEME_TOKEN_DEFAULTS.primitives, densityPreset.primitives || {}),
    primitives || {},
  );
  const resolvedUi = deepMerge(
    deepMerge(THEME_TOKEN_DEFAULTS.ui, densityPreset.ui || {}),
    ui || {},
  );

  resolvedUi.page = {
    ...THEME_TOKEN_DEFAULTS.ui.page,
    ...resolvedUi.page,
    maxWidth: {
      ...THEME_TOKEN_DEFAULTS.ui.page.maxWidth,
      ...normalizePageMaxWidthKeys(resolvedUi.page?.maxWidth),
    },
    paddingX: {
      ...THEME_TOKEN_DEFAULTS.ui.page.paddingX,
      ...(resolvedUi.page?.paddingX || {}),
    },
    paddingY: {
      ...THEME_TOKEN_DEFAULTS.ui.page.paddingY,
      ...(resolvedUi.page?.paddingY || {}),
    },
  };

  resolvedUi.chat = {
    ...THEME_TOKEN_DEFAULTS.ui.chat,
    ...resolvedUi.chat,
    modes: deepMerge(THEME_TOKEN_DEFAULTS.ui.chat.modes, resolvedUi.chat?.modes || {}),
    feedPaddingTop: {
      ...THEME_TOKEN_DEFAULTS.ui.chat.feedPaddingTop,
      ...(resolvedUi.chat?.feedPaddingTop || {}),
    },
    feedPaddingBottom: {
      ...THEME_TOKEN_DEFAULTS.ui.chat.feedPaddingBottom,
      ...(resolvedUi.chat?.feedPaddingBottom || {}),
    },
    feedPaddingX: {
      ...THEME_TOKEN_DEFAULTS.ui.chat.feedPaddingX,
      ...(resolvedUi.chat?.feedPaddingX || {}),
    },
  };

  if (!resolvedUi.chat.bubbleRadius) {
    resolvedUi.chat.bubbleRadius = CHAT_BUBBLE_RADIUS_BY_SCALE[themeConfig?.radius] || THEME_TOKEN_DEFAULTS.ui.chat.bubbleRadius;
  }

  resolvedPrimitives.radius = {
    ...THEME_TOKEN_DEFAULTS.primitives.radius,
    ...(resolvedPrimitives.radius || {}),
  };
  if (!resolvedPrimitives.radius.bubble) {
    resolvedPrimitives.radius.bubble = resolvedUi.chat.bubbleRadius;
  }
  resolvedPrimitives.measure = {
    ...THEME_TOKEN_DEFAULTS.primitives.measure,
    ...(resolvedPrimitives.measure || {}),
  };
  if (!resolvedPrimitives.measure.shell) {
    resolvedPrimitives.measure.shell = resolvedUi.shell?.frame?.maxWidth || THEME_TOKEN_DEFAULTS.primitives.measure.shell;
  }
  if (!resolvedPrimitives.measure.chat_feed) {
    resolvedPrimitives.measure.chat_feed = resolvedUi.chat.feedMaxWidth || THEME_TOKEN_DEFAULTS.primitives.measure.chat_feed;
  }

  return {
    primitives: resolvedPrimitives,
    ui: resolvedUi,
  };
}

function resolveHeaderIconValue(basePath, value) {
  if (!value || typeof value !== 'string') return value ?? null;
  if (value.startsWith('/') || value.startsWith('http')) return value;
  if (HEADER_ICON_FILE_RE.test(value)) return resolveBrandAsset(basePath, value);
  return value;
}

function hslTripletToRgb(triplet) {
  if (!triplet || typeof triplet !== 'string') return null;
  const parts = triplet.trim().split(/\s+/);
  if (parts.length !== 3) return null;

  const hue = Number.parseFloat(parts[0]);
  const saturation = Number.parseFloat(parts[1].replace('%', ''));
  const lightness = Number.parseFloat(parts[2].replace('%', ''));
  if (![hue, saturation, lightness].every(Number.isFinite)) return null;

  const s = saturation / 100;
  const l = lightness / 100;
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const hPrime = ((hue % 360) + 360) % 360 / 60;
  const x = chroma * (1 - Math.abs((hPrime % 2) - 1));

  let red = 0;
  let green = 0;
  let blue = 0;

  if (hPrime >= 0 && hPrime < 1) {
    red = chroma;
    green = x;
  } else if (hPrime >= 1 && hPrime < 2) {
    red = x;
    green = chroma;
  } else if (hPrime >= 2 && hPrime < 3) {
    green = chroma;
    blue = x;
  } else if (hPrime >= 3 && hPrime < 4) {
    green = x;
    blue = chroma;
  } else if (hPrime >= 4 && hPrime < 5) {
    red = x;
    blue = chroma;
  } else {
    red = chroma;
    blue = x;
  }

  const match = l - chroma / 2;
  return {
    r: Math.round((red + match) * 255),
    g: Math.round((green + match) * 255),
    b: Math.round((blue + match) * 255),
  };
}

function hslTripletToHex(triplet, fallback = null) {
  const rgb = hslTripletToRgb(triplet);
  if (!rgb) return fallback;
  return `#${[rgb.r, rgb.g, rgb.b].map((value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function paletteHex(scale, tone, fallback) {
  return hslTripletToHex(scale?.[tone], fallback);
}

function resolveThemeFontPreset(fontKey, fallbackKey = 'system') {
  return {
    ...(LEGACY_FONT_PRESETS[fallbackKey] || LEGACY_FONT_PRESETS.system),
    ...(LEGACY_FONT_PRESETS[fontKey] || {}),
  };
}

function buildSchemaChatTheme(config, basePath) {
  const fallback = BARE_FALLBACK_THEME;
  const themeConfig = config?.theme || {};
  const ui = config?.ui || {};
  const identity = config?.identity || {};
  const rawAssets = config?.assets || {};
  const appearance = themeConfig.appearance === 'light' ? 'light' : 'dark';
  const resolvedSurfaceTokens = resolveThemeSurfaceTokens({
    themeConfig,
    primitives: config?.primitives,
    ui,
  });

  const primaryScale = palettes[themeConfig.primary] || palettes.cyan;
  const secondaryScale = palettes.violet;
  const accentScale = palettes.amber;
  const successScale = palettes.emerald;
  const warningScale = palettes.amber;
  const errorScale = palettes.red;

  const primaryMain = paletteHex(primaryScale, 500, '#06b6d4');
  const primaryLight = paletteHex(primaryScale, 300, '#67e8f9');
  const primaryDark = paletteHex(primaryScale, 700, '#0e7490');
  const secondaryMain = paletteHex(secondaryScale, 500, '#8b5cf6');
  const secondaryLight = paletteHex(secondaryScale, 300, '#a78bfa');
  const secondaryDark = paletteHex(secondaryScale, 700, '#6d28d9');
  const accentMain = paletteHex(accentScale, 500, '#f59e0b');
  const accentLight = paletteHex(accentScale, 300, '#fbbf24');
  const accentDark = paletteHex(accentScale, 700, '#d97706');
  const successMain = paletteHex(successScale, 500, '#10b981');
  const warningMain = paletteHex(warningScale, 500, '#f59e0b');
  const errorMain = paletteHex(errorScale, 500, '#ef4444');

  const primaryRgb = hexToRgb(primaryMain) || '6, 182, 212';
  const secondaryRgb = hexToRgb(secondaryMain) || '139, 92, 246';
  const accentRgb = hexToRgb(accentMain) || '245, 158, 11';
  const successRgb = hexToRgb(successMain) || '16, 185, 129';
  const warningRgb = hexToRgb(warningMain) || '245, 158, 11';
  const errorRgb = hexToRgb(errorMain) || '239, 68, 68';

  const assets = {
    logo: resolveBrandAsset(basePath, rawAssets.logo),
    wordmark: resolveBrandAsset(basePath, rawAssets.wordmark),
    favicon: resolveBrandAsset(basePath, rawAssets.favicon),
    chatbackgroundImage: resolveBrandAsset(basePath, rawAssets.chatbackgroundImage),
    loadingIcon: resolveBrandAsset(basePath, rawAssets.loadingIcon),
  };

  const darkColors = {
    primary: { main: primaryMain, light: primaryLight, dark: primaryDark, name: themeConfig.primary || 'cyan' },
    secondary: { main: secondaryMain, light: secondaryLight, dark: secondaryDark, name: 'violet' },
    accent: { main: accentMain, light: accentLight, dark: accentDark, name: 'amber' },
    success: { main: successMain, light: paletteHex(successScale, 300, '#34d399'), dark: paletteHex(successScale, 700, '#059669'), name: 'emerald' },
    warning: { main: warningMain, light: accentLight, dark: accentDark, name: 'amber' },
    error: { main: errorMain, light: paletteHex(errorScale, 300, '#f87171'), dark: paletteHex(errorScale, 700, '#dc2626'), name: 'red' },
    background: { base: '#0b1220', surface: '#0f1724', elevated: '#131d33', overlay: 'rgba(13, 23, 42, 0.72)' },
    border: { subtle: '#1e293b', strong: '#334155', accent: primaryMain },
    text: { primary: '#e6eef8', secondary: '#94a3b8', muted: '#64748b', onAccent: '#020617' },
  };

  const lightColors = {
    primary: { main: primaryMain, light: primaryLight, dark: primaryDark, name: themeConfig.primary || 'cyan' },
    secondary: { main: secondaryMain, light: secondaryLight, dark: secondaryDark, name: 'violet' },
    accent: { main: accentMain, light: accentLight, dark: accentDark, name: 'amber' },
    success: { main: successMain, light: paletteHex(successScale, 300, '#34d399'), dark: paletteHex(successScale, 700, '#059669'), name: 'emerald' },
    warning: { main: warningMain, light: accentLight, dark: accentDark, name: 'amber' },
    error: { main: errorMain, light: paletteHex(errorScale, 300, '#f87171'), dark: paletteHex(errorScale, 700, '#dc2626'), name: 'red' },
    background: { base: '#f4f8fc', surface: '#ffffff', elevated: '#e7eef7', overlay: 'rgba(226, 232, 240, 0.72)' },
    border: { subtle: '#d6e0ec', strong: '#b6c4d6', accent: primaryMain },
    text: { primary: '#08111f', secondary: '#526274', muted: '#7b8898', onAccent: '#f8fafc' },
  };

  return {
    fonts: {
      body: resolveThemeFontPreset(themeConfig.font, 'rajdhani'),
      heading: resolveThemeFontPreset(themeConfig.font_heading || 'orbitron', 'orbitron'),
      logo: resolveThemeFontPreset(themeConfig.font_logo || 'logo', 'logo'),
    },
    colors: appearance === 'light' ? lightColors : darkColors,
    shadows: {
      primary: `0 20px 45px rgba(${primaryRgb}, 0.24)`,
      secondary: `0 20px 45px rgba(${secondaryRgb}, 0.24)`,
      accent: `0 18px 40px rgba(${accentRgb}, 0.32)`,
      success: `0 18px 40px rgba(${successRgb}, 0.24)`,
      warning: `0 18px 45px rgba(${warningRgb}, 0.34)`,
      error: `0 18px 45px rgba(${errorRgb}, 0.30)`,
      elevated: appearance === 'light' ? '0 24px 60px rgba(15, 23, 42, 0.16)' : '0 24px 60px rgba(11, 18, 32, 0.55)',
      focus: `0 0 0 3px rgba(${primaryRgb}, 0.55)`,
    },
    branding: {
      name: identity.name || fallback.branding.name,
      chatbackgroundImage: assets.chatbackgroundImage || fallback.branding.chatbackgroundImage,
      loadingIcon: assets.loadingIcon || fallback.branding.loadingIcon,
      favicon: assets.favicon,
      logo: assets.logo,
      wordmark: assets.wordmark,
    },
    primitives: resolvedSurfaceTokens.primitives,
    profile: fallback.profile,
    notifications: fallback.notifications,
    chat: {
      ...resolvedSurfaceTokens.ui.chat,
      modes: {
        ...resolvedSurfaceTokens.ui.chat.modes,
        ask: { tint: primaryMain, label: 'Ask' },
        workflow: { tint: secondaryMain, label: 'Workflow' },
      },
      bubbleRadius:
        resolvedSurfaceTokens.ui.chat.bubbleRadius ||
        CHAT_BUBBLE_RADIUS_BY_SCALE[themeConfig.radius] ||
        fallback.chat.bubbleRadius,
    },
    ui: {
      ...resolvedSurfaceTokens.ui,
      chat: {
        ...resolvedSurfaceTokens.ui.chat,
        modes: {
          ...resolvedSurfaceTokens.ui.chat.modes,
          ask: { tint: primaryMain, label: 'Ask' },
          workflow: { tint: secondaryMain, label: 'Workflow' },
        },
      },
    },
    header: fallback.header,
    footer: fallback.footer,
    _basePath: basePath,
    _source: 'app-theme-bridge',
  };
}

/**
 * Convert a unified theme_config.json into the full theme shape.
 *
 * @param {object} config    — merged theme config (identity, assets, fonts, colors, shadows, ui).
 * @param {string} basePath  — absolute URL prefix for relative asset filenames, e.g. '/assets'.
 */
function themeConfigToTheme(config, basePath) {
  const themeConfig = config.theme || {};
  const ui = config.ui || {};
  const fallback = BARE_FALLBACK_THEME;
  const identity = config.identity || {};
  const resolvedSurfaceTokens = resolveThemeSurfaceTokens({
    themeConfig,
    primitives: config.primitives,
    ui,
  });

  // --- Visual assets ---
  const rawAssets = config.assets || {};
  const assets = {
    logo:            resolveBrandAsset(basePath, rawAssets.logo),
    wordmark:        resolveBrandAsset(basePath, rawAssets.wordmark),
    favicon:         resolveBrandAsset(basePath, rawAssets.favicon),
    chatbackgroundImage: resolveBrandAsset(basePath, rawAssets.chatbackgroundImage),
    loadingIcon:     resolveBrandAsset(basePath, rawAssets.loadingIcon),
  };

  // --- Fallback warnings ---
  const _bn = identity.name || 'theme';
  if (!assets.chatbackgroundImage)
    console.warn(`⚠️ [THEME] [${_bn}] assets.chatbackgroundImage not set — using fallback: chat_bg_template.png`);
  if (!assets.logo)
    console.warn(`⚠️ [THEME] [${_bn}] assets.logo not set — header logo will be missing`);
  if (!assets.favicon)
    console.warn(`⚠️ [THEME] [${_bn}] assets.favicon not set — browser tab icon will be missing`);
  if (!assets.wordmark)
    console.warn(`⚠️ [THEME] [${_bn}] assets.wordmark not set — header wordmark will be missing`);
  if (!config.fonts)
    console.warn(`⚠️ [THEME] [${_bn}] fonts block not configured — using system-ui fallback`);
  if (!config.colors)
    console.warn(`⚠️ [THEME] [${_bn}] colors block not configured — using bare fallback palette`);
  if (!config.shadows)
    console.warn(`⚠️ [THEME] [${_bn}] shadows block not configured — using bare fallback shadows`);

  return {
    fonts:   config.fonts   || fallback.fonts,
    colors:  config.colors  || fallback.colors,
    shadows: config.shadows || fallback.shadows,
    branding: {
      name:            identity.name              || fallback.branding.name,
      chatbackgroundImage: assets.chatbackgroundImage     || fallback.branding.chatbackgroundImage,
      loadingIcon:     assets.loadingIcon         || fallback.branding.loadingIcon,
      favicon:         assets.favicon,
      logo:            assets.logo,
      wordmark:        assets.wordmark,
    },
    // Shell chrome (header, profile, notifications, footer) defaults are
    // provided by NavigationProvider. theme_config.json only carries visual
    // shell/page/chat tokens, not shell content. Static shell defaults remain
    // here for useTheme consumers.
    primitives: resolvedSurfaceTokens.primitives,
    profile:       fallback.profile,
    notifications: fallback.notifications,
    chat: resolvedSurfaceTokens.ui.chat,
    ui: resolvedSurfaceTokens.ui,
    header: fallback.header,
    footer: fallback.footer,
    _basePath: basePath,
    _source:   'config',
  };
}

/**
 * Apply App UI --mz-* tokens when the config response contains a `theme` key
 * (the schema-driven format from the active app root's brand/theme_config.json).
 *
 * Dynamically imports the token engine so this module stays usable in
 * environments where the theme module hasn't been bundled yet.
 */
async function applyAppThemeTokens(themeConfig) {
  try {
    const { generateThemeTokens, applyThemeTokens, getFontImports } =
      await import('../ui/theme/index.js');

    const tokens = generateThemeTokens(themeConfig);
    applyThemeTokens(tokens);

    // Apply dark mode class based on appearance setting
    const appearance = themeConfig.appearance ?? 'system';
    const prefersDark = typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    const isDark = appearance === 'dark' || (appearance === 'system' && prefersDark);
    document.documentElement.classList.toggle('dark', isDark);

    // Inject Google Fonts for the selected fonts
    for (const url of getFontImports(themeConfig)) {
      if (!document.querySelector(`link[href="${url}"]`)) {
        const link = document.createElement('link');
        link.rel  = 'stylesheet';
        link.href = url;
        document.head.appendChild(link);
      }
    }

  } catch (err) {
    console.warn('⚠️ [THEME] Could not apply App UI tokens:', err.message);
  }
}

/**
 * Load theme from the declarative theme_config.json via backend API.
 * Falls back to BARE_FALLBACK_THEME when the API is unavailable.
 *
 * When the response contains a `theme` key (schema-driven format), the
 * --mz-* CSS token set is applied immediately for the App UI system.
 * The --color-* / --core-primitive-* variables are set via the
 * themeConfigToTheme path for chat UI.
 */
async function loadThemeFromConfig() {
  const assetsPath = '/assets';
  const coreUrl = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL) || '';
  const baseUrl = coreUrl.replace(/\/+$/, '');

  try {
    const url = baseUrl ? `${baseUrl}/api/theme-config` : '/api/theme-config';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`/api/theme-config returned ${res.status}`);

    const config = await res.json();

    // Unified format: 'theme' key (App UI --mz-* tokens) coexists with
    // top-level chat-shell tokens.
    // Apply --mz-* tokens first, then continue to process chat-shell tokens.
    if (config.theme && typeof config.theme === 'object') {
      await applyAppThemeTokens(config.theme);
    }

    // Top-level format (identity/assets/fonts/colors/shadows) drives
    // --color-* / --core-primitive-* tokens for chat UI.
    if (config.identity || config.colors || config.fonts) {
      const theme = themeConfigToTheme(config, assetsPath);
      const src = config.theme ? 'unified' : 'config';
      return { theme, meta: { source: src, appId: 'default' } };
    }

    // Schema-only format without top-level chat-shell tokens.
    if (config.theme) {
      const theme = buildSchemaChatTheme(config, assetsPath);
      return { theme, meta: { source: 'app-theme-bridge', appId: 'default' } };
    }

    throw new Error('theme_config.json has no recognisable format (expected theme: or identity/colors keys)');
  } catch (err) {
    console.warn('⚠️ [THEME] Could not load theme config from API:', err.message);
    return { theme: BARE_FALLBACK_THEME, meta: { source: 'fallback', appId: 'default' } };
  }
}

// ---------------------------------------------------------------------------
// Theme cache + ID helpers
// ---------------------------------------------------------------------------

const themeCache = new Map();
const CURRENT_APP_ID_STORAGE_KEY = 'mozaiks.current_app_id';

function getAccessToken() {
  try {
    if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
      return window.mozaiksAuth.getAccessToken();
    }
  } catch (_) {}

  try {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('chatui_token') || localStorage.getItem('access_token');
    }
  } catch (_) {}

  return null;
}

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

function isSchemaThemeOverride(theme) {
  if (!theme || typeof theme !== 'object' || Array.isArray(theme)) return false;
  if (theme.colors || theme.fonts || theme.shadows || theme.branding || theme.chat) return false;
  return ['primary', 'appearance', 'font', 'font_heading', 'font_logo', 'radius', 'variant', 'density']
    .some((key) => Object.prototype.hasOwnProperty.call(theme, key));
}

function buildSchemaOverrideTheme(themeOverride, currentTheme) {
  return buildSchemaChatTheme(
    {
      theme: themeOverride,
      identity: {
        name: currentTheme?.branding?.name || BARE_FALLBACK_THEME.branding.name,
      },
      assets: {
        logo: currentTheme?.branding?.logo || null,
        wordmark: currentTheme?.branding?.wordmark || null,
        favicon: currentTheme?.branding?.favicon || null,
        chatbackgroundImage: currentTheme?.branding?.chatbackgroundImage || null,
        loadingIcon: currentTheme?.branding?.loadingIcon || null,
      },
    },
    currentTheme?._basePath || '/assets',
  );
}

/**
 * Try to fetch platform-level theme overrides from the API.
 * Returns the override theme object or null when unavailable.
 *
 * This only applies in multi-tenant platform mode where /api/themes/{appId}
 * returns platform overrides (typically shape: { theme: {...} }).
 * Core/local runtimes may not provide that shape, so this safely returns null.
 */
async function fetchPlatformOverrides(appId) {
  const controller = new AbortController();
  const timeout    = globalThis.setTimeout(() => controller.abort(), 4000);

  try {
    const token = getAccessToken();
    const headers = { Accept: 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;

    const response = await fetch(`/api/themes/${encodeURIComponent(appId)}`, {
      method: 'GET',
      headers,
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
 *   1. /api/theme-config     — declarative config (brand/theme_config.json).
 *   2. /api/themes/{appId}   — platform overrides merged on top (multi-tenant only).
 *
 * This guarantees theme_config.json is the source of truth in local dev.
 * In platform mode the API can overlay tenant-specific customisation.
 */
export async function getTheme(appId = 'default') {
  const normalizedId = normalizeAppId(appId);
  if (themeCache.has(normalizedId)) {
    return themeCache.get(normalizedId).theme;
  }

  // 1. Declarative theme_config.json — always load as the base
  const brand = await loadThemeFromConfig();
  let   theme = brand.theme;
  let   meta  = brand.meta;

  // 2. Platform API overrides (only when the endpoint exists)
  const overrides = await fetchPlatformOverrides(normalizedId);
  if (overrides) {
    const overrideTheme = isSchemaThemeOverride(overrides.theme)
      ? buildSchemaOverrideTheme(overrides.theme, theme)
      : overrides.theme;
    theme = deepMerge(theme, overrideTheme);
    meta  = { ...meta, ...overrides.meta, source: 'brand+api' };
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

    // Fonts
    const fonts = t.fonts || BARE_FALLBACK_THEME.fonts;
    Object.values(fonts).forEach((font) => {
      if (font?.googleFont && !font.localFont) loadGoogleFont(font.googleFont);
      if (font?.localFont && font?.src) {
        void ensureLocalFont(font);
      }
    });
    BUILTIN_LOCAL_FONT_PRESETS.forEach((font) => {
      void ensureLocalFont(font);
    });

    const root = document.documentElement;
    applyFontVariables(root, fonts);

    // CSS variables
    updateCSSVariables(t.colors, t.shadows, t.chat, t.primitives, t.ui);

    // Branding
    const branding = t.branding || {};
    if (branding.name) document.title = branding.name;
    if (branding.favicon) updateFavicon(branding.favicon);

    // Asset CSS variables (used by components via var() references)
    if (branding.chatbackgroundImage) {
      root.style.setProperty('--brand-bg-url', `url("${branding.chatbackgroundImage}")`);
    } else {
      console.warn('⚠️ [THEME] branding.chatbackgroundImage is empty after resolution — --brand-bg-url will not be set');
      root.style.removeProperty('--brand-bg-url');
    }
    if (branding.logo)            root.style.setProperty('--brand-logo-url', branding.logo);
    if (branding.wordmark)        root.style.setProperty('--brand-wordmark-url', branding.wordmark);
    if (t.profile?.icon) root.style.setProperty('--brand-profile-icon-url', t.profile.icon);

    logThemeRuntimeDiagnostics(root, fonts, t);

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

function normalizeFontSource(src) {
  if (!src || typeof src !== 'string') return null;
  const trimmed = src.trim();
  if (!trimmed) return null;
  if (/^[a-z]+:\/\//i.test(trimmed)) return trimmed;
  return trimmed.startsWith('/') ? trimmed : `/${trimmed.replace(/^\/+/, '')}`;
}

async function probeLocalFontSource(src) {
  const normalizedSrc = normalizeFontSource(src);
  if (!normalizedSrc) return false;
  if (localFontAvailabilityCache.has(normalizedSrc)) {
    return localFontAvailabilityCache.get(normalizedSrc);
  }

  const probe = (async () => {
    try {
      const headResponse = await fetch(normalizedSrc, { method: 'HEAD', cache: 'no-store' });
      if (headResponse.ok) return true;
      if (![405, 501].includes(headResponse.status)) return false;
    } catch (_) {
    }

    try {
      const getResponse = await fetch(normalizedSrc, { cache: 'no-store' });
      return getResponse.ok;
    } catch (_) {
      return false;
    }
  })();

  localFontAvailabilityCache.set(normalizedSrc, probe);
  return probe;
}

function inferFontFormat(src) {
  const normalized = (src || '').toLowerCase();
  if (normalized.endsWith('.woff2')) return 'woff2';
  if (normalized.endsWith('.woff')) return 'woff';
  if (normalized.endsWith('.ttf')) return 'truetype';
  if (normalized.endsWith('.otf')) return 'opentype';
  return 'woff2';
}

function normalizeFontFamily(family) {
  if (!family || typeof family !== 'string') return null;
  const trimmed = family.trim();
  if (!trimmed) return null;
  if (/^['"].*['"]$/.test(trimmed)) return trimmed;
  return /\s/.test(trimmed) ? `"${trimmed}"` : trimmed;
}

function buildFontStack(font, fallbackStack) {
  const family = normalizeFontFamily(font?.family);
  const fallbacks = font?.fallbacks || fallbackStack;
  return [family, fallbacks].filter(Boolean).join(', ');
}

function summarizeFontConfig(font) {
  if (!font) return null;
  return {
    family: font.family || null,
    source: font.localFont ? 'local' : font.googleFont ? 'google' : 'system',
    src: font.src || font.googleFont || null,
    fallbacks: font.fallbacks || null,
  };
}

function isFontAvailable(font) {
  if (typeof document === 'undefined' || !document.fonts?.check || !font?.family) {
    return null;
  }

  const rawFamily = String(font.family).trim().replace(/^['"]|['"]$/g, '');
  if (!rawFamily) {
    return null;
  }

  try {
    return document.fonts.check(`16px "${rawFamily}"`) || document.fonts.check(`16px ${rawFamily}`);
  } catch (_) {
    return null;
  }
}

function logThemeRuntimeDiagnostics(root, fonts, theme) {
  if (typeof window === 'undefined' || !root) {
    return;
  }

  const readElementFont = (selector) => {
    try {
      const element = document.querySelector(selector);
      if (!element) {
        return null;
      }
      return window.getComputedStyle(element).fontFamily;
    } catch (_) {
      return null;
    }
  };

  const emitDiagnostics = (phase) => {
    try {
      const rootStyle = window.getComputedStyle(root);
      const bodyStyle = document.body ? window.getComputedStyle(document.body) : null;
    } catch (error) {
      console.warn('⚠️ [THEME] Failed to collect runtime diagnostics:', error?.message || error);
    }
  };

  emitDiagnostics('post-apply');

  if (document.fonts?.ready && typeof document.fonts.ready.then === 'function') {
    document.fonts.ready
      .then(() => emitDiagnostics('fonts-ready'))
      .catch((error) => {
        console.warn('⚠️ [THEME] FontFaceSet ready() rejected:', error?.message || error);
      });
  }
}

function loadLocalFont(font) {
  const family = normalizeFontFamily(font?.family);
  const src = normalizeFontSource(font?.src);
  if (!family || !src) return;

  const styleId = `mozaiks-local-font-${src.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`;
  if (document.getElementById(styleId)) {
    return;
  }

  const style = document.createElement('style');
  style.id = styleId;
  style.textContent = `@font-face { font-family: ${family}; src: url("${src}") format("${inferFontFormat(src)}"); font-display: swap; }`;
  document.head.appendChild(style);
}

async function ensureLocalFont(font) {
  const src = normalizeFontSource(font?.src);
  const family = normalizeFontFamily(font?.family);
  if (!src || !family) return false;

  const styleId = `mozaiks-local-font-${src.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`;
  if (document.getElementById(styleId)) {
    return true;
  }

  if (localFontRegistrationCache.has(styleId)) {
    return localFontRegistrationCache.get(styleId);
  }

  const registration = probeLocalFontSource(src)
    .then((available) => {
      if (!available) {
        if (!localFontMissingNotice.has(src)) {
          localFontMissingNotice.add(src);
          console.info('🔤 [THEME] Skipping missing local font asset:', { family: font.family, src });
        }
        return false;
      }
      loadLocalFont({ ...font, src });
      return true;
    })
    .catch((error) => {
      console.warn('⚠️ [THEME] Failed to verify local font asset:', {
        family: font?.family || null,
        src,
        error: error?.message || error,
      });
      return false;
    })
    .finally(() => {
      localFontRegistrationCache.delete(styleId);
    });

  localFontRegistrationCache.set(styleId, registration);
  return registration;
}

function applyFontVariables(root, themeFonts) {
  const fonts = themeFonts || BARE_FALLBACK_THEME.fonts;
  const bodyFont = fonts.body || BARE_FALLBACK_THEME.fonts.body;
  const headingFont = fonts.heading || bodyFont || BARE_FALLBACK_THEME.fonts.heading;
  const logoFont = fonts.logo || headingFont || bodyFont || BARE_FALLBACK_THEME.fonts.logo;

  const bodyStack = buildFontStack(bodyFont, 'ui-sans-serif, system-ui, sans-serif');
  const headingStack = buildFontStack(headingFont, bodyStack || 'ui-sans-serif, system-ui, sans-serif');
  const logoStack = buildFontStack(logoFont, headingStack || bodyStack || 'ui-sans-serif, system-ui, sans-serif');

  if (bodyStack) root.style.setProperty('--font-body', bodyStack);
  if (headingStack) root.style.setProperty('--font-heading', headingStack);
  if (logoStack) root.style.setProperty('--font-logo', logoStack);

  const utilityStacks = {
    '--font-fagrak': buildFontStack({ family: 'Fagrak' }, bodyStack || 'ui-sans-serif, system-ui, sans-serif'),
    '--font-tech': buildFontStack({ family: 'Techfont' }, `${bodyStack || 'ui-sans-serif, system-ui, sans-serif'}, monospace`),
    '--font-fbs': buildFontStack({ family: 'Fueled by Schlitz' }, bodyStack || 'ui-sans-serif, system-ui, sans-serif'),
    '--font-hyperjump': buildFontStack({ family: 'Hyperjump' }, bodyStack || 'ui-sans-serif, system-ui, sans-serif'),
    '--font-transmission': buildFontStack(
      { family: 'Oxanium' },
      `${headingStack || bodyStack || 'ui-sans-serif, system-ui, sans-serif'}, monospace`
    ),
  };

  Object.entries(utilityStacks).forEach(([cssVar, value]) => {
    if (value) root.style.setProperty(cssVar, value);
  });

}

function updateFavicon(faviconUrl) {
  if (!faviconUrl || typeof faviconUrl !== 'string') return;
  let safeUrl = faviconUrl.trim();
  if (!safeUrl) return;

  // Normalize malformed host-style values like "//favicon.ico/" to "/favicon.ico"
  if (safeUrl.startsWith('//')) {
    safeUrl = `/${safeUrl.replace(/^\/+/, '')}`;
  }
  // Keep fully-qualified URLs, but remove accidental trailing slashes.
  if (/^[a-z]+:\/\//i.test(safeUrl)) {
    safeUrl = safeUrl.replace(/\/+$/, '');
  } else {
    // Coerce relative values like "favicon.ico/" to absolute root paths.
    if (!safeUrl.startsWith('/')) safeUrl = `/${safeUrl}`;
    safeUrl = safeUrl.replace(/\/+$/, '');
  }

  let link = document.querySelector("link[rel~='icon']");
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
  link.href = safeUrl;
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

function setDimensionVar(root, name, value, fallback) {
  const resolved = value || fallback;
  if (resolved != null && resolved !== '') {
    root.style.setProperty(`--${name}`, String(resolved));
  }
}

function updateCSSVariables(themeColors, themeShadows, themeChat, themePrimitives, themeUi) {
  const root    = document.documentElement;
  const colors  = themeColors  || BARE_FALLBACK_THEME.colors;
  const shadows = themeShadows || BARE_FALLBACK_THEME.shadows;
  const primitives = themePrimitives || BARE_FALLBACK_THEME.primitives;
  const ui = themeUi || BARE_FALLBACK_THEME.ui;
  const chat = themeChat || ui.chat || BARE_FALLBACK_THEME.chat;
  const fb      = BARE_FALLBACK_THEME;

  const backgroundBase = colors.background?.base || fb.colors.background.base;
  const surfaceBase = colors.background?.surface || fb.colors.background.surface;
  const surfaceAlt = colors.background?.elevated || fb.colors.background.elevated;
  const primaryMain = colors.primary?.main || fb.colors.primary.main;
  const primaryLight = colors.primary?.light || fb.colors.primary.light;
  const primaryDark = colors.primary?.dark || fb.colors.primary.dark;
  const secondaryMain = colors.secondary?.main || fb.colors.secondary.main;
  const textSecondary = colors.text?.secondary || fb.colors.text.secondary;

  const backgroundRgb = hexToRgb(backgroundBase) || hexToRgb(fb.colors.background.base) || '11, 18, 32';
  const surfaceRgb = hexToRgb(surfaceBase) || hexToRgb(fb.colors.background.surface) || '15, 23, 36';
  const surfaceAltRgb = hexToRgb(surfaceAlt) || hexToRgb(fb.colors.background.elevated) || '19, 29, 51';
  const primaryRgb = hexToRgb(primaryMain) || hexToRgb(fb.colors.primary.main) || '6, 182, 212';
  const primaryLightRgb = hexToRgb(primaryLight) || hexToRgb(fb.colors.primary.light) || '103, 232, 249';
  const primaryDarkRgb = hexToRgb(primaryDark) || hexToRgb(fb.colors.primary.dark) || '14, 116, 144';
  const secondaryRgb = hexToRgb(secondaryMain) || hexToRgb(fb.colors.secondary.main) || '139, 92, 246';
  const textSecondaryRgb = hexToRgb(textSecondary) || hexToRgb(fb.colors.text.secondary) || '148, 163, 184';

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
  setDimensionVar(root, 'core-primitive-radius', primitives?.radius?.surface, fb.primitives.radius.surface);
  setDimensionVar(root, 'shell-control-radius', primitives?.radius?.control, fb.primitives.radius.control);
  setDimensionVar(root, 'chat-bubble-radius', chat?.bubbleRadius || primitives?.radius?.bubble, fb.primitives.radius.bubble);

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

  const shell = ui.shell || fb.ui.shell;
  const shellHeader = shell.header || fb.ui.shell.header;
  const shellFooter = shell.footer || fb.ui.shell.footer;
  const shellFrame = shell.frame || fb.ui.shell.frame;
  const page = ui.page || fb.ui.page;
  const pageMaxWidth = normalizePageMaxWidthKeys(page.maxWidth || fb.ui.page.maxWidth);

  setDimensionVar(root, 'shell-frame-max-width', shellFrame.maxWidth || primitives?.measure?.shell, fb.primitives.measure.shell);
  setDimensionVar(root, 'shell-header-height', shellHeader.height, fb.ui.shell.header.height);
  setDimensionVar(root, 'shell-header-padding-x', shellHeader.paddingX, fb.ui.shell.header.paddingX);
  setDimensionVar(root, 'shell-header-gap', shellHeader.gap, fb.ui.shell.header.gap);
  setDimensionVar(root, 'shell-header-cluster-gap', shellHeader.clusterGap, fb.ui.shell.header.clusterGap);
  setDimensionVar(root, 'shell-header-nav-gap', shellHeader.navGap, fb.ui.shell.header.navGap);
  setDimensionVar(root, 'shell-header-nav-padding-left', shellHeader.navPaddingLeft, fb.ui.shell.header.navPaddingLeft);
  setDimensionVar(root, 'shell-header-control-gap', shellHeader.controlGap, fb.ui.shell.header.controlGap);
  setDimensionVar(root, 'shell-header-action-height', shellHeader.actionHeight, fb.ui.shell.header.actionHeight);
  setDimensionVar(root, 'shell-header-action-padding-x', shellHeader.actionPaddingX, fb.ui.shell.header.actionPaddingX);
  setDimensionVar(root, 'shell-header-utility-size', shellHeader.utilitySize, fb.ui.shell.header.utilitySize);
  setDimensionVar(root, 'shell-header-utility-padding', shellHeader.utilityPadding, fb.ui.shell.header.utilityPadding);
  setDimensionVar(root, 'shell-header-avatar-size', shellHeader.avatarSize, fb.ui.shell.header.avatarSize);
  setDimensionVar(root, 'shell-header-avatar-large-size', shellHeader.avatarLargeSize, fb.ui.shell.header.avatarLargeSize);
  setDimensionVar(root, 'shell-header-profile-height', shellHeader.profileHeight, fb.ui.shell.header.profileHeight);
  setDimensionVar(root, 'shell-header-profile-padding-x', shellHeader.profilePaddingX, fb.ui.shell.header.profilePaddingX);
  setDimensionVar(root, 'shell-header-panel-radius', shellHeader.panelRadius, fb.ui.shell.header.panelRadius);

  setDimensionVar(root, 'shell-footer-max-width', shellFooter.maxWidth, fb.ui.shell.footer.maxWidth);
  setDimensionVar(root, 'shell-footer-padding-y', shellFooter.paddingY, fb.ui.shell.footer.paddingY);
  setDimensionVar(root, 'shell-footer-padding-x', shellFooter.paddingX, fb.ui.shell.footer.paddingX);
  setDimensionVar(root, 'shell-footer-gap', shellFooter.gap, fb.ui.shell.footer.gap);

  setDimensionVar(root, 'shell-page-max-width-grid', pageMaxWidth.grid, fb.ui.page.maxWidth.grid);
  setDimensionVar(root, 'shell-page-max-width-sidebar', pageMaxWidth.sidebar, fb.ui.page.maxWidth.sidebar);
  setDimensionVar(root, 'shell-page-max-width-full-width', pageMaxWidth['full-width'], fb.ui.page.maxWidth['full-width']);
  setDimensionVar(root, 'shell-page-max-width-split', pageMaxWidth.split, fb.ui.page.maxWidth.split);
  setDimensionVar(root, 'shell-page-padding-x-base', page.paddingX?.base, fb.ui.page.paddingX.base);
  setDimensionVar(root, 'shell-page-padding-x-md', page.paddingX?.md, fb.ui.page.paddingX.md);
  setDimensionVar(root, 'shell-page-padding-x-xl', page.paddingX?.xl, fb.ui.page.paddingX.xl);
  setDimensionVar(root, 'shell-page-padding-y-base', page.paddingY?.base, fb.ui.page.paddingY.base);
  setDimensionVar(root, 'shell-page-padding-y-md', page.paddingY?.md, fb.ui.page.paddingY.md);
  setDimensionVar(root, 'shell-page-section-gap', page.sectionGap, fb.ui.page.sectionGap);
  setDimensionVar(root, 'shell-page-title-padding-bottom', page.titlePaddingBottom, fb.ui.page.titlePaddingBottom);

  setDimensionVar(root, 'chat-feed-max-width', chat.feedMaxWidth || primitives?.measure?.chat_feed, fb.primitives.measure.chat_feed);
  setDimensionVar(root, 'chat-feed-padding-top-base', chat.feedPaddingTop?.base, fb.ui.chat.feedPaddingTop.base);
  setDimensionVar(root, 'chat-feed-padding-top-sm', chat.feedPaddingTop?.sm, fb.ui.chat.feedPaddingTop.sm);
  setDimensionVar(root, 'chat-feed-padding-top-md', chat.feedPaddingTop?.md, fb.ui.chat.feedPaddingTop.md);
  setDimensionVar(root, 'chat-feed-padding-bottom-base', chat.feedPaddingBottom?.base, fb.ui.chat.feedPaddingBottom.base);
  setDimensionVar(root, 'chat-feed-padding-bottom-sm', chat.feedPaddingBottom?.sm, fb.ui.chat.feedPaddingBottom.sm);
  setDimensionVar(root, 'chat-feed-padding-bottom-md', chat.feedPaddingBottom?.md, fb.ui.chat.feedPaddingBottom.md);
  setDimensionVar(root, 'chat-feed-padding-x-base', chat.feedPaddingX?.base, fb.ui.chat.feedPaddingX.base);
  setDimensionVar(root, 'chat-feed-padding-x-sm', chat.feedPaddingX?.sm, fb.ui.chat.feedPaddingX.sm);
  setDimensionVar(root, 'chat-feed-padding-x-md', chat.feedPaddingX?.md, fb.ui.chat.feedPaddingX.md);
  setDimensionVar(root, 'chat-bubble-padding-y', chat.bubblePaddingY, fb.ui.chat.bubblePaddingY);
  setDimensionVar(root, 'chat-bubble-padding-x', chat.bubblePaddingX, fb.ui.chat.bubblePaddingX);
  setDimensionVar(root, 'chat-user-bubble-padding-y', chat.userBubblePaddingY, fb.ui.chat.userBubblePaddingY);
  setDimensionVar(root, 'chat-user-bubble-padding-x', chat.userBubblePaddingX, fb.ui.chat.userBubblePaddingX);
  setDimensionVar(root, 'chat-bubble-stack-gap', chat.bubbleGap, fb.ui.chat.bubbleGap);
  setDimensionVar(root, 'chat-bubble-header-gap', chat.bubbleHeaderGap, fb.ui.chat.bubbleHeaderGap);
  setDimensionVar(root, 'chat-name-pill-padding-y', chat.namePillPaddingY, fb.ui.chat.namePillPaddingY);
  setDimensionVar(root, 'chat-name-pill-padding-x', chat.namePillPaddingX, fb.ui.chat.namePillPaddingX);
  setDimensionVar(root, 'chat-name-pill-font-size', chat.namePillFontSize, fb.ui.chat.namePillFontSize);

  root.style.setProperty('--chat-bubble-shadow', `0 18px 40px rgba(${backgroundRgb}, 0.35)`);
  root.style.setProperty('--chat-user-bg', `linear-gradient(140deg, rgba(${primaryRgb}, 0.35), rgba(${primaryDarkRgb}, 0.45))`);
  root.style.setProperty('--chat-agent-bg', `linear-gradient(140deg, rgba(${surfaceRgb}, 0.90), rgba(${surfaceAltRgb}, 0.90))`);
  root.style.setProperty('--chat-user-border', `rgba(${primaryLightRgb}, 0.45)`);
  root.style.setProperty('--chat-agent-border', `rgba(${secondaryRgb}, 0.35)`);
  root.style.setProperty('--chat-divider-color', `rgba(${textSecondaryRgb}, 0.25)`);
  root.style.setProperty('--chat-input-bg', `rgba(${backgroundRgb}, 0.85)`);
  root.style.setProperty('--chat-input-border', `rgba(${textSecondaryRgb}, 0.35)`);
  root.style.setProperty('--chat-input-shadow', `0 15px 45px rgba(${backgroundRgb}, 0.45)`);
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
