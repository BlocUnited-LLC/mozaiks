import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import validateAllConfigs from '../../chat-ui/src/config/validateConfig.js';
import { clearThemeCache, getTheme } from '../../chat-ui/src/styles/themeProvider.js';

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
