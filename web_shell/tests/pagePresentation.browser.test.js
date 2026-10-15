// L1 component fixture: real renderers/controls/theme provider, synthetic schemas.
// This does not establish generated-app host, persistence or live-model acceptance.
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

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const screenshotDir = process.env.MOZAIKS_TEST_SCREENSHOTS;

test('shared page presentation preserves app typography, layout and interactive state', async t => {
  const entry = `
    import { useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import { MemoryRouter } from 'react-router-dom';
    import { ChatUIProvider } from '../chat-ui/src/context/ChatUIContext.jsx';
    import { PageRenderer } from '../chat-ui/src/ui/page-renderer/PageRenderer.jsx';
    import { PageFrame } from '../chat-ui/src/ui/page-renderer/PageFrame.jsx';
    import { PageHeader } from '../chat-ui/src/ui/primitives/PageHeader.jsx';
    import { applyTheme, DEFAULT_THEME } from '../chat-ui/src/styles/themeProvider.js';
    window.setTheme = (title, density = 'comfortable') => applyTheme({
      ...DEFAULT_THEME,
      theme: { density },
      fonts: { body: { family: 'Arial', fallbacks: 'sans-serif' }, heading: { family: 'Georgia', fallbacks: 'serif' } },
      ui: { page: { sectionGap: '1.75rem', ...(title === null ? {} : { title }) } },
    });
    window.setTheme(null);
    const form = { id: 'details', primitive: 'Form', config: {
      fields: [{ name: 'notes', label: 'Inspection notes', type: 'text' }, { name: 'inspector', label: 'Inspector', type: 'text' }],
      submit_label: 'Save notes',
    } };
    const supporting = { id: 'supporting', primitive: 'PageHeader', config: { title: 'Field collection' } };
    function Fixture() {
      const [schema, setSchema] = useState({ name: 'collection', title: 'Field collection', layout: 'sidebar', sections: [supporting, form] });
      const [view, setView] = useState('page');
      const [actions, setActions] = useState(0);
      window.setSchema = setSchema;
      window.setView = setView;
      window.form = form;
      window.supporting = supporting;
      return <>
        {view === 'titles' ? <>
          <section aria-label="Frame title"><PageFrame title="Field collection"><p>Editorial field notes</p></PageFrame></section>
          <section aria-label="Header title"><PageHeader title="Field collection" actions={[{ id:'inspect', label:'Inspect collection' }]}
            onAction={() => setActions(n => n + 1)} /></section>
          <section aria-label="Explicit font"><PageHeader title="Body override" title_font="body" /></section>
          <output aria-label="Inspections">{actions}</output>
        </> : <PageRenderer schema={schema} />}
      </>;
    }
    createRoot(document.getElementById('root')).render(
      <MemoryRouter><ChatUIProvider uiConfig={{chat:{defaultAppId:'presentation-fixture'}}}><Fixture /></ChatUIProvider></MemoryRouter>
    );
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' },
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    nodePaths: [path.join(shell, 'node_modules')],
    define: { 'process.env.NODE_ENV': '"test"', 'process.env': '{}', 'import.meta.env': '{}' },
  });
  const styles = await postcss([tailwindcss()]).process(
    await fs.readFile(path.join(shell, 'styles.css'), 'utf8')
      + `\n@source "${path.join(root, 'chat-ui/src').replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const requests = [];
  const server = http.createServer((req, res) => {
    if (req.url === '/fixture.js') {
      res.setHeader('Content-Type', 'text/javascript');
      res.end(bundle.outputFiles[0].text);
    } else if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html');
      res.end(`<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles.css}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
    } else {
      requests.push(req.url);
      res.statusCode = 404;
      res.end();
    }
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
  try {
    await expect(page.getByLabel('Inspection notes')).toBeVisible();
  } catch (error) {
    t.diagnostic(JSON.stringify({ errors, requests, body: await page.locator('body').innerText() }));
    throw error;
  }

  await t.test('both title paths honor two distinct roles and reset omitted values', async () => {
    await page.evaluate(() => window.setView('titles'));
    const frame = page.getByRole('region', { name: 'Frame title', exact: true }).getByRole('heading');
    const header = page.getByRole('region', { name: 'Header title', exact: true }).getByRole('heading');
    for (const [name, title, expected] of [
      ['editorial', { font: 'heading', weight: 'regular', size: 'xl', lineHeight: 'relaxed', letterSpacing: 'tight', textTransform: 'none' }, ['Georgia', '400', '48px', '72px', '-1.2px', 'none']],
      ['operations', { font: 'body', weight: 'bold', size: 'sm', lineHeight: 'tight', letterSpacing: 'wide', textTransform: 'uppercase' }, ['Arial', '700', '24px', '27.6px', '1.2px', 'uppercase']],
    ]) {
      await page.evaluate(title => window.setTheme(title), title);
      for (const heading of [frame, header]) {
        for (const [property, value] of ['font-family', 'font-weight', 'font-size', 'line-height', 'letter-spacing', 'text-transform'].map((p, i) => [p, expected[i]])) {
          if (property === 'font-family') await expect(heading).toHaveCSS(property, new RegExp(value));
          else await expect(heading).toHaveCSS(property, value);
        }
      }
      await expect(page.getByRole('heading', { name: 'Body override' })).toHaveCSS('font-family', /Arial/);
      if (screenshotDir) {
        await fs.mkdir(screenshotDir, { recursive: true });
        await page.screenshot({ path: path.join(screenshotDir, `page-title-${name}.png`), fullPage: true });
      }
    }
    await page.evaluate(() => window.setTheme(null));
    await expect(frame).toHaveCSS('text-transform', 'none');
    await expect(header).toHaveCSS('font-weight', '600');
    await expect(frame).toHaveCSS('font-family', /Georgia/);
    await page.getByRole('button', { name: 'Inspect collection' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('status', { name: 'Inspections' })).toHaveText('1');
    await page.getByRole('button', { name: 'Inspect collection' }).click();
    await expect(page.getByRole('status', { name: 'Inspections' })).toHaveText('2');
  });

  await t.test('sidebar reflows without losing typed input at all declared widths', async () => {
    await page.evaluate(() => window.setView('page'));
    const input = page.getByLabel('Inspection notes');
    await input.fill('Keep this unsent field observation');
    await input.evaluate(element => { window.originalInput = element; });
    for (const width of [320, 390, 768, 1280, 1440, 390]) {
      await page.setViewportSize({ width, height: 720 });
      await expect(input).toHaveValue('Keep this unsent field observation');
      assert.equal(await input.evaluate(element => element === window.originalInput), true);
      const sidebar = await page.locator('aside').boundingBox();
      const main = await page.locator('main').boundingBox();
      assert.ok(width < 768 ? main.y >= sidebar.y + sidebar.height : main.x >= sidebar.x + sidebar.width);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}px`);
      await expect(page.locator('aside').locator('..')).toHaveCSS('gap', '28px');
      await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    }
    await input.focus();
    await page.keyboard.press('Tab');
    await expect(page.getByLabel('Inspector', { exact: true })).toBeFocused();
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'page-sidebar-mobile.png'), fullPage: true });
    await page.evaluate(() => document.documentElement.style.fontSize = '32px');
    await page.setViewportSize({ width: 320, height: 480 });
    await expect(input).toHaveValue('Keep this unsent field observation');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.evaluate(() => document.documentElement.style.fontSize = '');
  });

  await t.test('all supported layouts use theme spacing and reject unknown layouts before reading data', async () => {
    for (const layout of ['grid', 'full-width', 'split']) {
      await page.evaluate(layout => window.setSchema({ name: 'layout', title: 'Collection', layout,
        sections: [window.supporting, window.form] }), layout);
      await expect(page.getByLabel('Inspection notes')).toBeVisible();
      const section = page.locator('[data-page="layout"] > div > div > div');
      await expect(section).toHaveCSS('gap', '28px');
    }
    for (const layout of ['invented', 'toString', null]) {
      await page.evaluate(layout => window.setSchema({ name: 'invalid', title: 'Invalid', layout,
        sections: [{...window.form, config:{...window.form.config, api_endpoint:'/forbidden-read'}}] }), layout);
      await expect(page.getByRole('alert')).toContainText('Unsupported layout');
      await expect(page.getByLabel('Inspection notes')).toHaveCount(0);
    }
    assert.equal(requests.includes('/forbidden-read'), false);
  });

  await t.test('long labels remain operable with enlarged text and contrast preferences', async () => {
    await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'active' });
    await page.evaluate(() => {
      document.documentElement.style.fontSize = '32px';
      window.setSchema({ name: 'long-content', title: 'Fallback title', layout: 'full-width', sections: [
        window.form,
        { id: 'header', primitive: 'PageHeader', config: {
          title: 'CollectionOfUninterruptedScientificFieldObservations',
          actions: [{ id: 'review', label: 'Review the complete collection of field observations', variant: 'outline' },
            { id: 'pending', label: 'Awaiting review', disabled: true }],
        } },
      ] });
    });
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const button = page.getByRole('button', { name: 'Review the complete collection of field observations' });
    await button.focus();
    await expect(button).toBeFocused();
    await expect(page.getByRole('button', { name: 'Awaiting review' })).toBeDisabled();
    const box = await button.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 320);
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'page-enlarged-contrast.png'), fullPage: true });
    await page.evaluate(() => document.documentElement.style.fontSize = '');
    await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'none' });
  });
  assert.deepEqual(errors, []);
});
