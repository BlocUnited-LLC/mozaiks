import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';
import YAML from 'yaml';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const component = path.join(root, 'factory_app/workflows/ValueEngine/ui/ValueEngine/components/ConceptBlueprint.js');

test('concept review stays concise and preserves declared review actions on desktop and mobile', async (t) => {
  const tools = YAML.parse(await fs.readFile(path.join(root, 'factory_app/workflows/ValueEngine/tools.yaml'), 'utf8'));
  const contract = tools.tools.find((tool) => tool.function === 'save_value_manifest').ui_contract;
  const entry = `
    import React, { useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import ConceptBlueprint from ${JSON.stringify(component)};
    import { submitToolCallResponse } from ${JSON.stringify(path.join(root, 'chat-ui/src/adapters/uiToolResponse.js'))};
    window.submissions = [];
    function Fixture() {
      const [draft, setDraft] = useState(1);
      const [result, setResult] = useState(null);
      const [reject, setReject] = useState(false);
      const [actions, setActions] = useState(null);
      window.setReviewActions = setActions;
      const payload = { title: 'Concept Blueprint: Customer Ledger', app_id: 'customer-ledger',
        review_id: 'review-' + draft, concept_overview: 'A private customer tracker for independent service businesses. ' +
          'Customer records, notes, and search work together to make day-to-day follow-up easier. '.repeat(8),
        blueprint: {app_name: 'Customer Ledger', value_proposition: 'Customer records in one place',
          target_user: 'Independent service businesses', mvp_scope: {core_features: ['List, add, and edit customers',
            'Search and status filters', 'Private customer notes', 'Follow-up reminders', 'Export customer records'],
            deferred_features: ['Shared team workspaces']}, unique_differentiators: ['Simple daily follow-up'],
          brand_intent: {style_summary: 'Clear, practical, and accessible', appearance_hint: 'Light',
            brand_keywords: ['Calm', 'Trustworthy'], experience_goals: ['Find a customer quickly']},
          app_ui_requirements: ['Responsive customer list and edit form'], capability_pack_hints: ['crm-pack'],
          agentic_capabilities: ['No AI workflows required']},
        api_endpoints: [{method: 'GET', path: '/api/customers', description: 'List customers'},
          {method: 'POST', path: '/api/customers/customer-records/export-with-a-deliberately-long-path', description: 'Export records'}],
        ...(actions ? {actions} : {}),
        ui_contract: ${JSON.stringify(contract)}};
      return <main style={{maxWidth: 640, margin: '0 auto', padding: 12}}>
        <button onClick={() => {setDraft(draft + 1); setResult(null);}}>Next draft</button>
        <label><input type="checkbox" checked={reject} onChange={(event) => setReject(event.target.checked)} />Reject response</label>
        <ConceptBlueprint payload={payload} toolCallId={'tool-' + draft} sourceWorkflowName="ValueEngine"
          onResponse={async (value) => {
            window.submissions.push(value);
            if (window.holdReview) await new Promise(resolve => {window.finishReview = resolve;});
            if (window.staleReview) return submitToolCallResponse('stale-event', value, {
              fetchImpl: async () => ({ok: false, status: 404}),
            });
            if (reject) throw Error('Simulated transport failure');
            setResult(value);
          }} />
        <output aria-label="Review response">{result ? JSON.stringify(result) : ''}</output>
      </main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture />);
  `;
  const bundle = await build({
    stdin: {contents: entry, resolveDir: shell, loader: 'jsx'}, bundle: true, write: false,
    jsx: 'automatic', loader: {'.js': 'jsx', '.png': 'dataurl'}, nodePaths: [path.join(shell, 'node_modules')],
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
  for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
    const page = await browser.newPage({viewport});
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const approve = page.getByRole('button', {name: 'Approve Concept', exact: true});
    await expect(approve).toBeVisible();
    assert.deepEqual(errors, []);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    const heading = await page.getByRole('heading', {name: 'Customer Ledger', exact: true}).boundingBox();
    const benefit = page.locator('article header').getByText('Customer records in one place', {exact: true});
    const valueProp = await benefit.boundingBox();
    const approvalBox = await approve.boundingBox();
    assert.ok(heading.y + heading.height <= valueProp.y);
    assert.ok(approvalBox.y + approvalBox.height < viewport.height, 'approval must be available without scrolling');
    assert.ok(approvalBox.y < 240, 'approval should stay near the top even with a long overview');
    await expect(approve).toHaveCount(1);
    await expect(page.getByRole('region', {name: 'Core features'}).getByRole('listitem')).toHaveCount(3);
    await expect(page.getByText('2 more in Product details below.', {exact: true})).toBeVisible();
    for (const title of ['Product details', 'Design details', 'Technical details']) {
      await expect(page.locator('details').filter({has: page.locator('summary', {hasText: title})})).not.toHaveAttribute('open');
    }
    for (const text of ['customer-ledger', 'tool-1', 'ValueEngine', 'crm-pack', '/api/customers']) {
      await expect(page.getByText(text, {exact: true})).toBeHidden();
    }
    await page.screenshot({path: path.join(screenshots, `${viewport.width}.png`), fullPage: true});

    await page.getByText('Product details', {exact: true}).click();
    await expect(page.getByText('Follow-up reminders', {exact: true})).toBeVisible();
    await expect(page.getByText('Shared team workspaces', {exact: true})).toBeVisible();
    await page.getByText('Product details', {exact: true}).click();
    await page.getByText('Design details', {exact: true}).click();
    await expect(page.getByText('Find a customer quickly', {exact: true})).toBeVisible();
    await expect(page.getByText('Responsive customer list and edit form', {exact: true})).toBeVisible();
    await page.getByText('Design details', {exact: true}).click();
    const technical = page.getByText('Technical details', {exact: true});
    await technical.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('customer-ledger', {exact: true})).toBeVisible();
    await expect(page.getByText('/api/customers', {exact: true})).toBeVisible();
    await expect(page.getByRole('row')).toHaveCount(3);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.keyboard.press('Enter');
    await page.getByText('Request changes', {exact: true}).click();
    await page.getByLabel('Requested changes').fill('Add status filtering.');
    await page.evaluate(() => {window.holdReview = true;});
    await page.getByRole('button', {name: 'Request Changes', exact: true}).click();
    await expect(approve).toBeDisabled();
    await expect(page.getByRole('button', {name: 'Request Changes', exact: true})).toBeDisabled();
    assert.equal(await page.evaluate(() => window.submissions.length), 1);
    await page.evaluate(() => {window.holdReview = false; window.finishReview();});
    await page.getByRole('status').filter({hasText: 'Review submitted'}).waitFor();
    assert.deepEqual(JSON.parse(await page.getByLabel('Review response').textContent()), {
      action: 'request_changes', approved: false, review_id: 'review-1', rationale: 'Add status filtering.',
    });
    assert.equal(await page.getByRole('button', {name: 'Approve Concept', exact: true}).count(), 0);
    await page.getByRole('button', {name: 'Next draft', exact: true}).click();
    assert.equal(await page.getByLabel('Requested changes').inputValue(), '');
    await page.getByLabel('Reject response').check();
    await page.getByRole('button', {name: 'Approve Concept', exact: true}).click();
    await expect(page.getByRole('alert')).toHaveText('The review could not be submitted.');
    await page.getByLabel('Reject response').uncheck();
    await page.getByRole('button', {name: 'Approve Concept', exact: true}).click();
    await page.getByRole('status').filter({hasText: 'Review submitted'}).waitFor();
    assert.equal(JSON.parse(await page.getByLabel('Review response').textContent()).review_id, 'review-2');
    await page.getByRole('button', {name: 'Next draft', exact: true}).click();
    await page.getByRole('button', {name: 'Cancel', exact: true}).click();
    await expect(page.getByRole('status').filter({hasText: 'Review submitted'})).toHaveText('Review submitted');
    assert.deepEqual(JSON.parse(await page.getByLabel('Review response').textContent()), {
      action: 'cancel', approved: false, review_id: 'review-3', rationale: '',
    });
    await page.getByRole('button', {name: 'Next draft', exact: true}).click();
    await page.evaluate(() => {window.staleReview = true;});
    await approve.click();
    await expect(page.getByRole('alert')).toHaveText('This review is no longer active. Your decision was not applied. Reopen the workflow to load its current review.');
    await expect(page.getByRole('status').filter({hasText: 'Review submitted'})).toHaveCount(0);
    await expect(approve).toBeEnabled();
    await page.evaluate(() => {window.staleReview = false;});
    await page.evaluate(() => window.setReviewActions([{id: 'request_changes', label: 'Send feedback', approved: false}]));
    await expect(approve).toHaveCount(0);
    await expect(page.getByRole('button', {name: 'Cancel', exact: true})).toHaveCount(0);
    await page.getByText('Request changes', {exact: true}).click();
    await expect(page.getByRole('button', {name: 'Send feedback', exact: true})).toBeVisible();
    assert.deepEqual(errors, []);
    await page.close();
  }
});
