/**
 * Approval gates must be addressable by a stable handle.
 *
 * ApprovalCard is the shared gate primitive, and its button labels are authored
 * by a model — they differ between builds and between workflows. Driving it by
 * visible text means any automation (CI smoke runs, the live traversal) is
 * pattern-matching English that changes underneath it. A live run stalled for
 * four minutes in front of an enabled "Approve" button for exactly this reason.
 *
 * The handle is derived from the action id, which is contract, not copy.
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
const ui = path.join(path.dirname(shell), 'chat-ui/src');

const compiled = await build({
  entryPoints: [path.join(ui, 'core/ui/ApprovalCard.js')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  loader: { '.js': 'jsx', '.css': 'empty', '.png': 'dataurl' },
  external: ['react', 'react/jsx-runtime'],
  nodePaths: [path.join(shell, 'node_modules')],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(
  module, module.exports, createRequire(import.meta.url),
);
const render = (payload) => renderToStaticMarkup(
  createElement(module.exports.default, { payload, onResponse: () => {}, onCancel: () => {} }),
);

test('default approval actions expose handles derived from their ids', () => {
  const html = render({ title: 'Plan' });
  assert.match(html, /data-testid="approval-action-approve"/);
  assert.match(html, /data-testid="approval-action-request_changes"/);
  assert.match(html, /data-testid="approval-action-cancel"/);
});

test('a workflow-supplied action gets a handle too', () => {
  // Workflows may override the action set; the handle must follow the id, not
  // a hardcoded list, or custom gates become undriveable again.
  const html = render({ actions: [{ id: 'promote_bundle', label: 'Ship It', variant: 'primary' }] });
  assert.match(html, /data-testid="approval-action-promote_bundle"/);
});

test('the handle does not depend on the label', () => {
  // Same id, model-authored label. The handle must not move.
  const a = render({ actions: [{ id: 'approve', label: 'Approve' }] });
  const b = render({ actions: [{ id: 'approve', label: 'Yes, build this for me' }] });
  for (const html of [a, b]) assert.match(html, /data-testid="approval-action-approve"/);
  assert.ok(a !== b, 'labels should still differ; only the handle is stable');
});
