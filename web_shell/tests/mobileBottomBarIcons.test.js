import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const component = path.join(root, 'chat-ui/src/components/layout/ShellNavigationIcon.jsx');

const bundle = await build({
  entryPoints: [component],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  packages: 'external',
  write: false,
  nodePaths: [path.join(root, 'web_shell/node_modules')],
});
const module = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports);
const ShellNavigationIcon = module.exports.default;

const markup = (icon, fallback = '?') => renderToStaticMarkup(
  React.createElement(ShellNavigationIcon, { icon, fallback }),
);

test('mobile navigation renders known manifest hints as icons', () => {
  for (const icon of ['store', 'analytics', 'apps']) {
    const html = markup(icon, 'X');
    assert.match(html, /<svg\b/);
    assert.doesNotMatch(html, />X</);
  }
});

test('mobile navigation renders asset icons and keeps an initial fallback', () => {
  assert.match(markup('custom.svg'), /mask:url\(\/assets\/custom\.svg\)/);
  assert.equal(markup('unknown-hint', 'R'), 'R');
  assert.equal(markup('constructor', 'C'), 'C');
  assert.equal(markup(null, 'D'), 'D');
});
