// ============================================================================
// FILE: chat-ui/src/styles/artifactDesignSystem.js
// PURPOSE: Full design system for shell runtime (extends chat-ui's minimal tokens)
// USAGE: Import into workflow UI components to maintain consistency
// NOTE: This module uses Tailwind utility classes that resolve dynamically
//       based on the active theme loaded via themeProvider.js
// ============================================================================

/**
 * FONT SYSTEM
 * Uses Tailwind font utilities that map to theme-configured fonts
 * The actual font families are loaded dynamically per app
 * via themeProvider.js and tailwind.config.js
 * 
 * Default (MozaiksAI brand):
 * - Body/Default: Rajdhani (font-sans)
 * - Headings: Orbitron (font-heading)
 * - Logo/Branding: Fagrak Inline (font-logo)
 * 
 * These can be overridden per app via theme API
 */
export const fonts = {
  // Body text - default across all components
  // Resolves to theme.fonts.body.family (e.g., Rajdhani)
  body: 'font-sans',
  
  // Headings (h1-h6, section titles)
  // Resolves to theme.fonts.heading.family (e.g., Orbitron)
  heading: 'font-heading',
  
  // Branding elements (logos, special callouts)
  // Resolves to theme.fonts.logo.family (e.g., Fagrak Inline)
  logo: 'font-logo',
};

/**
 * Generate a single Tailwind arbitrary-value class backed by a CSS variable.
 * The brand hex value is embedded as the var() fallback — so the class renders
 * correctly to the template brand even before CSS variables are explicitly
 * applied (e.g. during SSR or pre-hydration). Single declaration; no competing
 * duplicate class.
 */
const createVarClass = (type, varName, brandHex = '') => {
  const expr = brandHex ? `var(--${varName},${brandHex})` : `var(--${varName})`;
  const map = {
    bg:     `bg-[${expr}]`,
    border: `border-[${expr}]`,
    text:   `text-[${expr}]`,
    ring:   `ring-[${expr}]`,
  };
  return map[type] || '';
};

/**
 * Alpha variant: uses the companion *-rgb variable.
 * brandRgb should be the "r,g,b" triplet matching the color's hex value.
 */
const createAlphaVarClass = (type, varName, alpha, brandRgb = '0,0,0') => {
  const sanitized = Math.max(0, Math.min(1, Number(alpha) || 0));
  return `${type}-[rgba(var(--${varName}-rgb,${brandRgb}),${sanitized})]`;
};

/**
 * Shadow: CSS variable holding a full box-shadow value.
 * Underscores encode spaces inside Tailwind arbitrary values.
 */
const createShadowVarClass = (varName, brandShadow = '0_0_0_rgba(0,0,0,0)') => {
  return `[box-shadow:var(--${varName},${brandShadow})]`;
};

/**
 * Gradient stop backed by CSS variable with brand-value fallback.
 */
const createGradientStopVar = (stop, varName, brandHex = '') => {
  const expr = brandHex ? `var(--${varName},${brandHex})` : `var(--${varName})`;
  return `${stop}-[${expr}]`;
};

// ─────────────────────────────────────────────────────────────────────────────
// BRAND TOKENS
// These values are sourced directly from template/brands/public/brand.json.
// They serve as the CSS var() fallback values embedded inside each class —
// so the template brand IS the rendered value, not a degraded fallback.
//
// When you update brand.json, update these values too (and vice versa).
// ─────────────────────────────────────────────────────────────────────────────
const BRAND_TOKENS = {
  brand: {
    //                          bg          border       text (on bg)   ring         rgb (precomputed for alpha classes)
    primary:     { bg: '#06b6d4', border: '#06b6d4', text: '#f8fafc', ring: '#67e8f9', rgb: '6,182,212' },
    primaryLight:{ bg: '#67e8f9', border: '#67e8f9', text: '#0b1220', ring: '#67e8f9', rgb: '103,232,249' },
    primaryDark: { bg: '#0e7490', border: '#0e7490', text: '#f8fafc', ring: '#0e7490', rgb: '14,116,144' },
    secondary:   { bg: '#8b5cf6', border: '#8b5cf6', text: '#f8fafc', ring: '#a78bfa', rgb: '139,92,246' },
    accent:      { bg: '#f59e0b', border: '#f59e0b', text: '#0b1220', ring: '#fbbf24', rgb: '245,158,11' },
  },
  status: {
    success:     { bg: '#10b981', border: '#10b981', text: '#f8fafc', ring: '#10b981', rgb: '16,185,129' },
    warning:     { bg: '#f59e0b', border: '#f59e0b', text: '#0b1220', ring: '#fbbf24', rgb: '245,158,11' },
    error:       { bg: '#ef4444', border: '#ef4444', text: '#f8fafc', ring: '#ef4444', rgb: '239,68,68'  },
  },
  // Surface values from brand.json colors.background
  surface: {
    base:         '#0b1220',          // background.base
    baseRgb:      '11,18,32',
    raised:       '#0f1724',          // background.surface
    raisedRgb:    '15,23,36',
    elevated:     '#131d33',          // background.elevated
    elevatedRgb:  '19,29,51',
    overlay:      'rgba(13,23,42,0.72)',
  },
  border: {
    subtle: '#1e293b',
    strong: '#334155',
    accent: '#06b6d4',
  },
  text: {
    primary:   '#e6eef8',
    secondary: '#94a3b8',
    muted:     '#64748b',
    onAccent:  '#f8fafc',
  },
  // Shadow strings — underscores encode spaces for Tailwind arbitrary values
  shadow: {
    primary:   '0_20px_45px_rgba(6,182,212,0.24)',
    secondary: '0_20px_45px_rgba(139,92,246,0.24)',
    accent:    '0_18px_40px_rgba(245,158,11,0.32)',
    success:   '0_18px_40px_rgba(16,185,129,0.24)',
    warning:   '0_18px_45px_rgba(245,158,11,0.34)',
    error:     '0_18px_45px_rgba(239,68,68,0.3)',
    elevated:  '0_24px_60px_rgba(11,18,32,0.55)',
    focus:     '0_0_0_3px_rgba(8,145,178,0.55)',
  },
};

const createColorScale = (varName, tokens = {}) => ({
  bg:     createVarClass('bg',     varName, tokens.bg),
  border: createVarClass('border', varName, tokens.border),
  text:   createVarClass('text',   varName, tokens.text),
  ring:   createVarClass('ring',   varName, tokens.ring),
});

export const colors = {
  brand: {
    primary:     createColorScale('color-primary',       BRAND_TOKENS.brand.primary),
    primaryLight:createColorScale('color-primary-light', BRAND_TOKENS.brand.primaryLight),
    primaryDark: createColorScale('color-primary-dark',  BRAND_TOKENS.brand.primaryDark),
    secondary:   createColorScale('color-secondary',     BRAND_TOKENS.brand.secondary),
    accent:      createColorScale('color-accent',        BRAND_TOKENS.brand.accent),
  },
  status: {
    success: createColorScale('color-success', BRAND_TOKENS.status.success),
    warning: createColorScale('color-warning', BRAND_TOKENS.status.warning),
    error:   createColorScale('color-error',   BRAND_TOKENS.status.error),
  },
  surface: {
    base:         createVarClass('bg', 'color-background',     BRAND_TOKENS.surface.base),
    baseOverlay:  createAlphaVarClass('bg', 'color-background', 0.85, BRAND_TOKENS.surface.baseRgb),
    raised:       createVarClass('bg', 'color-surface',        BRAND_TOKENS.surface.raised),
    raisedOverlay:createAlphaVarClass('bg', 'color-surface',    0.75, BRAND_TOKENS.surface.raisedRgb),
    elevated:     createVarClass('bg', 'color-surface-alt',    BRAND_TOKENS.surface.elevated),
    overlay:      `bg-[var(--color-surface-overlay,${BRAND_TOKENS.surface.overlay})]`,
  },
  border: {
    subtle: createVarClass('border', 'color-border-subtle', BRAND_TOKENS.border.subtle),
    strong: createVarClass('border', 'color-border-strong', BRAND_TOKENS.border.strong),
    accent: createVarClass('border', 'color-border-accent', BRAND_TOKENS.border.accent),
  },
  text: {
    primary:   createVarClass('text', 'color-text-primary',   BRAND_TOKENS.text.primary),
    secondary: createVarClass('text', 'color-text-secondary', BRAND_TOKENS.text.secondary),
    muted:     createVarClass('text', 'color-text-muted',     BRAND_TOKENS.text.muted),
    onAccent:  createVarClass('text', 'color-text-on-accent', BRAND_TOKENS.text.onAccent),
  },
};

export const shadows = {
  primary:   createShadowVarClass('shadow-primary',   BRAND_TOKENS.shadow.primary),
  secondary: createShadowVarClass('shadow-secondary', BRAND_TOKENS.shadow.secondary),
  accent:    createShadowVarClass('shadow-accent',    BRAND_TOKENS.shadow.accent),
  success:   createShadowVarClass('shadow-success',   BRAND_TOKENS.shadow.success),
  warning:   createShadowVarClass('shadow-warning',   BRAND_TOKENS.shadow.warning),
  error:     createShadowVarClass('shadow-error',     BRAND_TOKENS.shadow.error),
  elevated:  createShadowVarClass('shadow-elevated',  BRAND_TOKENS.shadow.elevated),
  focus:     createShadowVarClass('shadow-focus',     BRAND_TOKENS.shadow.focus),
};

export const gradients = {
  surface: {
    from: createGradientStopVar('from', 'color-surface-alt', BRAND_TOKENS.surface.elevated),
    to:   createGradientStopVar('to',   'color-surface',     BRAND_TOKENS.surface.raised),
  },
  accent: {
    from: createGradientStopVar('from', 'color-primary',   BRAND_TOKENS.brand.primary.bg),
    to:   createGradientStopVar('to',   'color-secondary', BRAND_TOKENS.brand.secondary.bg),
  },
};

/**
 * TYPOGRAPHY SCALE
 * Consistent sizing for text elements
 */
export const typography = {
  // Display/Hero text
  display: {
    xl: `text-5xl ${fonts.heading} font-black`, // Main artifact titles
    lg: `text-4xl ${fonts.heading} font-black`,
    md: `text-3xl ${fonts.heading} font-bold`,
  },
  
  // Headings
  heading: {
    xl: `text-2xl ${fonts.heading} font-bold`,
    lg: `text-xl ${fonts.heading} font-black`,
    md: `text-lg ${fonts.heading} font-bold`,
    sm: `text-base ${fonts.heading} font-bold`,
    xs: `text-sm ${fonts.heading} font-bold`,
  },
  
  // Body text
  body: {
    lg: `text-base ${fonts.body}`,
    md: `text-sm ${fonts.body}`,
    sm: `text-xs ${fonts.body}`,
  },
  
  // Labels/badges
  label: {
    lg: `text-sm ${fonts.body} font-bold uppercase tracking-wider`,
    md: `text-xs ${fonts.body} font-bold uppercase tracking-wide`,
    sm: `text-[11px] ${fonts.body} font-bold uppercase tracking-wider`,
  },
};

/**
 * SPACING SYSTEM
 * Consistent padding/gaps
 */
export const spacing = {
  section: 'space-y-8', // Between major sections
  subsection: 'space-y-6', // Between subsections
  group: 'space-y-4', // Between related items
  tight: 'space-y-3', // Tight groupings
  items: 'space-y-2', // Individual items
  
  padding: {
    xl: 'p-8',
    lg: 'p-6',
    md: 'p-5',
    sm: 'p-4',
    xs: 'p-3',
  },
  
  gap: {
    lg: 'gap-5',
    md: 'gap-4',
    sm: 'gap-3',
    xs: 'gap-2',
  },
};

/**
 * COMPONENT PATTERNS
 * Reusable component style patterns
 */
export const components = {
  // Card styles
  card: {
    primary: [
      'rounded-2xl border-3',
      colors.border.accent,
      'bg-gradient-to-br',
      gradients.surface.from,
      gradients.surface.to,
      shadows.primary,
      'shadow-2xl',
      spacing.padding.xl,
    ].join(' '),
    secondary: [
      'rounded-xl border-2',
      colors.border.subtle,
      colors.surface.raised,
      shadows.elevated,
      spacing.padding.lg,
    ].join(' '),
    ghost: [
      'rounded-lg border',
      colors.border.subtle,
      colors.surface.raisedOverlay,
      spacing.padding.md,
    ].join(' '),
  },
  
  // Button styles
  button: {
    primary: [
      'rounded-lg',
      colors.brand.primary.bg,
      'px-6 py-3',
      typography.label.md,
      colors.text.onAccent,
      'transition-all',
      `hover:bg-[var(--color-primary-light,${BRAND_TOKENS.brand.primaryLight.bg})]`,
      'disabled:opacity-60',
    ].join(' '),
    secondary: [
      'rounded-lg border-2',
      colors.border.subtle,
      colors.surface.raised,
      'px-6 py-3',
      typography.label.md,
      colors.text.secondary,
      'transition-all',
      `hover:border-[var(--color-border-strong,${BRAND_TOKENS.border.strong})]`,
      `hover:bg-[rgba(var(--color-surface-rgb,${BRAND_TOKENS.surface.raisedRgb}),0.92)]`,
      'disabled:opacity-60',
    ].join(' '),
    ghost: [
      'rounded-lg border',
      colors.border.subtle,
      colors.surface.raisedOverlay,
      'px-4 py-2',
      typography.body.md,
      colors.text.secondary,
      'transition',
      `hover:border-[var(--color-border-accent,${BRAND_TOKENS.border.accent})]`,
      `hover:bg-[rgba(var(--color-surface-alt-rgb,${BRAND_TOKENS.surface.elevatedRgb}),0.75)]`,
      'disabled:opacity-60',
    ].join(' '),
  },

  // Panel / container surfaces shared across artifact and inline components
  panel: {
    inline: [
      'rounded-xl border backdrop-blur-md shadow-xl transition-all',
      createAlphaVarClass('border', 'color-primary', 0.35, BRAND_TOKENS.brand.primary.rgb),
      createAlphaVarClass('bg', 'color-surface', 0.78, BRAND_TOKENS.surface.raisedRgb),
      shadows.primary,
      spacing.padding.lg,
    ].join(' '),
    subtle: [
      'rounded-lg border backdrop-blur-sm transition-colors',
      colors.border.subtle,
      colors.surface.raisedOverlay,
      spacing.padding.md,
    ].join(' '),
    modal: [
      'rounded-2xl border-2 shadow-2xl backdrop-blur-lg transition-all',
      colors.border.accent,
      colors.surface.raised,
      shadows.elevated,
      spacing.padding.xl,
    ].join(' '),
  },

  // Input styles with theme-aware borders and focus states
  input: {
    base: [
      'w-full rounded-lg border px-4 py-3 transition-colors',
      createAlphaVarClass('border', 'color-primary', 0.28, BRAND_TOKENS.brand.primary.rgb),
      createAlphaVarClass('bg', 'color-surface-alt', 0.5, BRAND_TOKENS.surface.elevatedRgb),
      colors.text.primary,
      `placeholder:text-[rgba(var(--color-text-muted-rgb,100,116,139),0.7)]`,
      'focus:outline-none focus:ring-2',
      `focus:ring-[var(--color-primary-light,${BRAND_TOKENS.brand.primaryLight.bg})]`,
      `focus:border-[var(--color-primary,${BRAND_TOKENS.brand.primary.bg})]`,
    ].join(' '),
    error: [
      `border-[var(--color-error,${BRAND_TOKENS.status.error.bg})]`,
      `focus:ring-[var(--color-error,${BRAND_TOKENS.status.error.bg})]`,
      `focus:border-[var(--color-error,${BRAND_TOKENS.status.error.bg})]`,
    ].join(' '),
    disabled: 'opacity-50 cursor-not-allowed',
    withIcon: 'pr-12',
  },
  
  // Badge/chip styles
  badge: {
    primary: [
      'inline-flex items-center',
      spacing.gap.xs,
      'rounded-lg border-2',
      createAlphaVarClass('border', 'color-primary', 0.45, BRAND_TOKENS.brand.primary.rgb),
      createAlphaVarClass('bg', 'color-primary', 0.12, BRAND_TOKENS.brand.primary.rgb),
      colors.brand.primary.text,
      'px-4 py-2',
      typography.label.md,
    ].join(' '),
    secondary: [
      'inline-flex items-center',
      spacing.gap.xs,
      'rounded-lg border-2',
      createAlphaVarClass('border', 'color-secondary', 0.45, BRAND_TOKENS.brand.secondary.rgb),
      createAlphaVarClass('bg', 'color-secondary', 0.12, BRAND_TOKENS.brand.secondary.rgb),
      colors.brand.secondary.text,
      'px-4 py-2',
      typography.label.md,
    ].join(' '),
    success: [
      'inline-flex items-center',
      spacing.gap.xs,
      'rounded-lg border-2',
      createAlphaVarClass('border', 'color-success', 0.45, BRAND_TOKENS.status.success.rgb),
      createAlphaVarClass('bg', 'color-success', 0.12, BRAND_TOKENS.status.success.rgb),
      colors.status.success.text,
      'px-4 py-2',
      typography.label.md,
    ].join(' '),
    warning: [
      'inline-flex items-center',
      spacing.gap.xs,
      'rounded-lg',
      colors.status.warning.bg,
      'px-4 py-2.5',
      typography.label.md,
      colors.text.onAccent,
      shadows.warning,
      'shadow-lg',
    ].join(' '),
    neutral: [
      'inline-flex items-center',
      spacing.gap.sm,
      'rounded-lg border-2',
      colors.border.subtle,
      colors.surface.raised,
      'px-5 py-3',
      typography.body.lg,
      colors.text.primary,
      'font-bold',
    ].join(' '),
  },
  
  // Section header
  sectionHeader: [
    'flex items-center',
    spacing.gap.sm,
    'rounded-lg',
    colors.surface.raised,
    'px-6 py-4',
    'border-l-4',
    colors.border.accent,
    colors.text.primary,
  ].join(' '),
  
  // Accordion item (closed)
  accordionClosed: [
    'overflow-hidden rounded-2xl border-3',
    colors.border.subtle,
    colors.surface.raisedOverlay,
    'transition-all',
    shadows.elevated,
  ].join(' '),
  
  // Accordion item (open)
  accordionOpen: [
    'overflow-hidden rounded-2xl border-3',
    colors.border.accent,
    colors.surface.raised,
    'shadow-2xl',
    shadows.primary,
    'transition-all',
  ].join(' '),
  
  // Icon container
  iconContainer: {
    primary: [
      'rounded-lg',
      createAlphaVarClass('bg',   'color-primary',       0.2,  BRAND_TOKENS.brand.primary.rgb),
      'p-2.5 ring-2',
      createAlphaVarClass('ring', 'color-primary-light', 0.5,  BRAND_TOKENS.brand.primaryLight.rgb),
    ].join(' '),
    secondary: [
      'rounded-lg',
      createAlphaVarClass('bg',   'color-secondary',       0.2, BRAND_TOKENS.brand.secondary.rgb),
      'p-2.5 ring-2',
      createAlphaVarClass('ring', 'color-secondary-light', 0.5, '167,139,250'),
    ].join(' '),
    warning: [
      'rounded-lg',
      createAlphaVarClass('bg',   'color-warning', 0.2,  BRAND_TOKENS.status.warning.rgb),
      'p-2.5 ring-2',
      createAlphaVarClass('ring', 'color-warning', 0.45, BRAND_TOKENS.status.warning.rgb),
    ].join(' '),
  },
};

/**
 * LAYOUT PATTERNS
 * Common layout structures
 */
export const layouts = {
  // Full-bleed artifact container
  artifactContainer: [
    'min-h-screen',
    spacing.section,
    'rounded-2xl',
    colors.surface.base,
    spacing.padding.xl,
  ].join(' '),
  
  // Two-column grid (modules + flowchart)
  twoColumnGrid: 'grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]',
  
  // Tool grid
  toolGrid: 'grid grid-cols-1 gap-3 lg:grid-cols-2',
};

/**
 * UTILITY HELPERS
 * Common utility class combinations
 */
export const utils = {
  // Text truncation
  truncate: 'truncate',
  lineClamp: (lines) => `line-clamp-${lines}`,
  
  // Transitions
  transition: {
    all: 'transition-all',
    colors: 'transition-colors',
  },
  
  // Hover effects
  hover: {
    lift: `hover:shadow-xl hover:[box-shadow:var(--shadow-primary,${BRAND_TOKENS.shadow.primary})]`,
    border: `hover:border-[var(--color-primary-light,${BRAND_TOKENS.brand.primaryLight.bg})]`,
  },
};

/**
 * EXAMPLE USAGE:
 * 
 * import { typography, colors, components, spacing } from '../../../styles/artifactDesignSystem';
 * 
 * <div className={components.card.primary}>
 *   <h1 className={`${typography.display.xl} ${colors.text.primary}`}>Workflow Name</h1>
 *   <p className={`${typography.body.md} ${colors.text.secondary}`}>Description</p>
 *   <button className={components.button.primary}>Approve</button>
 * </div>
 */

// Export everything as a single default object for convenience
const designSystem = {
  fonts,
  colors,
  shadows,
  gradients,
  typography,
  spacing,
  components,
  layouts,
  utils,
};

export default designSystem;
