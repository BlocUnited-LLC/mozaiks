import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = await build({
  entryPoints: [path.join(shell, '../factory_app/workflows/AppGenerator/ui/BuildStatusPane.js')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.js': 'jsx' },
  external: ['react', 'react/jsx-runtime'], nodePaths: [path.join(shell, 'node_modules')],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const render = (integrationTestResult) => renderToStaticMarkup(createElement(module.exports.default, {
  validationStatus: 'passed', integrationPassed: true, integrationTestResult,
}));
// The integration block only; the header above it reports the separate build validation.
const integrationBlock = (html) => html.slice(html.indexOf('rounded-lg bg-black/25'));

test('a skipped integration check is shown as skipped with its reason, not as a clean pass', () => {
  const block = integrationBlock(render({
    passed: true,
    skipped_checks: [{ id: 'app_runtime_smoke', reason: 'no database configured' }],
  }));

  assert.match(block, /data-testid="integration-skipped-checks"/);
  assert.match(block, /app_runtime_smoke skipped/);
  assert.match(block, /no database configured/);
  assert.match(block, />1 skipped</);
  assert.match(block, /lucide-triangle-alert/);
  assert.doesNotMatch(block, /lucide-circle-check/);
});

test('integration checks with nothing skipped still render as passed', () => {
  const block = integrationBlock(render({ passed: true, skipped_checks: [] }));

  assert.doesNotMatch(block, /skipped/);
  assert.match(block, /lucide-circle-check/);
});
