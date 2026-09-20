import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import validateAllConfigs from '../../chat-ui/src/config/validateConfig.js';

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
