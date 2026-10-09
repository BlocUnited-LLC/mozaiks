import { defineConfig, loadEnv, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import fs from 'fs';
import { createHash } from 'node:crypto';
import { workflowUiPlugin } from './workflowUi.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require   = createRequire(import.meta.url);

// ── Platform resolution ────────────────────────────────────────────────────
// PLATFORM_PATH (from root .env) may point to either:
// - an app bundle directory that contains app.json (e.g. factory_app/app)
// - a workspace root that contains ./app/app.json
// MOZAIKS_APP_WORKSPACE_PATH selects an external app workspace/repo root when
// PLATFORM_PATH is not set.
//
// When unset, it defaults to the first-party builder/reference app bundle at ./factory_app/app.
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

function resolveWorkflowRoot(platformAppDir, workflowsEnvPath, factoryWorkflowsRoot, chatUiSrcRoot) {
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

  const workspaceRoot = path.basename(platformAppDir) === 'app' ? path.dirname(platformAppDir) : platformAppDir;
  const workspaceWorkflowRoot = path.resolve(workspaceRoot, 'workflows');
  if (fs.existsSync(workspaceWorkflowRoot) && fs.statSync(workspaceWorkflowRoot).isDirectory()) {
    return workspaceWorkflowRoot;
  }
  return resolveFirstExistingPath([
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

function tailwindSourceLinkType(target) {
  const stats = fs.statSync(target);
  if (!stats.isDirectory()) return 'file';
  return process.platform === 'win32' ? 'junction' : 'dir';
}

function ensureTailwindSourceLinks(entries) {
  const linkRoot = path.resolve(__dirname, '.mozaiks-tailwind-sources');
  const required = process.env.MOZAIKS_REQUIRE_TAILWIND_SOURCE_LINKS === '1';

  try {
    fs.mkdirSync(linkRoot, { recursive: true });
    for (const name of fs.readdirSync(linkRoot)) {
      fs.rmSync(path.join(linkRoot, name), { recursive: true, force: true });
    }
  } catch (error) {
    if (required) throw error;
    console.warn(`[mozaiks-web-shell] Failed to prepare Tailwind source links: ${error.message}`);
    return linkRoot;
  }

  for (const [name, target] of entries) {
    if (!target || !fs.existsSync(target)) continue;

    try {
      const linkPath = path.join(linkRoot, name);
      fs.symlinkSync(fs.realpathSync(target), linkPath, tailwindSourceLinkType(target));
    } catch (error) {
      if (required) throw error;
      console.warn(`[mozaiks-web-shell] Failed to link Tailwind source '${name}': ${error.message}`);
    }
  }

  return linkRoot;
}

function isLoopbackAddress(address) {
  return address === '::1' || address.startsWith('127.') || address.startsWith('::ffff:127.');
}

// The address of a client that is not on this machine, or null for a local one.
function remoteClientAddress(req) {
  const address = req.socket?.remoteAddress ?? '';
  return isLoopbackAddress(address) ? null : address || 'unknown';
}

// Dev-proxy `configure` hook: give the backend the address of a client that is
// not on this machine (see the proxy entries below).
function markRemoteClients(proxy) {
  // HTTP: 'start' is emitted for every proxied request before the outgoing
  // request is built from req.headers. Not 'proxyReq': http-proxy-3 skips that
  // event for a request that carries an Expect header, and never emits it on
  // its fetch path (FORCE_FETCH_PATH=true), so such a request would arrive
  // unmarked and look local.
  proxy.on('start', (req) => {
    const address = remoteClientAddress(req);
    if (address) req.headers['x-forwarded-for'] = address;
  });
  // WebSocket upgrade: 'proxyReqWs' is emitted for every upgrade before the
  // request is sent. Never forward another machine's request unmarked.
  proxy.on('proxyReqWs', (proxyReq, req) => {
    const address = remoteClientAddress(req);
    if (!address) return;
    if (proxyReq.headersSent) return proxyReq.destroy();
    proxyReq.setHeader('X-Forwarded-For', address);
  });
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
  const platformWorkflowRoot = resolveWorkflowRoot(
    platformAppDir,
    workflowsEnv,
    factoryWorkflowsRoot,
    chatUiSrcRoot,
  );

  // Schema-only apps do not need a custom registration barrel.
  const platformExtensionsFile = path.resolve(platformAppDir, 'ui/index.js');
  const platformAppDirForward = platformAppDir.replace(/\\/g, '/');

  // Public (static) assets come from the active app bundle: <app>/brand
  const platformBrandDir = resolveBrandDir(platformAppDir, factoryBrandDir);
  const tailwindSourceLinkRoot = ensureTailwindSourceLinks([
    ['chat-ui-src', chatUiSrcRoot],
    ['factory-app-ui', path.resolve(factoryAppRoot, 'app/ui')],
    ['factory-workflows', factoryWorkflowsRoot],
    ['platform-ui', path.resolve(platformAppDir, 'ui')],
    ['platform-workflows', platformWorkflowRoot],
  ]);
  const viteFsAllow = Array.from(new Set([
    __dirname,
    projectRoot,
    platformInputPath,
    platformAppDir,
    platformBrandDir,
    platformWorkflowRoot,
    factoryAppRoot,
    factoryBrandDir,
    factoryWorkflowsRoot,
    chatUiRoot,
    chatUiSrcRoot,
    chatUiNodeModules,
    tailwindSourceLinkRoot,
    path.resolve(__dirname, 'node_modules'),
  ]));

  // App manifest — only user-facing fields (appName, targets, authRequired, admins).
  // apiUrl/wsUrl fall back to env vars or localhost for local dev.
  const appConfigPath = path.join(platformAppDir, 'app.json');
  const appConfig = fs.existsSync(appConfigPath)
    ? require(appConfigPath)
    : {};
  const apiUrl = process.env.MOZAIKS_BACKEND_URL || rootEnv.MOZAIKS_BACKEND_URL || process.env.VITE_API_URL || rootEnv.VITE_API_URL || appConfig.apiUrl || 'http://localhost:8000';
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
  cacheDir: path.join(__dirname, 'node_modules', '.vite-apps', createHash('sha256').update(platformAppDir).digest('hex').slice(0, 16)),
  plugins: [
    {
      name: 'monaco-patched-sanitizer',
      enforce: 'pre',
      resolveId(source, importer) {
        const sanitizer = path.resolve(__dirname, 'node_modules/monaco-editor/esm/vs/base/browser/domSanitize.js').replace(/\\/g, '/');
        if (source === './dompurify/dompurify.js' && importer?.split('?', 1)[0].replace(/\\/g, '/') === sanitizer) {
          return path.resolve(chatUiSrcRoot, 'utils/monacoDomPurify.js');
        }
      },
    },
    {
      name: 'mozaiks-app-extensions',
      resolveId(id) {
        if (id === '@platform/extensions') return '\0mozaiks-app-extensions';
      },
      load(id) {
        if (id !== '\0mozaiks-app-extensions') return undefined;
        if (!fs.existsSync(platformExtensionsFile)) return 'export {};';
        this.addWatchFile(platformExtensionsFile);
        return `export * from ${JSON.stringify(platformExtensionsFile.replaceAll('\\', '/'))};`;
      },
    },
    workflowUiPlugin({ primaryRoot: platformWorkflowRoot, factoryRoot: factoryWorkflowsRoot }),
    // Pre-process .js files that contain JSX anywhere in the build graph.
    // Covers chat-ui/src, shared factory workflow UIs, active app workflow/module
    // UIs, and product/workspace overlays that still ship JSX in .js files.
    {
      name: 'jsx-in-js',
      enforce: 'pre',
      async transform(code, id) {
          // Asset requests belong to Vite's loaders, even when the asset is .js.
          const request = id.split('#', 1)[0];
          const queryIndex = request.indexOf('?');
          const sourceId = (queryIndex < 0 ? request : request.slice(0, queryIndex)).replace(/\\/g, '/');
          const query = new URLSearchParams(queryIndex < 0 ? '' : request.slice(queryIndex + 1));
          if (id.startsWith('\0') || !sourceId.endsWith('.js') || query.has('raw') || query.has('url')) return;
          const isChatUiJs = /(?:\/chat-ui\/src\/|\/mozaiks_chat_ui\/src\/).*\.js$/.test(sourceId);
        const isWorkflowOrModuleUiJs =
            [platformWorkflowRoot, factoryWorkflowsRoot].some((root) => sourceId.startsWith(root.replace(/\\/g, '/') + '/')) ||
            /\/factory_app\/workflows\/.*\/ui\/.*\.js$/.test(sourceId) ||
            /\/factory_app\/app\/workflows\/.*\/ui\/.*\.js$/.test(sourceId) ||
            /\/app\/(?:workflows|modules)\/.*\/ui\/.*\.js$/.test(sourceId);
        const isProductUiJs =
            /\/[^/]+-platform\/.*\.js$/.test(sourceId) ||
            sourceId.startsWith(platformAppDirForward + '/');
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
    // Deduplicate singleton packages that break hooks if loaded twice.
    // chat-ui ships its own node_modules/react; dedupe forces the resolver
    // to always use web_shell's copy, which matches what react-dom uses.
    dedupe: ['react', 'react-dom', 'react-router-dom', 'react-router'],
    alias: {
      // ── Core aliases (always present) ───────────────────────────────────
      '@mozaiks/chat-ui': chatUiSrcRoot,
      '@mozaiks/factory-app-ui': path.resolve(factoryAppRoot, 'app/ui/index.js'),
      '@mozaiks/factory-admin': path.resolve(factoryAppRoot, 'app/admin/index.js'),
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
      'clsx': path.resolve(__dirname, 'node_modules/clsx'),
      'tailwind-merge': path.resolve(__dirname, 'node_modules/tailwind-merge'),
      'class-variance-authority': path.resolve(__dirname, 'node_modules/class-variance-authority'),
      '@radix-ui/react-dialog': path.resolve(__dirname, 'node_modules/@radix-ui/react-dialog'),
      '@radix-ui/react-progress': path.resolve(__dirname, 'node_modules/@radix-ui/react-progress'),
      '@radix-ui/react-select': path.resolve(__dirname, 'node_modules/@radix-ui/react-select'),
      '@radix-ui/react-slot': path.resolve(__dirname, 'node_modules/@radix-ui/react-slot'),
      '@radix-ui/react-tabs': path.resolve(__dirname, 'node_modules/@radix-ui/react-tabs'),
      '@radix-ui/react-tooltip': path.resolve(__dirname, 'node_modules/@radix-ui/react-tooltip'),
      'marked': path.resolve(__dirname, 'node_modules/marked'),
      'dompurify': path.resolve(__dirname, 'node_modules/dompurify'),
      'react-icons': path.resolve(__dirname, 'node_modules/react-icons'),
      '@xyflow/react': path.resolve(__dirname, 'node_modules/@xyflow/react'),
      '@dagrejs/dagre': path.resolve(__dirname, 'node_modules/@dagrejs/dagre'),

      // ── Platform extension alias (PLATFORM_PATH-driven) ─────────────────
      // App.jsx imports: import { register } from '@platform/extensions'
      // Resolved to the active app root UI barrel, which owns any declared
      // custom routes and management surfaces for that app.
    },
  },

  define: {
    // Provide process.env for chat-ui modules that read Node-style env fields.
    'process.env': JSON.stringify({}),
    'import.meta.env.MOZAIKS_HOST': JSON.stringify(hostMode),
  },

  server: {
    port: 3000,
    strictPort: true,
    fs: {
      allow: viteFsAllow,
    },
    // With authentication off the backend grants development access
    // (AUTH_ANON_ACCESS=local) only to requests from this machine, and treats
    // ANY forwarding header as "not this machine". Every proxied request reaches
    // it from this dev server's loopback connection, so markRemoteClients marks
    // the clients that are elsewhere: another machine's request carries its
    // address in X-Forwarded-For (replacing whatever it sent), while a browser
    // on this machine passes through with no header added. (xfwd would add the
    // header for every client and so refuse local browsers too.)
    proxy: {
      '/api': { target: apiUrl, changeOrigin: true, configure: markRemoteClients },
      '/ws':  { target: apiUrl.replace('http', 'ws'), ws: true, configure: markRemoteClients },
    },
  },

  build: {
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;

          if (
            id.includes('/node_modules/react/') ||
            id.includes('/node_modules/react-dom/') ||
            id.includes('/node_modules/react-router/') ||
            id.includes('/node_modules/react-router-dom/') ||
            id.includes('/node_modules/@remix-run/router/')
          ) {
            return 'vendor-react';
          }

          if (
            id.includes('/node_modules/react-icons/') ||
            id.includes('/node_modules/lucide-react/')
          ) {
            return 'vendor-icons';
          }

          if (
            id.includes('/node_modules/marked/') ||
            id.includes('/node_modules/dompurify/')
          ) {
            return 'vendor-content';
          }

          if (id.includes('/node_modules/keycloak-js/')) {
            return 'vendor-auth';
          }

          // Monaco follows CodeEditorPane's lazy boundary. A forced vendor chunk
          // pulls its sanitizer into the eager shared dependency graph.
          return undefined;
        },
      },
    },
  },

  optimizeDeps: {
    // Keep Monaco's copied sanitizer import visible to the exact resolver above
    // in dev as well as production, instead of prebundling its vendored copy.
    exclude: ['monaco-editor'],
    rolldownOptions: {
      // Vite 8 uses Rolldown for dependency scanning before normal plugins run.
      // Several first-party UI files intentionally use JSX in .js modules, so
      // the scanner must parse .js the same way the jsx-in-js transform does.
      moduleTypes: {
        '.js': 'jsx',
      },
      plugins: [],
    },
  },
  };
});
