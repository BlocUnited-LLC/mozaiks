import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { verifyAssets } from './verify-assets.mjs';

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'mozaiks-mobile-assets-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const write = (relative, value) => {
    const target = path.join(root, relative);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, value);
  };
  const config = { appId: 'org.example.reference', appName: 'Reference', webDir: 'www' };
  write('capacitor.config.json', JSON.stringify(config));
  write('android/app/src/main/assets/capacitor.config.json', JSON.stringify(config));
  write('node_modules/@capacitor/android/package.json', JSON.stringify({ version: '8.4.3' }));
  write('www/index.html', '<html><head><title>Reference</title></head><body>Reference</body></html>');
  write('www/assets/app.js', 'export const ready = true;');
  write('android/app/src/main/assets/public/index.html', '<html><head><title>Reference</title></head><body>Reference</body></html>');
  write('android/app/src/main/assets/public/assets/app.js', 'export const ready = true;');
  return { root, write, config };
}

test('records the exact packaged web files without claiming native acceptance', t => {
  const { root } = fixture(t);
  const result = verifyAssets(root);
  assert.equal(result.assets.length, 2);
  assert.match(result.assets[0].sha256, /^[a-f0-9]{64}$/);
  assert.ok(result.notVerified.includes('APK compilation'));
});

test('rejects changed packaged bytes', t => {
  const { root, write } = fixture(t);
  write('android/app/src/main/assets/public/assets/app.js', 'export const ready = false;');
  assert.throws(() => verifyAssets(root), /Packaged asset differs: assets\/app.js/);
});

test('rejects a missing packaged asset', t => {
  const { root } = fixture(t);
  rmSync(path.join(root, 'android/app/src/main/assets/public/assets/app.js'));
  assert.throws(() => verifyAssets(root), /ENOENT/);
});

test('rejects remote page loading even when the asset copy matches', t => {
  const { root, write, config } = fixture(t);
  write('android/app/src/main/assets/capacitor.config.json', JSON.stringify({
    ...config, server: { url: 'https://example.invalid' },
  }));
  assert.throws(() => verifyAssets(root), /must load bundled assets/);
});

test('rejects stale copied Capacitor configuration', t => {
  const { root, write, config } = fixture(t);
  write('android/app/src/main/assets/capacitor.config.json', JSON.stringify({
    ...config, appId: 'org.example.another',
  }));
  assert.throws(() => verifyAssets(root), /Copied Capacitor configuration differs/);
});

test('rejects an unrelated web build before asset verification', t => {
  const { root, write } = fixture(t);
  write('www/index.html', '<html><head><title>Studio</title></head></html>');
  assert.throws(() => verifyAssets(root), /web build title differs/);
});
