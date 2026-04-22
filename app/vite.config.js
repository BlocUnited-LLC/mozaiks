import { defineConfig, loadEnv, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import fs from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require   = createRequire(import.meta.url);

// ── Platform resolution ────────────────────────────────────────────────────
// PLATFORM_PATH (from root .env) may point to either:
// - an app bundle directory that contains app.json (e.g. mozaiks-platform/app)
// - a platform root that contains ./app/app.json (future-proof support)
//
// When unset, it defaults to the OSS app bundle at ./platform.
const projectRoot = path.resolve(__dirname, '..');

function resolveAppBundleDir(platformInputPath) {
  const directAppJson = path.join(platformInputPath, 'app.json');
  if (fs.existsSync(directAppJson)) return platformInputPath;

  const nestedAppDir = path.join(platformInputPath, 'app');
  const nestedAppJson = path.join(nestedAppDir, 'app.json');
  if (fs.existsSync(nestedAppJson)) return nestedAppDir;

  return platformInputPath;
}

function resolveFirstExistingPath(candidates) {
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[candidates.length - 1];
}

function resolveWorkflowRoot(platformAppDir, platformInputPath) {
  return resolveFirstExistingPath([
    path.resolve(platformAppDir, 'workflows'),
    path.resolve(platformInputPath, 'workflows'),
    path.resolve(projectRoot, 'platform/workflows'),
    path.resolve(projectRoot, 'chat-ui/src/workflows_stub'),
  ]);
}

function normalizeFaviconPath(value) {
  if (!value || typeof value !== 'string') return 'favicon.ico';
  let out = value.trim();
  if (!out) return 'favicon.ico';

  if (out.startsWith('//')) {
    out = `/${out.replace(/^\/+/, '')}`;
  }
  if (/^[a-z]+:\/\//i.test(out)) {
    return out.replace(/\/+$/, '');
  }
  // Keep favicon href relative for Vite's history-fallback transformed HTML.
  // Absolute "/favicon.ico" becomes "//favicon.ico" on /dashboard in dev fallback.
  out = out.replace(/^\/+/, '');
  out = out.replace(/\/+$/, '');
  return out || 'favicon.ico';
}

// Favicon — read from brand/theme_config.json if available (best-effort; runtime
// theme loading via /api/theme-config is the authoritative source).
export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, projectRoot, '');
  const platformEnv = process.env.PLATFORM_PATH || rootEnv.PLATFORM_PATH;
  const platformInputPath = platformEnv
    ? path.resolve(projectRoot, platformEnv)
    : path.resolve(projectRoot, 'platform');
  const platformAppDir = resolveAppBundleDir(platformInputPath);
  const platformWorkflowRoot = resolveWorkflowRoot(platformAppDir, platformInputPath);

  // Platform UI extensions:
  // - product platforms: <app>/../ui/index.js
  // - OSS bundles: <app>/extensions.js
  // - fallback: platform/extensions.js
  const platformExtensionsFile = resolveFirstExistingPath([
    path.resolve(platformAppDir, '../ui/index.js'),
    path.resolve(platformAppDir, 'ui/index.js'),
    path.resolve(platformAppDir, '../extensions.js'),
    path.resolve(platformAppDir, 'extensions.js'),
    path.resolve(projectRoot, 'platform/extensions.js'),
  ]);

  // Public (static) assets: <app>/../brand OR <app>/brand OR platform/brand
  const platformBrandDir = resolveFirstExistingPath([
    path.resolve(platformAppDir, '../brand'),
    path.resolve(platformAppDir, 'brand'),
    path.resolve(projectRoot, 'platform/brand'),
  ]);

  // App manifest — only user-facing fields (appName, targets, authRequired, admins).
  // apiUrl/wsUrl fall back to env vars or localhost for local dev.
  const appConfigPath = path.join(platformAppDir, 'app.json');
  const appConfig = fs.existsSync(appConfigPath)
    ? require(appConfigPath)
    : {};
  const apiUrl = process.env.VITE_API_URL || rootEnv.VITE_API_URL || appConfig.apiUrl || 'http://localhost:8000';
  const hostMode = process.env.VITE_MOZAIKS_HOST || rootEnv.VITE_MOZAIKS_HOST || process.env.MOZAIKS_HOST || rootEnv.MOZAIKS_HOST || 'studio';
  const resolveFavicon = () => {
    const themeConfigPath = path.join(platformBrandDir, 'theme_config.json');
    if (!fs.existsSync(themeConfigPath)) return 'favicon.ico';
    try {
      const cfg = JSON.parse(fs.readFileSync(themeConfigPath, 'utf-8'));
      const favicon = cfg?.theme?.branding?.favicon_url || cfg?.assets?.favicon;
      if (!favicon) return 'favicon.ico';
      const candidate = (favicon.startsWith('/') || /^[a-z]+:\/\//i.test(favicon))
        ? favicon
        : `/assets/${favicon}`;
      return normalizeFaviconPath(candidate);
    } catch {
      return 'favicon.ico';
    }
  };

  return {
  plugins: [
    // Pre-process .js files that contain JSX anywhere in the build graph.
    // Covers chat-ui/src (chat UI), platform module/workflow UIs (any *-platform),
    // and standard platform/workflows|modules UI subdirectories.
    {
      name: 'jsx-in-js',
      enforce: 'pre',
      async transform(code, id) {
        const isChatUiJs     = /[\\/]chat-ui[\\/]src[\\/].*\.js$/.test(id);
        const isPlatformUiJs = /[\\/]platform[\\/](workflows|modules)[\\/].*[\\/]ui[\\/].*\.js$/.test(id);
        const isProductUiJs  = /[\\/][^/\\]+-platform[\\/].*\.js$/.test(id);
        if (isChatUiJs || isPlatformUiJs || isProductUiJs) {
          return transformWithEsbuild(code, id, { loader: 'jsx', jsx: 'automatic', jsxImportSource: 'react' });
        }
      },
    },
    react({ include: /\.(jsx|js)$/ }),
    // Inject app name and favicon into index.html at build time.
    {
      name: 'html-inject-app-config',
      transformIndexHtml(html) {
        return html
          .replace(/__APP_NAME__/g,    appConfig.appName || 'Mozaiks')
          .replace(/__FAVICON_HREF__/g, resolveFavicon());
      },
    },
  ],

  // Serve static assets from the active platform's brand directory.
  publicDir: platformBrandDir,

  resolve: {
    // Resolve shared packages from chat-ui/node_modules (where all deps live).
    modules: [path.resolve(__dirname, '../chat-ui/node_modules'), 'node_modules'],
    alias: {
      // ── Core aliases (always present) ───────────────────────────────────
      '@mozaiks/chat-ui': path.resolve(__dirname, '../chat-ui/src'),
      '@chat-workflows-root': platformWorkflowRoot,
      'react-native':     'react-native-web',
      // Ensure files imported from sibling product/workflow folders resolve to
      // this frontend's dependency tree instead of walking unrelated parents.
      react: path.resolve(__dirname, 'node_modules/react'),
      'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),
      'react-router-dom': path.resolve(__dirname, 'node_modules/react-router-dom'),
      'lucide-react': path.resolve(__dirname, 'node_modules/lucide-react'),
      '@monaco-editor/react': path.resolve(__dirname, 'node_modules/@monaco-editor/react'),
      'monaco-editor': path.resolve(__dirname, 'node_modules/monaco-editor'),

      // ── Platform extension alias (PLATFORM_PATH-driven) ─────────────────
      // App.jsx imports: import { register } from '@platform/extensions'
      // Resolved to: PLATFORM_PATH/../ui/index.js  (product platforms)
      //          or: PLATFORM_PATH/../extensions.js (standard OSS app bundles)
      //
      // Never hardcodes a product name. Change PLATFORM_PATH in .env to switch.
      '@platform/extensions': platformExtensionsFile,
    },
  },

  define: {
    // Shim process.env for legacy CRA-style env reads in chat-ui/src.
    'process.env': JSON.stringify({}),
    'import.meta.env.MOZAIKS_HOST': JSON.stringify(hostMode),
  },

  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': { target: apiUrl, changeOrigin: true },
      '/ws':  { target: apiUrl.replace('http', 'ws'), ws: true },
    },
  },

  optimizeDeps: {
    esbuildOptions: {
      loader: { '.js': 'jsx' },
    },
  },
  };
});
