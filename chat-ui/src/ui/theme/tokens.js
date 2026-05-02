/**
 * Theme token generator for the Mozaiks App UI system.
 *
 * Maps theme_config.json (from /api/theme-config or the active app root's brand/theme_config.json)
 * to CSS custom properties on a target element.
 *
 * All variables use the --mz-* namespace to avoid collisions with existing
 * chat-ui CSS variables (--color-*, --core-primitive-*, etc.).
 *
 * Internal — agents select from named options (primary: cyan), never raw CSS.
 */

import { palettes } from './palettes.js';
import { fontFamilies, fontImports } from './fonts.js';

const radiusScale = {
  none:   '0px',
  small:  '0.25rem',
  medium: '0.5rem',
  large:  '0.75rem',
  full:   '9999px',
};

const densityScale = {
  compact:     '0.2rem',
  comfortable: '0.25rem',
  spacious:    '0.3125rem',
};

/**
 * Generate CSS custom property map from a theme config object.
 *
 * @param {object} config - Theme config matching the theme schema in DESIGN_SYSTEM_SPEC.md
 * @returns {Record<string, string>} - CSS variable key → value map
 */
export function generateThemeTokens(config) {
  const {
    primary    = 'blue',
    variant    = 'default',   // reserved for Layer 1 primitive variants
    radius     = 'medium',
    appearance = 'system',
    font       = 'system',
    font_heading,
    density    = 'comfortable',
  } = config ?? {};

  const scale       = palettes[primary] ?? palettes.blue;
  const isDark      = appearance === 'dark';
  const spacingUnit = densityScale[density] ?? densityScale.comfortable;

  const base = isDark ? darkBase(scale) : lightBase(scale);

  return {
    ...base,

    // Primary brand color (driven by selected palette)
    '--mz-primary':            scale[500],
    '--mz-primary-foreground': scale[50],

    // Radius
    '--mz-radius': radiusScale[radius] ?? radiusScale.medium,

    // Typography
    '--mz-font-sans':    fontFamilies[font]          ?? fontFamilies.system,
    '--mz-font-heading': fontFamilies[font_heading]  ?? 'var(--mz-font-sans)',
    '--mz-font-mono':    "'JetBrains Mono', 'Fira Code', 'Courier New', monospace",

    // Density-driven spacing
    '--mz-spacing-unit': spacingUnit,
    '--mz-spacing-xs':   'calc(var(--mz-spacing-unit) * 1)',
    '--mz-spacing-sm':   'calc(var(--mz-spacing-unit) * 2)',
    '--mz-spacing-md':   'calc(var(--mz-spacing-unit) * 4)',
    '--mz-spacing-lg':   'calc(var(--mz-spacing-unit) * 6)',
    '--mz-spacing-xl':   'calc(var(--mz-spacing-unit) * 8)',
  };
}

function lightBase(scale) {
  return {
    '--mz-background':              '0 0% 100%',
    '--mz-foreground':              '222 84% 5%',
    '--mz-card':                    '0 0% 100%',
    '--mz-card-foreground':         '222 84% 5%',
    '--mz-popover':                 '0 0% 100%',
    '--mz-popover-foreground':      '222 84% 5%',
    '--mz-secondary':               '210 40% 96%',
    '--mz-secondary-foreground':    '222 47% 11%',
    '--mz-muted':                   '210 40% 96%',
    '--mz-muted-foreground':        '215 16% 47%',
    '--mz-accent':                  '210 40% 96%',
    '--mz-accent-foreground':       '222 47% 11%',
    '--mz-destructive':             '0 84% 60%',
    '--mz-destructive-foreground':  '210 40% 98%',
    '--mz-success':                 '142 76% 36%',
    '--mz-success-foreground':      '355 100% 97%',
    '--mz-warning':                 '48 96% 53%',
    '--mz-warning-foreground':      '26 83% 14%',
    '--mz-border':                  '214 32% 91%',
    '--mz-input':                   '214 32% 91%',
    '--mz-ring':                    scale[500],
  };
}

function darkBase(scale) {
  return {
    '--mz-background':              '222 84% 5%',
    '--mz-foreground':              '210 40% 98%',
    '--mz-card':                    '222 84% 5%',
    '--mz-card-foreground':         '210 40% 98%',
    '--mz-popover':                 '222 84% 5%',
    '--mz-popover-foreground':      '210 40% 98%',
    '--mz-secondary':               '217 33% 18%',
    '--mz-secondary-foreground':    '210 40% 98%',
    '--mz-muted':                   '217 33% 18%',
    '--mz-muted-foreground':        '215 20% 65%',
    '--mz-accent':                  '217 33% 18%',
    '--mz-accent-foreground':       '210 40% 98%',
    '--mz-destructive':             '0 63% 31%',
    '--mz-destructive-foreground':  '210 40% 98%',
    '--mz-success':                 '142 71% 45%',
    '--mz-success-foreground':      '145 80% 10%',
    '--mz-warning':                 '48 96% 53%',
    '--mz-warning-foreground':      '26 83% 14%',
    '--mz-border':                  '217 33% 18%',
    '--mz-input':                   '217 33% 18%',
    '--mz-ring':                    scale[400],
  };
}

/**
 * Apply a token map to a DOM element (defaults to :root).
 *
 * @param {Record<string, string>} tokens
 * @param {HTMLElement} [element=document.documentElement]
 */
export function applyThemeTokens(tokens, element = document.documentElement) {
  for (const [key, value] of Object.entries(tokens)) {
    element.style.setProperty(key, value);
  }
}

/**
 * Return Google Fonts import URLs needed for this config.
 * Deduplicates when font and font_heading resolve to the same import.
 *
 * @param {object} config - Theme config object
 * @returns {string[]} - Array of import URLs (empty strings filtered out)
 */
export function getFontImports(config) {
  const { font, font_heading } = config ?? {};
  const seen = new Set();
  const imports = [];
  for (const key of [font, font_heading]) {
    const url = fontImports[key];
    if (url && !seen.has(url)) {
      seen.add(url);
      imports.push(url);
    }
  }
  return imports;
}
