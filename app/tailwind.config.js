/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './*.{js,jsx}',
    './workflows/**/*.{js,jsx}',
    '../chat-ui/src/**/*.{js,jsx,ts,tsx}',
    '../../packages/frontend/chat-ui/src/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // Font utilities used by the design system.
        // The actual font families are injected as CSS variables by applyBrand().
        sans:    ['var(--font-body,Rajdhani)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        heading: ['var(--font-heading,Orbitron)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        logo:    ['var(--font-logo,Fagrak Inline)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      borderWidth: {
        '3': '3px',
      },
    },
  },
  plugins: [],
};
