import assert from 'node:assert/strict';
import test from 'node:test';
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
