import { defineConfig, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const appConfig = require('./app.json');

export default defineConfig({
  plugins: [
    // Pre-process .js files from chat-ui/src that contain JSX.
    // Runs before Vite's own module analysis so JSX syntax doesn't trip the parser.
    {
      name: 'jsx-in-chat-ui-js',
      enforce: 'pre',
      async transform(code, id) {
        if (/[\\/]chat-ui[\\/]src[\\/].*\.js$/.test(id)) {
          return transformWithEsbuild(code, id, { loader: 'jsx', jsx: 'automatic', jsxImportSource: 'react' });
        }
      },
    },
    // Include .js files — chat-ui/src uses .js with JSX syntax
    react({ include: /\.(jsx|js)$/ }),
    // ── HTML token injection ─────────────────────────────────────────────────
    // Replaces %APP_NAME% in index.html with the value from app.json.
    {
      name: 'html-inject-app-config',
      transformIndexHtml(html) {
        return html.replace(/%APP_NAME%/g, appConfig.appName);
      },
    },
  ],
  publicDir: './brand/public',
  resolve: {
    // chat-ui/src files live outside this project root and import shared packages.
    // All dependencies are installed in chat-ui/node_modules — resolve from there.
    modules: [path.resolve(__dirname, '../chat-ui/node_modules'), 'node_modules'],
    alias: {
      // Resolves @mozaiks/chat-ui to the local source during development.
      // In a published app this would be the installed npm package.
      '@mozaiks/chat-ui': path.resolve(__dirname, '../chat-ui/src/index.js'),
      // Resolves @chat-workflows to the workflow UI component registry.
      '@chat-workflows':  path.resolve(__dirname, '../chat-ui/src/workflows'),
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
