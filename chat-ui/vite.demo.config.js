import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const studioBrandRoot = path.join(repoRoot, 'factory_app', 'app', 'brand');
const studioAssetsRoot = path.join(studioBrandRoot, 'assets');
const studioConfigRoot = path.join(repoRoot, 'factory_app', 'app', 'config');

function resolveMimeType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  switch (ext) {
    case '.json':
      return 'application/json; charset=utf-8';
    case '.svg':
      return 'image/svg+xml';
    case '.png':
      return 'image/png';
    case '.jpg':
    case '.jpeg':
      return 'image/jpeg';
    case '.webp':
      return 'image/webp';
    case '.gif':
      return 'image/gif';
    case '.ico':
      return 'image/x-icon';
    case '.woff':
      return 'font/woff';
    case '.woff2':
      return 'font/woff2';
    case '.ttf':
      return 'font/ttf';
    case '.otf':
      return 'font/otf';
    default:
      return 'application/octet-stream';
  }
}

function sendFile(res, filePath) {
  if (!fs.existsSync(filePath)) {
    res.statusCode = 404;
    res.end('Not found');
    return;
  }
  res.setHeader('Content-Type', resolveMimeType(filePath));
  res.end(fs.readFileSync(filePath));
}

function demoShellPlugin() {
  return {
    name: 'mozaiks-demo-shell',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const requestUrl = req.url || '';
        const pathname = requestUrl.split('?')[0];

        if (pathname === '/api/theme-config') {
          sendFile(res, path.join(studioBrandRoot, 'theme_config.json'));
          return;
        }

        if (pathname === '/api/shell-config') {
          sendFile(res, path.join(studioConfigRoot, 'shell.json'));
          return;
        }

        if (pathname.startsWith('/assets/')) {
          const assetName = pathname.slice('/assets/'.length);
          sendFile(res, path.join(studioAssetsRoot, assetName));
          return;
        }

        if (pathname.startsWith('/fonts/')) {
          const fontName = pathname.slice('/fonts/'.length);
          sendFile(res, path.join(studioBrandRoot, 'fonts', fontName));
          return;
        }

        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), demoShellPlugin()],
  esbuild: {
    loader: 'jsx',
    include: /src\/.*\.[jt]sx?$/,
    exclude: [],
  },
  resolve: {
    alias: {
      '@chat-workflows-root': fileURLToPath(new URL('./src/workflows_stub', import.meta.url)),
    },
  },
  optimizeDeps: {
    esbuildOptions: {
      loader: {
        '.js': 'jsx',
      },
    },
  },
  define: {
    'process.env.NODE_ENV': JSON.stringify('development'),
  },
});