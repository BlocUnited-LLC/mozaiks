import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';
import YAML from 'yaml';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const component = path.join(root, 'factory_app/workflows/ValueEngine/ui/ValueEngine/components/ConceptBlueprint.js');

test('concept review renders, submits once, rejects transport errors, and resets for a new draft', async (t) => {
  const tools = YAML.parse(await fs.readFile(path.join(root, 'factory_app/workflows/ValueEngine/tools.yaml'), 'utf8'));
  const contract = tools.tools.find((tool) => tool.function === 'save_value_manifest').ui_contract;
  const entry = `
    import React, { useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import ConceptBlueprint from ${JSON.stringify(component)};
    function Fixture() {
      const [draft, setDraft] = useState(1);
      const [result, setResult] = useState(null);
      const [reject, setReject] = useState(false);
      const payload = { title: 'Concept Blueprint: Customer Ledger', app_id: 'customer-ledger',
        review_id: 'review-' + draft, concept_overview: 'A private customer tracker for independent service businesses.',
        blueprint: {app_name: 'Customer Ledger', value_proposition: 'Customer records in one place',
          target_user: 'Independent service businesses', mvp_scope: {core_features: ['List, add, and edit customers', 'Search and status filters']},
          brand_intent: {style_summary: 'Clear, practical, and accessible', appearance_hint: 'Light'},
          app_ui_requirements: ['Responsive customer list and edit form']},
        api_endpoints: [{method: 'GET', path: '/api/customers', description: 'List customers'}],
        ui_contract: ${JSON.stringify(contract)}};
      return <main style={{maxWidth: 1100, margin: '0 auto'}}>
        <button onClick={() => {setDraft(draft + 1); setResult(null);}}>Next draft</button>
        <label><input type="checkbox" checked={reject} onChange={(event) => setReject(event.target.checked)} />Reject response</label>
        <ConceptBlueprint payload={payload} toolCallId={'tool-' + draft} sourceWorkflowName="ValueEngine"
          onResponse={async (value) => { if (reject) throw Error('Simulated transport failure'); setResult(value); }} />
        <output aria-label="Review response">{result ? JSON.stringify(result) : ''}</output>
      </main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture />);
  `;
  const bundle = await build({
    stdin: {contents: entry, resolveDir: shell, loader: 'jsx'}, bundle: true, write: false,
    jsx: 'automatic', loader: {'.js': 'jsx'}, nodePaths: [path.join(shell, 'node_modules')],
    alias: {'@mozaiks/chat-ui': path.join(root, 'chat-ui/src')},
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8')) + `\n@source "${component.replaceAll('\\', '/')}";`,
    {from: path.join(shell, 'styles.css')},
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles.css}
      :root {--mz-background:0 0% 100%;--mz-foreground:0 0% 10%;--mz-card:0 0% 100%;--mz-border:0 0% 80%;
        --mz-primary:170 80% 25%;--mz-primary-foreground:0 0% 100%;--mz-muted:0 0% 96%;--mz-muted-foreground:0 0% 35%;
        --mz-destructive:0 80% 40%;--mz-radius:8px;--mz-font-sans:Arial;--mz-font-heading:Arial;
        --color-text-primary:#1a1a1a;--color-primary-light:#0d7666;}
    </style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`;
  const server = http.createServer((req, res) => {
    const script = req.url === '/fixture.js';
    res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    res.end(script ? bundle.outputFiles[0].text : html);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({headless: true});
  t.after(() => browser.close());
  const screenshots = path.join(shell, 'test-results/concept-review');
  await fs.mkdir(screenshots, {recursive: true});
  for (const viewport of [{width: 1440, height: 1100}, {width: 390, height: 844}]) {
    const page = await browser.newPage({viewport});
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByRole('button', {name: 'Approve Concept', exact: true}).waitFor();
    assert.deepEqual(errors, []);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    const heading = await page.getByRole('heading', {name: 'Concept Blueprint: Customer Ledger', exact: true}).boundingBox();
    const valueProp = await page.getByText('Customer records in one place', {exact: true}).boundingBox();
    assert.ok(heading.x + heading.width <= valueProp.x || heading.y + heading.height <= valueProp.y);
    await page.getByRole('region', {name: 'Concept review'}).scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(screenshots, `${viewport.width}.png`), fullPage: true});
    await page.getByLabel('Requested changes').fill('Add status filtering.');
    await page.getByRole('button', {name: 'Request Changes', exact: true}).click();
    await page.getByRole('status').filter({hasText: 'Review submitted'}).waitFor();
    assert.deepEqual(JSON.parse(await page.getByLabel('Review response').textContent()), {
      action: 'request_changes', approved: false, review_id: 'review-1', rationale: 'Add status filtering.',
    });
    assert.equal(await page.getByRole('button', {name: 'Approve Concept', exact: true}).count(), 0);
    await page.getByRole('button', {name: 'Next draft', exact: true}).click();
    assert.equal(await page.getByLabel('Requested changes').inputValue(), '');
    await page.getByLabel('Reject response').check();
    await page.getByRole('button', {name: 'Approve Concept', exact: true}).click();
    await page.getByRole('alert').waitFor();
    await page.getByLabel('Reject response').uncheck();
    await page.getByRole('button', {name: 'Approve Concept', exact: true}).click();
    await page.getByRole('status').filter({hasText: 'Review submitted'}).waitFor();
    assert.equal(JSON.parse(await page.getByLabel('Review response').textContent()).review_id, 'review-2');
    assert.deepEqual(errors, []);
    await page.close();
  }
});
