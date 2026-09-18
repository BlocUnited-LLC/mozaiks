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

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const component = path.join(path.dirname(shell), 'chat-ui/src/ui/primitives/SummaryStrip.jsx');
const labels = [
  'Planned reading', 'Currently reading', 'Finished reading',
  'Average completion time', 'Pages read this month', 'Yearly reading target',
];
const items = labels.map((label, index) => ({ id: `metric-${index}`, label, value: index + 1 }));

async function measure(page) {
  return page.locator('[aria-label="Summary metrics"]').evaluate(root => {
    const rect = element => {
      const { left, right, top, bottom, width } = element.getBoundingClientRect();
      return { left, right, top, bottom, width };
    };
    const text = element => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return {
        ...rect(element), content: element.textContent,
        clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight, scrollHeight: element.scrollHeight,
        whiteSpace: getComputedStyle(element).whiteSpace,
        textOverflow: getComputedStyle(element).textOverflow,
        lines: [...range.getClientRects()].map(({ left, right, top, bottom }) => ({ left, right, top, bottom })),
      };
    };
    return {
      root: rect(root), grid: rect(root.firstElementChild),
      pageWidth: document.documentElement.scrollWidth, viewportWidth: window.innerWidth,
      radius: getComputedStyle(root).borderTopLeftRadius,
      cards: [...root.firstElementChild.children].map(card => ({
        ...rect(card), label: text(card.firstElementChild),
        value: text(card.children[1].firstElementChild),
        detail: card.children[1].children[1] && getComputedStyle(card.children[1].children[1]).display !== 'none'
          ? text(card.children[1].children[1]) : null,
      })),
    };
  });
}

function assertContained(metrics, expectedItems) {
  assert.equal(metrics.cards.length, expectedItems.length);
  assert.ok(metrics.pageWidth <= metrics.viewportWidth, 'page must not overflow horizontally');
  assert.equal(metrics.radius, '8px', 'keep the shipped radius token');
  metrics.cards.forEach((card, index) => {
    assert.equal(card.label.content, expectedItems[index].label);
    assert.equal(card.value.content, String(expectedItems[index].value));
    assert.ok(card.left >= metrics.root.left && card.right <= metrics.root.right + 1, 'cell stays in strip');
    assert.ok(card.top >= metrics.root.top && card.bottom <= metrics.root.bottom + 1, 'cell is not clipped');
    assert.ok(card.label.bottom <= card.value.top, 'label and value do not overlap');
    for (const [name, content] of Object.entries({ label: card.label, value: card.value, detail: card.detail })) {
      if (!content) continue;
      assert.notEqual(content.textOverflow, 'ellipsis', `${name} must not be truncated`);
      assert.notEqual(content.whiteSpace, 'nowrap', `${name} must be allowed to wrap`);
      assert.ok(content.scrollWidth <= content.clientWidth + 1, `${name} must fit horizontally`);
      assert.ok(content.scrollHeight <= content.clientHeight + 1, `${name} must fit vertically`);
      for (const line of content.lines) {
        assert.ok(line.left >= card.left && line.right <= card.right + 1, `${name} text stays inside cell`);
        assert.ok(line.top >= card.top && line.bottom <= card.bottom + 1, `${name} text is not clipped`);
      }
    }
    for (const other of metrics.cards.slice(index + 1)) {
      assert.ok(card.right <= other.left || other.right <= card.left
        || card.bottom <= other.top || other.bottom <= card.top, 'cells do not overlap');
    }
  });
}

function assertColumns(metrics, columns) {
  const firstRow = metrics.cards.filter(card => Math.abs(card.top - metrics.cards[0].top) < 1);
  assert.equal(firstRow.length, columns, 'column count follows the parent width and item count');
  assert.ok(Math.abs(firstRow[0].left - metrics.grid.left) < 1);
  assert.ok(Math.abs(firstRow.at(-1).right - metrics.grid.right) < 1, 'no unused trailing column');
  assert.ok(Math.max(...metrics.cards.map(card => card.width))
    - Math.min(...metrics.cards.map(card => card.width)) < 1, 'columns have equal widths');
}

test('SummaryStrip intrinsic layout in the browser', async (t) => {
  const entry = `
    import React, { useEffect, useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import SummaryStrip from ${JSON.stringify(component)};
    function Fixture() {
      const [state, setState] = useState({ items: [], width: 276, revision: 0 });
      useEffect(() => { window.renderStrip = setState; }, []);
      return <main style={{ padding: 16 }}>
        <div id="fixture" data-revision={state.revision}
          style={{ width: state.width, maxWidth: '100%' }}>
          <SummaryStrip items={state.items} />
        </div>
      </main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    define: { 'process.env.NODE_ENV': '"test"' },
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${component.replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles.css}
      :root { --mz-background: 0 0% 100%; --mz-foreground: 220 15% 15%;
        --mz-card: 210 20% 98%; --mz-border: 215 18% 65%;
        --mz-muted-foreground: 215 12% 35%; --mz-radius: 8px; }
      body { margin: 0; background: white; font-family: Arial, sans-serif; }
    </style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`;
  const server = http.createServer((req, res) => {
    if (req.url !== '/' && req.url !== '/fixture.js') { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
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
  await page.waitForFunction(() => window.renderStrip);
  let revision = 0;
  async function render(nextItems, width) {
    revision += 1;
    await page.evaluate(state => window.renderStrip(state), { items: nextItems, width, revision });
    await page.waitForFunction(value => document.querySelector('#fixture').dataset.revision === String(value), revision);
  }
  const screenshots = process.env.SUMMARY_STRIP_SCREENSHOT_DIR || path.join(shell, 'test-results/summary-strip');
  await fs.mkdir(screenshots, { recursive: true });
  for (const layout of [
    { name: 'compact', viewport: 1440, width: 276, columns: 1 },
    { name: 'wide', viewport: 1440, width: 1120, columns: 6 },
    { name: 'mobile', viewport: 390, width: 1120, columns: 2 },
  ]) {
    for (const count of [1, 3, 4, 6]) {
      await t.test(`${count} metrics: ${layout.name}`, async () => {
        await page.setViewportSize({ width: layout.viewport, height: 844 });
        const expected = items.slice(0, count);
        await render(expected, layout.width);
        await page.screenshot({ path: path.join(screenshots, `${layout.name}-${count}.png`), fullPage: true });
        const metrics = await measure(page);
        assertContained(metrics, expected);
        assertColumns(metrics, Math.min(count, layout.columns));
      });
    }
  }
  await t.test('container resize reflows without changing the viewport', async () => {
    await page.setViewportSize({ width: 1440, height: 844 });
    for (const [width, columns] of [[1120, 6], [276, 1], [520, 3], [1120, 6]]) {
      await render(items, width);
      const metrics = await measure(page);
      assertContained(metrics, items);
      assertColumns(metrics, columns);
    }
  });
  await t.test('long labels, values and details wrap even below the preferred minimum width', async () => {
    const expected = [{ id: 'long', label: 'UnbrokenMetricName'.repeat(5),
      value: '1234567890'.repeat(4), detail: 'UnbrokenDetail'.repeat(5) }];
    await page.setViewportSize({ width: 1440, height: 844 });
    for (const width of [276, 120]) {
      await render(expected, width);
      await page.screenshot({ path: path.join(screenshots, `long-${width}.png`), fullPage: true });
      const metrics = await measure(page);
      assertContained(metrics, expected);
      assertColumns(metrics, 1);
      assert.equal(metrics.cards[0].detail.content, expected[0].detail);
    }
  });
  await t.test('empty and invalid inputs keep existing filtering behavior', async () => {
    for (const empty of [[], null, {}, [null, false, 'invalid', { value: 42 }]]) {
      await render(empty, 276);
      assert.equal(await page.locator('[aria-label="Summary metrics"]').count(), 0);
    }
    await render([null, items[0], { value: 42 }, items[1]], 520);
    const metrics = await measure(page);
    assertContained(metrics, items.slice(0, 2));
    assertColumns(metrics, 2);
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpectedRequests, []);
});
