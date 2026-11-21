// Runs inside Studio's E2B image. The compiler reads a virtual app file map and
// trusted React dependencies. No customer build scripts or plugins execute.
const fs = require('node:fs');
const path = require('node:path').posix;
const { createRequire } = require('node:module');
const nativePath = require('node:path');
const shellRoot = process.env.MOZAIKS_WEB_SHELL_PATH || '/opt/mozaiks/web_shell';
const uiRoot = process.env.MOZAIKS_CHAT_UI_PATH || '/opt/mozaiks/chat-ui';
const studioRequire = createRequire(nativePath.join(shellRoot, 'package.json'));
const esbuild = studioRequire('esbuild');
const packages = new Set(['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/client', 'lucide-react']);
const images = new Set(['.png', '.jpeg', '.jpg', '.webp', '.gif', '.svg', '.woff2']);
const extensions = ['', '.jsx', '.tsx', '.js', '.ts', '.json', '.css', '/index.jsx', '/index.tsx', '/index.js'];
const previewEntry = `
  import React from 'react';
  import {createRoot} from 'react-dom/client';
  import Page from 'reviewed-page';
  import {applyTheme} from 'studio-theme';
  import brand from 'reviewed-brand';
  applyTheme({...brand, _appThemeConfig: brand.theme || {}});
  class PreviewBoundary extends React.Component {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() {
      return this.state.failed
        ? React.createElement('p', { role: 'alert' }, 'This page could not render in preview.')
        : React.createElement(Page);
    }
  }
  createRoot(document.getElementById('app')).render(React.createElement(PreviewBoundary));
`;

async function shellStyles(files, basename) {
  const postcss = studioRequire('postcss');
  const tailwind = studioRequire('@tailwindcss/postcss');
  const stylesheet = nativePath.join(shellRoot, 'styles.css');
  const scanFile = nativePath.resolve(basename + '-source.jsx');
  fs.writeFileSync(scanFile, Object.entries(files)
    .filter(([name]) => /\.[jt]sx?$/.test(name)).map(([, source]) => source).join('\n'));
  const trustedCss = fs.readFileSync(stylesheet, 'utf8').replace(/^@source .*;\s*$/gm, '');
  const source = dir => '@source ' + JSON.stringify(dir.replaceAll('\\', '/')) + ';';
  // Only Studio's pinned CSS is a PostCSS input. App JS is scanned for class
  // names, and raw app CSS is handled by esbuild's virtual file resolver.
  const built = await postcss([tailwind({base: shellRoot, optimize: true})]).process(
    [trustedCss, source(scanFile), source(nativePath.join(uiRoot, 'src/ui'))].join('\n'),
    { from: stylesheet },
  );
  return built.css;
}

async function compile({ files, page, brand = {} }, basename) {
  const plugin = {
    name: 'document-page',
    setup(build) {
      build.onResolve({ filter: /.*/, namespace: 'document' }, args => {
        if (args.path === '@mozaiks/chat-ui/ui') return { path: nativePath.join(uiRoot, 'src/ui/index.js') };
        if (packages.has(args.path)) return { path: studioRequire.resolve(args.path) };
        const resolved = args.kind === 'entry-point' ? page
          : path.join(path.dirname(args.importer), args.path);
        if (!args.path.startsWith('.') && !['entry-point', 'url-token', 'import-rule'].includes(args.kind)) throw Error('Unsupported page import');
        const found = extensions.map(ext => resolved + ext).find(file => Object.hasOwn(files, file));
        if (!found) throw Error('Unsupported page import');
        return { path: found, namespace: 'document' };
      });
      build.onResolve({ filter: /^reviewed-page$/ }, () => ({ path: page, namespace: 'document' }));
      build.onResolve({ filter: /^studio-theme$/ }, () => ({ path: nativePath.join(uiRoot, 'src/styles/themeProvider.js') }));
      build.onResolve({ filter: /^reviewed-brand$/ }, () => ({ path: 'brand', namespace: 'brand-config' }));
      build.onLoad({ filter: /.*/, namespace: 'brand-config' }, () => ({ contents: JSON.stringify(brand), loader: 'json' }));
      build.onResolve({ filter: /^react(?:-dom)?(?:\/.*)?$/ }, args =>
        packages.has(args.path) ? { path: studioRequire.resolve(args.path) } : undefined);
      build.onLoad({ filter: /.*/, namespace: 'document' }, args => {
        const ext = path.extname(args.path);
        if (images.has(ext)) return { contents: Buffer.from(files[args.path], 'base64'), loader: 'dataurl' };
        return { contents: files[args.path], loader: ({ '.js': 'jsx', '.mjs': 'jsx', '.jsx': 'jsx', '.ts': 'ts', '.tsx': 'tsx', '.json': 'json', '.css': 'css' })[ext] };
      });
    },
  };
  const bundle = await esbuild.build({
    stdin: { contents: previewEntry, resolveDir: shellRoot },
    jsx: 'automatic', bundle: true, minify: true, write: false, outfile: 'page.js',
    platform: 'browser', format: 'iife', logLevel: 'silent',
    loader: { '.js': 'jsx', '.png': 'dataurl', '.svg': 'dataurl', '.woff2': 'dataurl' },
    nodePaths: [nativePath.join(shellRoot, 'node_modules')],
    define: { 'process.env.NODE_ENV': '"production"' }, plugins: [plugin],
  });
  const js = bundle.outputFiles.find(file => file.path.endsWith('.js')).text;
  const css = await shellStyles(files, basename) + '\n' + (bundle.outputFiles.find(file => file.path.endsWith('.css'))?.text || '');
  // Base64 transports arbitrary script/style text without HTML raw-text breakout.
  const encoded = text => Buffer.from(text).toString('base64');
  const csp = "default-src 'none'; script-src data:; style-src data: 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; worker-src 'none'";
  return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + csp + '"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer"><link rel="stylesheet" href="data:text/css;base64,' + encoded(css) + '"><style>body{margin:0}</style></head><body><div id="app"></div><script src="data:text/javascript;base64,' + encoded(js) + '"></script></body></html>';
}

async function main() {
  const [basename] = process.argv.slice(2);
  if (!/^mozaiks-preview-[a-f0-9]{32}$/.test(basename)) throw Error('Invalid compiler job');
  const input = JSON.parse(fs.readFileSync(basename + '.json', 'utf8'));
  const doc = await compile(input, basename);
  if (Buffer.byteLength(doc) > 2_000_000) throw Error('Document too large');
  fs.writeFileSync(basename + '.html', doc, { flag: 'wx' });
}
main().catch(() => { process.exitCode = 1; });
