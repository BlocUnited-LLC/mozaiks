/**
 * Tailwind configuration for the Mozaiks app shell.
 *
 * ZERO-TOUCH — users never edit this file. It is configured once at template
 * development time per PLATFORM_FRONTEND_STRATEGY.md.
 *
 * Content paths cover:
 *   - app/          — the shell itself
 *   - chat-ui/src/  — all chat UI + App UI primitives (always present)
 *   - platform/     — canonical OSS app bundle location
 *   - *-platform/   — any product/feature platform directory (mozaiks-platform, etc.)
 *
 * All dynamic brand values (colors, radius, fonts) are applied via CSS custom
 * properties (--mz-*) at runtime by themeProvider.js. Tailwind classes reference
 * those variables, so no rebuild is needed when the theme changes.
 */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './*.{js,jsx}',
    '../chat-ui/src/**/*.{js,jsx,ts,tsx}',
    // Standard OSS app bundle (platform/)
    '../platform/**/*.{js,jsx,ts,tsx}',
    // Any product/feature platform layer (*-platform/) — covers mozaiks-platform
    // and any future platform products without requiring config changes.
    '../*-platform/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      // ── App UI token colors (--mz-*) ────────────────────────────────────
      // Set at runtime by themeProvider.js from PLATFORM_PATH/brand/theme_config.json.
      // Tailwind classes (bg-primary, text-muted-foreground, etc.) resolve through
      // these variables, so zero rebuild is needed when theme changes.
      colors: {
        background:  'hsl(var(--mz-background))',
        foreground:  'hsl(var(--mz-foreground))',
        primary: {
          DEFAULT:    'hsl(var(--mz-primary))',
          foreground: 'hsl(var(--mz-primary-foreground))',
        },
        secondary: {
          DEFAULT:    'hsl(var(--mz-secondary))',
          foreground: 'hsl(var(--mz-secondary-foreground))',
        },
        muted: {
          DEFAULT:    'hsl(var(--mz-muted))',
          foreground: 'hsl(var(--mz-muted-foreground))',
        },
        accent: {
          DEFAULT:    'hsl(var(--mz-accent))',
          foreground: 'hsl(var(--mz-accent-foreground))',
        },
        destructive: {
          DEFAULT:    'hsl(var(--mz-destructive))',
          foreground: 'hsl(var(--mz-destructive-foreground))',
        },
        success: {
          DEFAULT:    'hsl(var(--mz-success))',
          foreground: 'hsl(var(--mz-success-foreground))',
        },
        warning: {
          DEFAULT:    'hsl(var(--mz-warning))',
          foreground: 'hsl(var(--mz-warning-foreground))',
        },
        card: {
          DEFAULT:    'hsl(var(--mz-card))',
          foreground: 'hsl(var(--mz-card-foreground))',
        },
        popover: {
          DEFAULT:    'hsl(var(--mz-popover))',
          foreground: 'hsl(var(--mz-popover-foreground))',
        },
        border: 'hsl(var(--mz-border))',
        input:  'hsl(var(--mz-input))',
        ring:   'hsl(var(--mz-ring))',
      },
      borderRadius: {
        lg: 'var(--mz-radius)',
        md: 'calc(var(--mz-radius) - 2px)',
        sm: 'calc(var(--mz-radius) - 4px)',
      },
      fontFamily: {
        // App UI fonts — resolved at runtime from theme_config
        sans:    ['var(--mz-font-sans)',    'system-ui', 'sans-serif'],
        heading: ['var(--mz-font-heading)', 'var(--mz-font-sans)', 'system-ui', 'sans-serif'],
        mono:    ['var(--mz-font-mono)',    'monospace'],
        // Legacy chat UI fonts — used by chat shell components (--font-* vars)
        'chat-body':    ['var(--font-body,Rajdhani)',          'ui-sans-serif', 'system-ui', 'sans-serif'],
        'chat-heading': ['var(--font-heading,Orbitron)',        'ui-sans-serif', 'system-ui', 'sans-serif'],
        logo:           ['var(--font-logo,Fagrak Inline)',      'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      borderWidth: {
        '3': '3px',
      },
    },
  },
  plugins: [],
};
