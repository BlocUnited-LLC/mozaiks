import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';
import fs from 'node:fs/promises';
import http from 'node:http';
import { chromium, expect } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = await build({
  entryPoints: [path.join(shell, '../factory_app/workflows/AppReview/ui/AppReview/AppReviewSummary.jsx')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], nodePaths: [path.join(shell, 'node_modules')],
  plugins: [{ name: 'isolated-review', setup(builder) {
    builder.onResolve({ filter: /@mozaiks\/chat-ui\/ui|studioApi\.js$/ }, args => ({ path: args.path, namespace: 'mock' }));
    builder.onLoad({ filter: /.*/, namespace: 'mock' }, args => ({
      loader: 'jsx', resolveDir: shell,
      contents: args.path.endsWith('studioApi.js')
        ? 'export const studioFetch = () => { throw Error("No live API allowed"); };'
        : `export const Panel = ({children}) => <section>{children}</section>;
           export const StatusPill = ({children}) => <span>{children}</span>;
           export const Button = ({children, disabled}) => <button disabled={disabled}>{children}</button>;
           export const Metric = ({label, value}) => <p>{label}: {value}</p>;`,
    }));
  } }],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const render = payload => renderToStaticMarkup(createElement(module.exports.default, { payload }));

test('missing review evidence is visible as Missing, never Skipped', () => {
  const html = render({ can_promote: false });
  for (const label of ['Bundle acceptance', 'Build validation', 'Integration checks', 'Security readiness']) {
    assert.match(html, new RegExp(`${label}</span><span>Missing</span>`));
  }
  assert.doesNotMatch(html, /Skipped/);
  assert.match(html, /<button disabled=""/);
});

test('explicit skipped runtime validation remains distinct from passed checks', () => {
  const html = render({
    app_validation_status: 'skipped', app_validation_strategy_used: 'skip',
    app_bundle_acceptance_status: 'passed', integration_tests_passed: true,
    security_readiness_summary: { status: 'passed', finding_count: 0, persisted: false, success: true },
    can_promote: true, artifact_version_id: 'artifact', build_registry_id: 'owned-build',
  });
  assert.match(html, /Build validation<\/span><span>Skipped<\/span>/);
  for (const label of ['Bundle acceptance', 'Integration checks', 'Security readiness']) {
    assert.match(html, new RegExp(`${label}</span><span>Passed</span>`));
  }
  assert.doesNotMatch(html, /Missing|disabled=""/);
});

test('failed checks stay Failed and promotion remains disabled', () => {
  const html = render({
    app_validation_status: 'failed', app_bundle_acceptance_status: 'failed', integration_tests_passed: false,
    can_promote: false, artifact_version_id: 'artifact', build_registry_id: 'owned-build',
  });
  for (const label of ['Bundle acceptance', 'Build validation', 'Integration checks']) {
    assert.match(html, new RegExp(`${label}</span><span>Failed</span>`));
  }
  assert.match(html, /<button disabled=""/);
});

async function discoveryFixture(t, payload) {
  const root = path.dirname(shell);
  const component = path.join(root, 'factory_app/workflows/ExistingAppDiscovery/ui/AppIntelligenceOverviewCard.jsx');
  const bundle = await build({
    stdin: { contents: `
      import React from 'react';
      import {createRoot} from 'react-dom/client';
      import Overview from ${JSON.stringify(component)};
      createRoot(document.getElementById('root')).render(<Overview payload={${JSON.stringify(payload)}}/>);
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
      '@mozaiks/chat-ui/ui': path.join(root, 'chat-ui/src/ui/index.js'),
    },
    define: { 'process.env.NODE_ENV': '"test"' },
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${path.join(root, 'chat-ui/src/ui').replaceAll('\\', '/')}";`
      + `\n@source "${path.dirname(component).replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles.css}</style></head><body style="margin:0;padding:16px"><main id="root" style="max-width:640px;margin:auto"></main><script type="module" src="/fixture.js"></script></body></html>`;
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  return page;
}

test('discovery overview exposes evidence interactively and keeps partial coverage visible on desktop and mobile', async t => {
  const page = await discoveryFixture(t, {
    app_name: 'Neighbourhood tools', github_repo: 'example/neighbourhood-tools', status: 'ready',
    analysis_summary: 'Neighbours share tools and coordinate borrowing.',
    warnings: ['Provider note'], current_app_context_version_id: 'context-private-id',
    app_intelligence_catalog: {
      present: true, snapshot_id: 'snapshot-private-id',
      warnings: ['source_scan_file_limit_reached: 500'],
      coverage: { file_count: 500, node_count: 2000, language_counts: { python: 420, javascript: 80 } },
      capabilities: Array.from({ length: 8 }, (_, i) => ({ label: `tool_booking_${i}`, kind: 'module_boundary', evidence_paths: [`app/modules/booking_${i}/backend/service.py`] })),
      architecture: { module_roots: [{ module_id: 'tool_bookings' }], ui_surfaces: [{ path: 'app/services/routes/inventory.py', role: 'route' }] },
      integration_surfaces: [{ label: 'email_delivery', kind: 'integration' }],
      data_surfaces: [
        { path: 'app/modules/tool_inventory/backend/schemas.py', role: 'data_model' },
        { path: 'app/modules/tool_bookings/backend/schemas.py', role: 'data_model' },
      ],
    },
  });
  await expect(page.getByRole('heading', { name: 'Neighbourhood tools' })).toBeVisible();
  await expect(page.getByText('Neighbours share tools and coordinate borrowing.', { exact: true })).toBeVisible();
  await expect(page.getByText('Partial analysis', { exact: true })).toBeVisible();
  await expect(page.getByText('The scan reached its limit.', { exact: false })).toBeVisible();
  await expect(page.getByText('Ready', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Tool Booking 7', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Show all 8', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tool Booking 7', exact: true })).toBeVisible();
  const firstCapability = page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'Tool Booking 0', exact: true }) });
  await expect(firstCapability.locator('pre')).not.toBeVisible();
  await firstCapability.locator('summary').click();
  await expect(firstCapability.locator('pre')).toContainText('app/modules/booking_0/backend/service.py');
  const context = page.locator('details').filter({ has: page.getByText('Index & agent context details', { exact: true }) });
  await expect(context.locator('pre')).not.toBeVisible();
  await context.locator('summary').click();
  await expect(context.locator('pre')).toContainText('context-private-id');
  await context.locator('summary').click();
  await page.getByRole('button', { name: 'Code structure', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Code structure', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('heading', { name: 'Routes, pages & components', exact: true })).toBeVisible();
  await expect(page.getByText('Route', { exact: true })).toBeVisible();
  await expect(page.getByText('UI surfaces', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Connections & data', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Email Delivery', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tool Inventory · Schemas', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tool Bookings · Schemas', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Review notes', exact: true }).click();
  await expect(page.getByText('source_scan_file_limit_reached: 500', { exact: true })).toBeVisible();
  await expect(page.getByText('Provider note', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Capabilities', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tool Booking 0', exact: true })).toBeVisible();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
});

test('discovery overview does not invent product analysis or app readiness when source evidence is absent', async t => {
  const page = await discoveryFixture(t, { github_repo: 'example/unscanned-app' });
  await expect(page.getByText('Product analysis has not been added', { exact: false })).toBeVisible();
  await expect(page.getByText('Partial analysis', { exact: true })).toBeVisible();
  await expect(page.getByText('No capability boundaries were included in this scan.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: /preview|build|launch/i })).toHaveCount(0);
});

test('discovery overview uses emitted product synthesis and health coverage even when warning codes are filtered', async t => {
  const page = await discoveryFixture(t, {
    app_name: 'Tool library', status: 'ready', summary: 'A community tool library.',
    stack: ['React', 'FastAPI'],
    features: [
      { name: 'Reserve tools', description: 'Members request tools for a date range.', files: ['app/modules/booking/service.py'] },
      { name: 'Import / Export', description: 'Move the inventory between systems.' },
    ],
    app_intelligence_catalog: {
      present: true, coverage: { file_count: 500, symbol_count: 4000, scan_health: { limit_reached: true } },
      capabilities: [{ capability_id: 'module:booking', label: 'booking', kind: 'module_boundary' }],
    },
  });
  await expect(page.getByText('A community tool library.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Reserve tools', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Import / Export', exact: true })).toBeVisible();
  await expect(page.getByText('FastAPI', { exact: true })).toBeVisible();
  await expect(page.getByText('Members request tools for a date range.', { exact: true })).toBeVisible();
  await expect(page.getByText('Partial analysis', { exact: true })).toBeVisible();
  await expect(page.getByText('Module Boundary', { exact: true })).toHaveCount(0);
  await expect(page.getByText('500 files indexed', { exact: true })).toHaveCount(0);
});
