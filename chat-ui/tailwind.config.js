/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './src/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        background: 'hsl(var(--mz-background))',
        foreground: 'hsl(var(--mz-foreground))',
        primary: {
          DEFAULT: 'hsl(var(--mz-primary))',
          foreground: 'hsl(var(--mz-primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--mz-secondary))',
          foreground: 'hsl(var(--mz-secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--mz-muted))',
          foreground: 'hsl(var(--mz-muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--mz-accent))',
          foreground: 'hsl(var(--mz-accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--mz-destructive))',
          foreground: 'hsl(var(--mz-destructive-foreground))',
        },
        success: {
          DEFAULT: 'hsl(var(--mz-success))',
          foreground: 'hsl(var(--mz-success-foreground))',
        },
        warning: {
          DEFAULT: 'hsl(var(--mz-warning))',
          foreground: 'hsl(var(--mz-warning-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--mz-card))',
          foreground: 'hsl(var(--mz-card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--mz-popover))',
          foreground: 'hsl(var(--mz-popover-foreground))',
        },
        border: 'hsl(var(--mz-border))',
        input: 'hsl(var(--mz-input))',
        ring: 'hsl(var(--mz-ring))',
      },
      borderRadius: {
        lg: 'var(--mz-radius)',
        md: 'calc(var(--mz-radius) - 2px)',
        sm: 'calc(var(--mz-radius) - 4px)',
      },
      fontFamily: {
        sans: ['var(--mz-font-sans)', 'system-ui', 'sans-serif'],
        heading: ['var(--mz-font-heading)', 'var(--mz-font-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--mz-font-mono)', 'monospace'],
      },
    },
  },
  plugins: [],
};
