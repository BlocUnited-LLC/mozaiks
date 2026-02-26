import { defineConfig, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const appConfig = require('../app.json');

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
    // ── Mock API ────────────────────────────────────────────────────────────
    // Returns sensible stub responses so the app degrades cleanly without a
    // running backend. Remove this block (and restore the proxy below) once
    // your backend is ready.
    {
      name: 'mock-api',
      configureServer(server) {
        server.middlewares.use('/api', (req, res) => {
          res.setHeader('Content-Type', 'application/json');
          if (req.url?.startsWith('/themes/')) {
            // 404 → themeProvider falls back to /brand.json automatically
            res.statusCode = 404;
            res.end(JSON.stringify({ error: 'No app theme — using brand.json' }));
          } else if (req.url?.startsWith('/workflows')) {
            // Return an empty workflow registry — app uses locally registered workflows
            res.statusCode = 200;
            res.end(JSON.stringify({}));
          } else {
            res.statusCode = 503;
            res.end(JSON.stringify({ error: 'Backend not running (dev mode)' }));
          }
        });
      },
    },
  ],
  publicDir: './brand/public',
  resolve: {
    // chat-ui/src files live outside this project root and import shared packages.
    // All dependencies are installed in chat-ui/node_modules — resolve from there.
    modules: [path.resolve(__dirname, '../../packages/frontend/chat-ui/node_modules'), 'node_modules'],
    alias: {
      // Resolves @mozaiks/chat-ui to the local source during development.
      // In a published app this would be the installed npm package.
      '@mozaiks/chat-ui': path.resolve(__dirname, '../../packages/frontend/chat-ui/src/index.js'),
      '@chat-workflows':  path.resolve(__dirname, 'workflows'),
    },
  },
  // Shim process.env for src/config/index.js (written for CRA / Node env vars).
  // All reads have || fallback defaults so an empty object is safe.
  // REACT_APP_API_BASE_URL is set to the current origin so workflowConfig.js
  // hits the Vite dev server (where mock-api intercepts it) instead of
  // hardcoded http://localhost:8080.
  define: {
    'process.env': JSON.stringify({}),
    'process.env.REACT_APP_API_BASE_URL': 'window.location.origin',
    'process.env.REACT_APP_WS_URL': 'window.location.origin.replace("http","ws")',
  },
  server: {
    port: 3000,
    // Proxy to your backend — uncomment when your backend is running:
    // proxy: {
    //   '/api': { target: appConfig.apiUrl, changeOrigin: true },
    //   '/ws':  { target: appConfig.apiUrl.replace('http', 'ws'), ws: true },
    // },
  },
  // chat-ui/src uses .js files that contain JSX — pre-bundle them correctly.
  optimizeDeps: {
    esbuildOptions: {
      loader: { '.js': 'jsx' },
    },
  },
});
