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
// - a workspace root that contains ./app/app.json
// MOZAIKS_APP_WORKSPACE_PATH is a convenience alias for an external app
// workspace/repo root when PLATFORM_PATH is not set.
//
// When unset, it defaults to the local App Zero app bundle at ./mozaiks-platform/app.
const projectRoot = path.resolve(__dirname, '..');

function resolveAppBundleDir(platformInputPath) {
  const directAppJson = path.join(platformInputPath, 'app.json');
  if (fs.existsSync(directAppJson)) return platformInputPath;

  const nestedAppDir = path.join(platformInputPath, 'app');
  const nestedAppJson = path.join(nestedAppDir, 'app.json');
  if (fs.existsSync(nestedAppJson)) return nestedAppDir;

  return platformInputPath;
}

function resolveBrandDir(platformAppDir, factoryBrandDir) {
  const candidates = [
    path.resolve(platformAppDir, 'brand'),
    factoryBrandDir,
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'theme_config.json'))) {
      return candidate;
    }
  }

  return candidates[0];
}

function resolveFirstExistingPath(candidates) {
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[candidates.length - 1];
}

function hasWorkflowDefinitions(candidate) {
  try {
    if (!fs.existsSync(candidate) || !fs.statSync(candidate).isDirectory()) return false;
    return fs.readdirSync(candidate, { withFileTypes: true }).some((entry) => {
      if (!entry.isDirectory()) return false;
      if (entry.name === 'extended_orchestration') return false;
      return fs.existsSync(path.join(candidate, entry.name, 'orchestrator.yaml'));
    });
  } catch {
    return false;
  }
}

function resolveWorkflowRoot(platformAppDir, platformInputPath, workflowRootsEnv, workflowsEnvPath, factoryWorkflowsRoot, chatUiSrcRoot) {
  const stubRoot = path.resolve(chatUiSrcRoot, 'workflows_stub');
  const resolveCandidate = (candidate) => {
    if (!candidate) return '';
    return path.isAbsolute(candidate)
      ? candidate
      : path.resolve(projectRoot, candidate);
  };

  if (workflowsEnvPath) {
    return resolveCandidate(workflowsEnvPath);
  }

  if (workflowRootsEnv) {
    const firstLegacyRoot = workflowRootsEnv
      .split(path.delimiter)
      .map((value) => value.trim())
      .filter(Boolean)[0];
    if (firstLegacyRoot) {
      return resolveCandidate(firstLegacyRoot);
    }
  }

  const appWorkflowRoot = path.resolve(platformAppDir, 'workflows');
  if (hasWorkflowDefinitions(appWorkflowRoot)) {
    return appWorkflowRoot;
  }

  const workspaceWorkflowRoot = path.resolve(platformInputPath, 'workflows');
  if (workspaceWorkflowRoot !== appWorkflowRoot && hasWorkflowDefinitions(workspaceWorkflowRoot)) {
    return workspaceWorkflowRoot;
  }

  if (hasWorkflowDefinitions(factoryWorkflowsRoot)) {
    return factoryWorkflowsRoot;
  }

  return resolveFirstExistingPath([
    appWorkflowRoot,
    workspaceWorkflowRoot,
    factoryWorkflowsRoot,
    stubRoot,
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
  const factoryAppRoot = resolveFirstExistingPath([
    process.env.MOZAIKS_FACTORY_APP_PATH || rootEnv.MOZAIKS_FACTORY_APP_PATH || '',
    path.resolve(projectRoot, 'factory_app'),
  ]);
  const factoryBrandDir = resolveFirstExistingPath([
    path.resolve(factoryAppRoot, 'app/brand'),
    path.resolve(projectRoot, 'factory_app/app/brand'),
  ]);
  const factoryWorkflowsRoot = resolveFirstExistingPath([
    path.resolve(factoryAppRoot, 'workflows'),
    path.resolve(projectRoot, 'factory_app/workflows'),
  ]);
  const chatUiRoot = resolveFirstExistingPath([
    process.env.MOZAIKS_CHAT_UI_PATH || rootEnv.MOZAIKS_CHAT_UI_PATH || '',
    path.resolve(projectRoot, 'chat-ui'),
  ]);
  const chatUiSrcRoot = resolveFirstExistingPath([
    path.resolve(chatUiRoot, 'src'),
    path.resolve(projectRoot, 'chat-ui/src'),
  ]);
  const chatUiNodeModules = resolveFirstExistingPath([
    path.resolve(chatUiRoot, 'node_modules'),
    path.resolve(__dirname, 'node_modules'),
  ]);
  const platformEnv = process.env.PLATFORM_PATH || rootEnv.PLATFORM_PATH;
  const appWorkspaceEnv =
    process.env.MOZAIKS_APP_WORKSPACE_PATH ||
    rootEnv.MOZAIKS_APP_WORKSPACE_PATH ||
    '';
  const platformInputPath = platformEnv
    ? path.resolve(projectRoot, platformEnv)
    : appWorkspaceEnv
      ? path.resolve(projectRoot, appWorkspaceEnv)
      : path.resolve(factoryAppRoot, 'app');
  const platformAppDir = resolveAppBundleDir(platformInputPath);
  const workflowsEnv =
    process.env.MOZAIKS_WORKFLOWS_PATH ||
    rootEnv.MOZAIKS_WORKFLOWS_PATH ||
    process.env.VITE_MOZAIKS_WORKFLOWS_PATH ||
    rootEnv.VITE_MOZAIKS_WORKFLOWS_PATH ||
    '';
  const workflowRootsEnv =
    process.env.MOZAIKS_WORKFLOW_ROOTS ||
    rootEnv.MOZAIKS_WORKFLOW_ROOTS ||
    process.env.VITE_MOZAIKS_WORKFLOW_ROOTS ||
    rootEnv.VITE_MOZAIKS_WORKFLOW_ROOTS ||
    '';
  const platformWorkflowRoot = resolveWorkflowRoot(
    platformAppDir,
    platformInputPath,
    workflowRootsEnv,
    workflowsEnv,
    factoryWorkflowsRoot,
    chatUiSrcRoot,
  );

  // Platform UI extensions come from the active app bundle: <app>/ui/index.js
  const platformExtensionsFile = path.resolve(platformAppDir, 'ui/index.js');

  // Public (static) assets come from the active app bundle: <app>/brand
  const platformBrandDir = resolveBrandDir(platformAppDir, factoryBrandDir);

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
    // Covers chat-ui/src, shared factory workflow UIs, active app workflow/module
    // UIs, and product/workspace overlays that still ship JSX in .js files.
    {
      name: 'jsx-in-js',
      enforce: 'pre',
      async transform(code, id) {
      const isChatUiJs = /(?:[\\/]chat-ui[\\/]src[\\/]|[\\/]mozaiks_chat_ui[\\/]src[\\/]).*\.js$/.test(id);
        const isWorkflowOrModuleUiJs =
          /[\\/]factory_app[\\/]workflows[\\/].*[\\/]ui[\\/].*\.js$/.test(id) ||
          /[\\/]factory_app[\\/]app[\\/]workflows[\\/].*[\\/]ui[\\/].*\.js$/.test(id) ||
          /[\\/]app[\\/](?:workflows|modules)[\\/].*[\\/]ui[\\/].*\.js$/.test(id);
        const isProductUiJs = /[\\/][^/\\]+-platform[\\/].*\.js$/.test(id);
        if (isChatUiJs || isWorkflowOrModuleUiJs || isProductUiJs) {
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
    modules: [chatUiNodeModules, path.resolve(__dirname, 'node_modules'), 'node_modules'],
    alias: {
      // ── Core aliases (always present) ───────────────────────────────────
      '@mozaiks/chat-ui': chatUiSrcRoot,
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
      // Resolved to the active app root UI barrel, which owns any declared
      // custom routes and management surfaces for that app.
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
