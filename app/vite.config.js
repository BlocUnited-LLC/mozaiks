import { defineConfig, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const appConfig = require('../platform/app.json');
const themeConfig = require('../platform/config/theme_config.json');

function resolveBrandPublicAsset(value) {
  if (!value || typeof value !== 'string') {
    return '/favicon.ico';
  }
  if (value.startsWith('/') || value.startsWith('http')) {
    return value;
  }
  return `/assets/${value}`;
}

export default defineConfig({
  plugins: [
    // Pre-process .js files that contain JSX.
    // Runs before Vite's own module analysis so JSX syntax doesn't trip the parser.
    {
      name: 'jsx-in-chat-ui-js',
      enforce: 'pre',
      async transform(code, id) {
        const isChatUiJsxInJs = /[\\/]chat-ui[\\/]src[\\/].*\.js$/.test(id);
        const isPlatformUiJsxInJs = /[\\/]platform[\\/](workflows|modules)[\\/].*[\\/]ui[\\/].*\.js$/.test(id);
        if (isChatUiJsxInJs || isPlatformUiJsxInJs) {
          return transformWithEsbuild(code, id, { loader: 'jsx', jsx: 'automatic', jsxImportSource: 'react' });
        }
      },
    },
    // Include .js files — chat-ui/src uses .js with JSX syntax
    react({ include: /\.(jsx|js)$/ }),
    // ── HTML token injection ─────────────────────────────────────────────────
    // Replaces HTML tokens in index.html with app/theme values.
    {
      name: 'html-inject-app-config',
      transformIndexHtml(html) {
        return html
          .replace(/__APP_NAME__/g, appConfig.appName)
          .replace(/__FAVICON_HREF__/g, resolveBrandPublicAsset(themeConfig?.assets?.favicon));
      },
    },
  ],
  publicDir: '../platform/brand',
  resolve: {
    // chat-ui/src files live outside this project root and import shared packages.
    // All dependencies are installed in chat-ui/node_modules — resolve from there.
    modules: [path.resolve(__dirname, '../chat-ui/node_modules'), 'node_modules'],
    alias: {
      // Resolves @mozaiks/chat-ui to the local source during development.
      // Points to the src directory so subpath imports (e.g. @mozaiks/chat-ui/coreBridge)
      // resolve correctly for platform module UIs.
      '@mozaiks/chat-ui': path.resolve(__dirname, '../chat-ui/src'),
      // Resolves @chat-workflows to the auto-discovery registry that scans platform/workflows.
      '@chat-workflows':  path.resolve(__dirname, '../chat-ui/src/@chat-workflows'),
      // Resolves @modules to the auto-discovery registry that scans platform/modules.
      '@modules':         path.resolve(__dirname, '../chat-ui/src/@modules'),
      // React Native Web: translate react-native imports to browser-compatible equivalents.
      // This allows chat-ui/src/ui/ components (built with RN primitives) to run in the browser.
      'react-native':     'react-native-web',
    },
  },
  // Shim process.env for src/config/index.js (written for CRA / Node env vars).
  // All reads have || fallback defaults so an empty object is safe.
  // IMPORTANT: esbuild define values must be valid JSON or JS identifiers.
  // Runtime expressions like window.location.* are NOT valid define values.
  // Instead, code that reads process.env.REACT_APP_WS_URL will get "" and
  // the fallback in config/index.js will compute the WS URL at runtime.
  define: {
    'process.env': JSON.stringify({}),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': { target: appConfig.apiUrl, changeOrigin: true },
      '/ws':  { target: appConfig.apiUrl.replace('http', 'ws'), ws: true },
    },
  },
  // chat-ui/src uses .js files that contain JSX — pre-bundle them correctly.
  optimizeDeps: {
    esbuildOptions: {
      loader: { '.js': 'jsx' },
    },
  },
});
