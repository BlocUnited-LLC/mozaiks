import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import validateAllConfigs from '../../chat-ui/src/config/validateConfig.js';
import { applyTheme, clearThemeCache, getCachedTheme, getTheme, getThemeMetadata } from '../../chat-ui/src/styles/themeProvider.js';
import { PAGE_TITLE_DEFAULTS } from '../../chat-ui/src/ui/theme/pageTitle.js';

test('concurrent theme consumers share the pending load and cached result', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  const requests = [];
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  globalThis.fetch = async url => {
    requests.push(url);
    if (url === '/api/theme-config') await pending;
    return { ok: true, json: async () => ({ identity: { name: 'Bakery' } }) };
  };
  const first = getTheme('bakery');
  const second = getTheme('bakery');
  assert.equal(getCachedTheme('bakery'), null);
  assert.deepEqual(requests, ['/api/theme-config']);
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(a, b);
  assert.equal(await getTheme('bakery'), a);
  assert.equal(getCachedTheme('bakery'), a);
  assert.equal(getThemeMetadata('bakery').source, 'config');
  assert.deepEqual(requests, ['/api/theme-config', '/api/themes/bakery']);
});

test('an invalidated pending theme cannot replace a newer cache entry', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  let releaseOld;
  let requests = 0;
  globalThis.fetch = async url => {
    if (url !== '/api/theme-config') return { ok: false };
    if (++requests === 1) {
      await new Promise(resolve => { releaseOld = resolve; });
      return { ok: true, json: async () => ({ identity: { name: 'Old' } }) };
    }
    return { ok: true, json: async () => ({ identity: { name: 'Current' } }) };
  };
  const old = getTheme('bakery');
  clearThemeCache('bakery');
  const current = await getTheme('bakery');
  releaseOld();
  assert.equal((await old).branding.name, 'Old');
  assert.equal(getCachedTheme('bakery'), current);
  assert.equal((await getTheme('bakery')).branding.name, 'Current');
});

test('a stalled base-theme request settles through the existing fallback', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requestSignal;
  globalThis.fetch = async (url, options) => {
    if (url !== '/api/theme-config') return { ok: false };
    requestSignal = options.signal;
    return new Promise((_, reject) => options.signal.addEventListener('abort', () => {
      reject(new Error('request aborted'));
    }));
  };
  const pending = getTheme('unavailable');
  t.mock.timers.tick(3999);
  assert.equal(requestSignal.aborted, false);
  t.mock.timers.tick(1);
  assert.equal((await pending).branding.name, 'App');
  assert.equal(requestSignal.aborted, true);
  assert.equal(getThemeMetadata('unavailable').source, 'fallback');
});

test('only custom overrides for the active app replace declared brand values', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  const brand = {
    identity: { name: 'Bakery' }, assets: {},
    theme: { font: 'system', primary: 'orange' },
    fonts: { body: { family: 'Bakery Local', localFont: true, src: '/fonts/bakery.ttf' } },
    colors: { primary: { main: '#e85d04' } },
  };
  const overlay = { fonts: { body: { family: 'Owner Custom' } }, colors: { primary: { main: '#047857' } } };
  for (const [response, expectedFont, expectedColor] of [
    [brand, 'Bakery Local', '#e85d04'],
    [{ app_id: 'bakery', source: 'default', theme: overlay }, 'Bakery Local', '#e85d04'],
    [{ app_id: 'another-app', source: 'custom', theme: overlay }, 'Bakery Local', '#e85d04'],
    [{ app_id: 'bakery', source: 'custom', theme: overlay }, 'Owner Custom', '#047857'],
  ]) {
    clearThemeCache();
    globalThis.fetch = async (url) => ({
      ok: true,
      json: async () => String(url).endsWith('/api/theme-config') ? brand : response,
    });
    const resolved = await getTheme('bakery');
    assert.equal(resolved.fonts.body.family, expectedFont);
    assert.equal(resolved.colors.primary.main, expectedColor);
  }
});

test('text-only branding is valid while malformed logo references still fail', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; });
  let logo = null;
  globalThis.fetch = async (url) => ({
    ok: true,
    json: async () => String(url).endsWith('/api/theme-config') ? {
      identity: { name: 'Records' }, assets: { logo },
      colors: { primary: { main: '#047857' }, secondary: { main: '#202124' } },
      fonts: { body: { family: 'system-ui' }, heading: { family: 'system-ui' } },
    } : {},
  });
  assert.deepEqual((await validateAllConfigs()).filter(issue => issue.message.includes('assets.logo')), []);
  logo = 'not-a-file';
  const issues = (await validateAllConfigs()).filter(issue => issue.message.includes('assets.logo'));
  assert.equal(issues.length, 1);
  assert.equal(issues[0].level, 'error');
});

test('partial brand tokens inherit the app appearance and declared font presets', async (t) => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  for (const [appearance, background, foreground] of [
    ['light', '#f4f8fc', '#08111f'], ['dark', '#0b1220', '#e6eef8'],
  ]) {
    clearThemeCache();
    const brand = {
      identity: { name: 'Reports' }, assets: {},
      theme: { appearance, primary: 'teal', font: 'inter', font_heading: 'oxanium' },
      fonts: { body: { family: 'Georgia', fallbacks: 'serif' }, logo: { family: 'Georgia' } },
      colors: { primary: { main: '#0f766e' } },
    };
    globalThis.fetch = async () => ({ ok: true, json: async () => brand });
    const resolved = await getTheme('reports');
    assert.equal(resolved.colors.background.base, background);
    assert.equal(resolved.colors.text.primary, foreground);
    assert.equal(resolved.colors.primary.main, '#0f766e');
    assert.deepEqual(resolved.fonts.body, { family: 'Georgia', fallbacks: 'serif' });
    assert.deepEqual(resolved.fonts.logo, { family: 'Georgia' });
    assert.equal(resolved.fonts.heading.family, 'Oxanium');
  }
});

test('shared brand fallbacks are bundled and missing backgrounds remain optional', () => {
  const bundle = buildSync({
    entryPoints: [fileURLToPath(new URL('../../chat-ui/src/styles/brandAssets.js', import.meta.url))],
    bundle: true, platform: 'node', format: 'cjs', loader: { '.png': 'dataurl' }, write: false,
  });
  const module = { exports: {} };
  new Function('module', 'exports', bundle.outputFiles[0].text)(module, module.exports);
  const { getBrandLogoSrc, getBrandLoadingIconSrc, getChatBackgroundSrc, applyBrandImageFallback } = module.exports;
  assert.match(getBrandLogoSrc({}), /^data:image\/png;base64,/);
  assert.equal(getBrandLoadingIconSrc({}), getBrandLogoSrc({}));
  assert.equal(getChatBackgroundSrc({}), null);
  assert.equal(getBrandLogoSrc({ branding: { logo: '/assets/custom.png' } }), '/assets/custom.png');
  assert.equal(getChatBackgroundSrc({ branding: { chatbackgroundImage: '/assets/custom-bg.png' } }), '/assets/custom-bg.png');
  const target = { src: '/assets/missing.png' };
  applyBrandImageFallback({ currentTarget: target });
  assert.equal(target.src, getBrandLogoSrc({}));
});

test('page title roles survive loading and reset when switching to an app without overrides', async t => {
  const previousFetch = globalThis.fetch;
  const previousDocument = globalThis.document;
  t.after(() => { globalThis.fetch = previousFetch; globalThis.document = previousDocument; clearThemeCache(); });
  const variables = new Map();
  globalThis.document = {
    documentElement: { style: { setProperty: (key, value) => variables.set(key, value), removeProperty: key => variables.delete(key) } },
    getElementById: () => ({}),
  };
  let brand = {
    identity: { name: 'Editorial' }, fonts: { body: { family: 'Georgia' }, heading: { family: 'Arial' } },
    ui: { page: { title: { font: 'body', weight: 'regular', size: 'xl', lineHeight: 'relaxed', letterSpacing: 'wide', textTransform: 'uppercase' } } },
  };
  globalThis.fetch = async () => ({ ok: true, json: async () => brand });
  const editorial = await getTheme('editorial');
  assert.deepEqual(editorial.ui.page.title, brand.ui.page.title);
  applyTheme(editorial);
  assert.equal(variables.get('--shell-page-title-font-family'), 'var(--font-body)');
  assert.equal(variables.get('--font-body'), 'Georgia, ui-sans-serif, system-ui, sans-serif');
  assert.equal(variables.get('--shell-page-title-font-weight'), '400');
  assert.equal(variables.get('--shell-page-title-font-size'), '3rem');
  assert.equal(variables.get('--shell-page-title-line-height'), '1.5');
  assert.equal(variables.get('--shell-page-title-letter-spacing'), '0.05em');
  assert.equal(variables.get('--shell-page-title-text-transform'), 'uppercase');
  brand = { identity: { name: 'Operations' } };
  const operations = await getTheme('operations');
  assert.deepEqual(operations.ui.page.title, PAGE_TITLE_DEFAULTS);
  applyTheme(operations);
  assert.equal(variables.get('--shell-page-title-font-family'), 'var(--font-heading)');
  assert.equal(variables.get('--shell-page-title-font-weight'), '600');
  assert.equal(variables.get('--shell-page-title-text-transform'), 'none');
  assert.equal(variables.get('--shell-page-title-letter-spacing'), 'normal');
});

test('invalid explicit title styles are diagnosed and never applied as fallback styles', async t => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  for (const title of [null, 'bold', { font: 'logo' }, { weight: '900' }, { size: '99px' }, { lineHeight: 1 }, { letterSpacing: '2px' }, { textTransform: 'random' }, { css: 'color:red' }]) {
    clearThemeCache();
    const brand = { identity: { name: 'Invalid' }, assets: {}, ui: { page: { title } } };
    globalThis.fetch = async () => ({ ok: true, json: async () => brand });
    const issues = await validateAllConfigs();
    assert.ok(issues.some(issue => issue.level === 'error' && issue.message.includes('ui.page.title')));
    await assert.rejects(getTheme('invalid'), /ui.page.title/);
    assert.equal(getCachedTheme('invalid'), null);
    assert.throws(() => applyTheme(brand), /ui.page.title/);
  }
});

test('a rejected theme response preserves its diagnostic and is never cached as defaults', async t => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  const requests = [];
  globalThis.fetch = async url => {
    requests.push(url);
    return { ok: false, status: 422, json: async () => ({ detail: 'ui.page.title.size: unsupported token' }) };
  };
  await assert.rejects(getTheme('invalid-api'), /ui.page.title.size: unsupported token/);
  assert.equal(getCachedTheme('invalid-api'), null);
  assert.deepEqual(requests, ['/api/theme-config']);
});

test('compact platform changes preserve captured title choices and explicit title overrides are validated', async t => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; clearThemeCache(); });
  const title = { font: 'body', weight: 'regular', size: 'xl', textTransform: 'uppercase' };
  const brand = { identity: { name: 'Editorial' }, ui: { page: { title } } };
  let override = { primary: 'rose' };
  globalThis.fetch = async url => ({ ok: true, json: async () => url === '/api/theme-config'
    ? brand : { app_id: 'editorial', source: 'custom', theme: override } });
  const resolved = await getTheme('editorial');
  assert.deepEqual(resolved.ui.page.title, { ...PAGE_TITLE_DEFAULTS, ...title });
  clearThemeCache();
  override = { ui: { page: { title: { size: 'arbitrary-css' } } } };
  await assert.rejects(getTheme('editorial'), /ui.page.title.size/);
  assert.equal(getCachedTheme('editorial'), null);
});

test('PageHeader refuses the removed titleFont alias instead of silently following the title role', () => {
  const bundle = buildSync({
    entryPoints: [fileURLToPath(new URL('../../chat-ui/src/ui/primitives/PageHeader.jsx', import.meta.url))],
    bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false,
    nodePaths: [fileURLToPath(new URL('../node_modules', import.meta.url))],
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(module, module.exports, () => ({}));
  const { PageHeader } = module.exports;
  assert.throws(() => PageHeader({ title: 'Billing', titleFont: 'body' }), /no longer accepts titleFont/);
  assert.ok(PageHeader({ title: 'Billing', title_font: 'body' }));
  assert.ok(PageHeader({ title: 'Billing' }));
});
