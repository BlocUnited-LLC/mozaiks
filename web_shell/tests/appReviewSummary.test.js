import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const component = path.join(root, 'factory_app/workflows/AppReview/ui/AppReview/AppReviewSummary.jsx');
const compiled = await build({
  entryPoints: [component],
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
  for (const label of ['Bundle acceptance', 'Build validation', 'Integration checks']) {
    assert.match(html, new RegExp(`${label}</span><span>Missing</span>`));
  }
  assert.match(html, /Security scan \(advisory\)<\/span><span>Missing<\/span>/);
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
  for (const label of ['Bundle acceptance', 'Integration checks']) {
    assert.match(html, new RegExp(`${label}</span><span>Passed</span>`));
  }
  assert.match(html, /Security scan \(advisory\)<\/span><span>No findings<\/span>/);
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

test('advisory security results remain distinct from required checks in the browser', async (t) => {
  const entry = `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { flushSync } from 'react-dom';
    import AppReviewSummary from ${JSON.stringify(component)};
    const root = createRoot(document.getElementById('root'));
    window.renderReview = payload => flushSync(() => root.render(
      <main style={{maxWidth: 640, margin: '0 auto', padding: 16}}>
        <AppReviewSummary payload={payload} />
      </main>
    ));
  `;
  const bundle = await build({
    stdin: {contents: entry, resolveDir: shell, loader: 'jsx'}, bundle: true, write: false,
    jsx: 'automatic', loader: {'.js': 'jsx', '.png': 'dataurl'}, nodePaths: [path.join(shell, 'node_modules')],
    alias: {'@mozaiks/chat-ui': path.join(root, 'chat-ui/src')},
    plugins: [{name: 'no-live-api', setup(builder) {
      builder.onResolve({filter: /studioApi\.js$/}, args => ({path: args.path, namespace: 'mock'}));
      builder.onLoad({filter: /.*/, namespace: 'mock'}, () => ({
        contents: 'export const studioFetch = () => { throw Error("No live API allowed"); };',
      }));
    }}],
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${component.replaceAll('\\', '/')}";`
      + `\n@source "${path.join(root, 'chat-ui/src/ui/primitives').replaceAll('\\', '/')}";`,
    {from: path.join(shell, 'styles.css')},
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles.css}
      :root {--mz-background:0 0% 100%;--mz-foreground:0 0% 10%;--mz-card:0 0% 100%;--mz-border:0 0% 80%;
        --mz-primary:170 80% 25%;--mz-primary-foreground:0 0% 100%;--mz-muted:0 0% 96%;--mz-muted-foreground:0 0% 35%;
        --mz-success:140 80% 25%;--mz-warning:35 90% 30%;--mz-destructive:0 80% 40%;--mz-radius:8px;--mz-font-sans:Arial;}
    </style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`;
  const server = http.createServer((req, res) => {
    if (req.url !== '/' && req.url !== '/fixture.js') { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  const unexpectedRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url === `${baseUrl}/` || url === `${baseUrl}/fixture.js`) return route.continue();
    unexpectedRequests.push(url);
    return route.abort();
  });
  await page.goto(baseUrl);
  await page.waitForFunction(() => window.renderReview);
  const payload = {
    app_validation_status: 'passed', app_bundle_acceptance_status: 'passed', integration_tests_passed: true,
    can_promote: true, artifact_version_id: 'artifact', build_registry_id: 'owned-build',
  };
  const caveat = page.getByText('An automated scan cannot establish that the app is secure.', {exact: true});
  const cases = [
    {name: 'no findings', summary: {status: 'passed', finding_count: 0, findings: []}, label: 'No findings', tone: 'text-muted-foreground', caveat: true},
    {name: 'count without details', summary: {status: 'attention_required', finding_count: 2}, label: 'Needs review', tone: 'text-warning', count: 2, caveat: true},
    {name: 'details without count', summary: {findings: [{title: 'Review authentication'}]}, label: 'Needs review', tone: 'text-warning', count: 1, caveat: true},
    {name: 'failed with empty findings', summary: {status: 'failed', finding_count: 0, findings: []}, label: 'Failed', tone: 'text-destructive'},
    {name: 'failed with findings', summary: {status: 'failed', finding_count: 1}, label: 'Failed', tone: 'text-destructive', count: 1, caveat: true},
    {name: 'attention with empty findings', summary: {status: 'attention_required', finding_count: 0, findings: []}, label: 'Needs review', tone: 'text-warning', caveat: true},
    {name: 'unassessed', summary: {status: 'not_assessed', success: false, finding_count: 0}, label: 'Not assessed', tone: 'text-warning'},
    {name: 'skipped', summary: {status: 'skipped', finding_count: 0}, label: 'Skipped', tone: 'text-muted-foreground'},
    {name: 'missing', label: 'Missing', tone: 'text-warning'},
    {name: 'zero count without status', summary: {finding_count: 0, findings: []}, label: 'Missing', tone: 'text-warning'},
    {name: 'findings override passed', summary: {status: 'passed', finding_count: 1}, label: 'Needs review', tone: 'text-warning', count: 1, caveat: true},
  ];
  for (const width of [390, 1440]) {
    await page.setViewportSize({width, height: 900});
    for (const scenario of cases) {
      await t.test(`${scenario.name} at ${width}px`, async () => {
        await page.evaluate(value => window.renderReview(value), {...payload, security_readiness_summary: scenario.summary});
        const securityRow = page.getByText('Security scan (advisory)', {exact: true}).locator('..');
        const pill = securityRow.getByText(scenario.label, {exact: true}).locator('..');
        assert.equal(await pill.isVisible(), true);
        assert.equal(await pill.evaluate(element => element.classList.contains('text-success')), false);
        assert.equal(await pill.evaluate((element, tone) => element.classList.contains(tone), scenario.tone), true);
        for (const label of ['Bundle acceptance', 'Build validation', 'Integration checks']) {
          const requiredPill = page.getByText(label, {exact: true}).locator('..').getByText('Passed', {exact: true}).locator('..');
          assert.equal(await requiredPill.isVisible(), true);
          assert.equal(await requiredPill.evaluate(element => element.classList.contains('text-success')), true);
        }
        assert.equal(await caveat.count(), scenario.caveat ? 1 : 0);
        const findings = page.getByText('Review security findings', {exact: true});
        assert.equal(await findings.count(), scenario.count ? 1 : 0);
        if (scenario.count) {
          assert.equal(await page.getByText(`${scenario.count} advisory finding${scenario.count === 1 ? '' : 's'} recorded for review.`, {exact: true}).isVisible(), true);
        }
        assert.equal(await page.getByRole('button', {name: /Promote to Active|Activate this version/}).isEnabled(), true);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
        assert.ok(await securityRow.evaluate(row => {
          const [label, status] = row.children;
          return label.getBoundingClientRect().right <= status.getBoundingClientRect().left;
        }), 'security label and status do not overlap');
      });
    }
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpectedRequests, []);
});
