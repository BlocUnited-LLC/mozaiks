/**
 * WorkflowCompletion run-summary contract.
 *
 * The live traversal showed a "Run Summary" heading with nothing under it.
 * Cause: ChatPage built `summary` as an object literal whose fields were both
 * null. An object of nulls is truthy, so the panel rendered its heading and
 * then every field guard failed. These tests pin both halves of the fix — the
 * panel only appears when something will actually be inside it.
 */
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
  entryPoints: [path.join(shell, '../chat-ui/src/ui/screens/WorkflowCompletion.jsx')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], nodePaths: [path.join(shell, 'node_modules')],
  plugins: [{ name: 'isolated-completion', setup(builder) {
    builder.onResolve({ filter: /@mozaiks\/chat-ui\/platform/ }, args => ({ path: args.path, namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({
      loader: 'jsx', resolveDir: shell,
      contents: `export const TransitionActionPanel = ({children}) => <section>{children}</section>;
                 export const TransitionActionButton = ({label}) => <button>{label}</button>;
                 export const useTransitionMotion = () => ({ entered: true, prefersReducedMotion: false });`,
    }));
  } }],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const render = props => renderToStaticMarkup(createElement(module.exports.default, props));

test('a summary whose every field is empty renders no Run Summary panel', () => {
  // The exact shape ChatPage used to emit when the run reported no metrics.
  const html = render({ transition: { context: { workflowName: 'ThemeCapture', summary: { duration: null, tokensUsed: null } } } });
  assert.doesNotMatch(html, /Run Summary/, 'empty summary must not render a labelled panel');
});

test('an empty object summary renders no Run Summary panel', () => {
  assert.doesNotMatch(render({ summary: {} }), /Run Summary/);
});

test('a whitespace-only string summary renders no Run Summary panel', () => {
  assert.doesNotMatch(render({ summary: '   ' }), /Run Summary/);
});

test('a summary with one real field renders that field', () => {
  const html = render({ transition: { context: { summary: { duration: '42s', tokensUsed: null } } } });
  assert.match(html, /Run Summary/);
  assert.match(html, /Duration/);
  assert.match(html, /42s/);
  assert.doesNotMatch(html, /Tokens Used/, 'null fields must not render an empty row');
});

test('zero files generated is a real value, not an absent one', () => {
  const html = render({ summary: { filesGenerated: 0 } });
  assert.match(html, /Files Generated/);
  assert.match(html, /0/);
});

test('a string summary still renders as prose', () => {
  assert.match(render({ summary: 'Generated 12 modules.' }), /Generated 12 modules\./);
});
