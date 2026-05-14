/**
 * Tailwind configuration for the Mozaiks app shell.
 *
 * ZERO-TOUCH — users never edit this file. It is configured once at template
 * development time per PLATFORM_FRONTEND_STRATEGY.md.
 *
 * Content paths cover:
 *   - web_shell/    — the shell itself
 *   - chat-ui/src/ or mozaiks_chat_ui/src/ — chat UI + App UI primitives
 *   - *-platform/   — product/feature platform directories (mozaiks-platform, etc.)
 *   - active app and factory app env paths — external app workspaces in dev
 *
 * All dynamic brand values (colors, radius, fonts) are applied via CSS custom
 * properties (--mz-*) at runtime by themeProvider.js. Tailwind classes reference
 * those variables, so no rebuild is needed when the theme changes.
 */

function normalizeContentPath(value) {
  if (!value || typeof value !== 'string') return null;
  return value.replace(/\\/g, '/').replace(/\/+$/, '');
}

function workspaceContentPaths(value) {
  const base = normalizeContentPath(value);
  if (!base) return [];
  return [
    `${base}/ui/**/*.{js,jsx,ts,tsx}`,
    `${base}/app/ui/**/*.{js,jsx,ts,tsx}`,
    `${base}/workflows/**/*.{js,jsx,ts,tsx}`,
  ];
}

const chatUiEnvPath = normalizeContentPath(process.env.MOZAIKS_CHAT_UI_PATH);
const envContentPaths = [
  ...workspaceContentPaths(process.env.PLATFORM_PATH),
  ...workspaceContentPaths(process.env.MOZAIKS_APP_WORKSPACE_PATH),
  ...workspaceContentPaths(process.env.MOZAIKS_FACTORY_APP_PATH),
  ...(chatUiEnvPath ? [`${chatUiEnvPath}/src/**/*.{js,jsx,ts,tsx}`] : []),
];

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './*.{js,jsx}',
    '../chat-ui/src/**/*.{js,jsx,ts,tsx}',
    '../mozaiks_chat_ui/src/**/*.{js,jsx,ts,tsx}',
    '../factory_app/app/ui/**/*.{js,jsx,ts,tsx}',
    '../factory_app/workflows/**/*.{js,jsx,ts,tsx}',
    // Any product/feature platform layer (*-platform/) — covers mozaiks-platform
    // and any future platform products without requiring config changes.
    '../*-platform/**/*.{js,jsx,ts,tsx}',
    ...envContentPaths,
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
