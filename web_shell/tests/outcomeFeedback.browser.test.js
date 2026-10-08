import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('human feedback awaits authenticated acceptance, retries failures, and permits skipping', async (t) => {
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import OutcomeFeedback from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/core/ui/OutcomeFeedback.js'))};
    import {submitToolCallResponse} from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/adapters/uiToolResponse.js'))};
    window.fixture = {requests: [], status: 503};
    window.fetch = async (url, options) => {
      window.fixture.requests.push({url, headers: options.headers, body: JSON.parse(options.body)});
      await new Promise(resolve => { window.fixture.release = resolve; });
      return Response.json({status: window.fixture.status === 200 ? 'success' : 'rejected'}, {status: window.fixture.status});
    };
    const root = createRoot(document.getElementById('root'));
    window.mount = key => root.render(<OutcomeFeedback key={key} onResponse={async response => {
      if (window.fixture.status === false) return false;
      await submitToolCallResponse('server-event', response, {token: 'signed-user-token'});
      return true;
    }} />);
    window.mount('first');
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
  });
  const server = http.createServer((req, res) => {
    const script = req.url === '/fixture.js';
    res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    res.end(script ? bundle.outputFiles[0].text : '<html><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const send = page.getByRole('button', { name: 'Send feedback', exact: true });
  assert.equal(await send.isDisabled(), true);
  assert.equal(await page.getByRole('button', {name: 'Skip', exact: true}).isEnabled(), true);
  await page.getByRole('radio', {name: '4 stars', exact: true}).click();
  await page.getByRole('radio', {name: '4 stars', exact: true}).press('ArrowRight');
  assert.equal(await page.getByRole('radio', {name: '5 stars', exact: true}).isChecked(), true);
  await page.getByRole('radio', {name: '5 stars', exact: true}).press('ArrowLeft');
  assert.equal(await page.getByRole('radio', {name: '4 stars', exact: true}).isChecked(), true);
  await page.getByRole('button', {name: 'No', exact: true}).click();
  await page.getByRole('combobox').selectOption('partial');
  await send.evaluate(element => { element.click(); element.click(); });
  await page.waitForFunction(() => window.fixture.requests.length === 1);
  assert.equal(await page.getByRole('status').count(), 0);
  assert.equal(await page.getByRole('button', {name: 'Skip', exact: true}).isDisabled(), true);
  await page.evaluate(() => window.fixture.release());
  await page.getByText('Your feedback could not be submitted. Please try again.').waitFor();
  await page.evaluate(() => { window.fixture.status = false; });
  await send.click();
  assert.equal(await page.getByRole('status').count(), 0);
  await page.evaluate(() => { window.fixture.status = 200; });
  await send.click();
  await page.waitForFunction(() => window.fixture.requests.length === 2);
  await page.evaluate(() => window.fixture.release());
  await page.getByRole('status').filter({hasText: 'Response received.'}).waitFor();
  assert.deepEqual(await page.evaluate(() => window.fixture.requests.at(-1)), {
    url: '/api/tool-call/respond', headers: {'Content-Type': 'application/json', Authorization: 'Bearer signed-user-token'},
    body: {event_id: 'server-event', response_data: {status: 'submitted', rating: 4, helpful: false, outcome: 'partial'}},
  });
  await page.evaluate(() => window.mount('second'));
  await page.getByRole('button', {name: 'Skip', exact: true}).click();
  await page.waitForFunction(() => window.fixture.requests.length === 3);
  await page.evaluate(() => window.fixture.release());
  await page.getByRole('status').filter({hasText: 'Response received.'}).waitFor();
  assert.deepEqual(await page.evaluate(() => window.fixture.requests.at(-1).body.response_data), {status: 'skipped'});
  assert.deepEqual(errors, []);
});

test('feedback acknowledges visibility after mount, not a buffered or offscreen offer', async (t) => {
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import OutcomeFeedback from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/core/ui/OutcomeFeedback.js'))};
    import {acknowledgeFeedbackRender} from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/adapters/uiToolResponse.js'))};
    window.acks = [];
    window.fetch = async (url, options) => {
      window.acks.push({url, headers: options.headers, body: JSON.parse(options.body)});
      return Response.json({status: 'success'});
    };
    const root = createRoot(document.getElementById('root'));
    window.mount = (key, eventId) => root.render(
      <div style={{marginTop: '1400px'}}>
        <OutcomeFeedback key={key} toolCallId={eventId} onResponse={async () => true}
          onRendered={(id) => acknowledgeFeedbackRender(id, {token: 'signed-user-token'})} />
      </div>
    );
    window.mount('first', 'server-event');
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
  });
  const server = http.createServer((req, res) => {
    const script = req.url === '/fixture.js';
    res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    res.end(script ? bundle.outputFiles[0].text : '<html><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.addInitScript(() => {
    window.feedbackHidden = true;
    Object.defineProperty(document, 'visibilityState', {
      configurable: true, get: () => window.feedbackHidden ? 'hidden' : 'visible',
    });
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByText('How was this result?').waitFor({ state: 'attached' });
  assert.deepEqual(await page.evaluate(() => window.acks), []);
  await page.getByText('How was this result?').scrollIntoViewIfNeeded();
  assert.deepEqual(await page.evaluate(() => window.acks), []);
  await page.evaluate(() => {
    window.feedbackHidden = false;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForFunction(() => window.acks.length === 1);
  assert.deepEqual(await page.evaluate(() => window.acks), [{
    url: '/api/workflow-feedback/rendered',
    headers: {'Content-Type': 'application/json', Authorization: 'Bearer signed-user-token'},
    body: {event_id: 'server-event'},
  }]);
  await page.evaluate(() => window.mount('first', 'server-event'));
  assert.equal(await page.evaluate(() => window.acks.length), 1);
  assert.deepEqual(errors, []);
});
