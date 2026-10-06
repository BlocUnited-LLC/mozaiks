import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildAndroid, buildEnvironment, configureAndroid, filesUnder, prepareNativeWorkspace, readDelivery, verifyBundledAssets } from '../../factory_app/workflows/AppGenerator/tools/android_delivery_build.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const digest = `sha256:${'a'.repeat(64)}`;
const community = fileURLToPath(new URL('../../examples/canonical-apps/community/', import.meta.url));
const xml = '<manifest><application android:usesCleartextTraffic="false"><activity android:name=".MainActivity"><intent-filter><action android:name="android.intent.action.MAIN" /></intent-filter></activity></application></manifest>';

function fixture(t, { realApp = false } = {}) {
  const temporary = realpathSync(os.tmpdir());
  const owned = realpathSync(mkdtempSync(path.join(temporary, 'mozaiks-android-build-')));
  t.after(() => {
    const resolved = realpathSync(owned);
    assert.equal(resolved, owned);
    assert.equal(path.dirname(resolved), temporary);
    assert.ok(path.basename(resolved).startsWith('mozaiks-android-build-'));
    rmSync(resolved, { recursive: true, force: true });
  });
  const workspace = path.join(owned, 'workspace with spaces');
  const root = path.join(workspace, 'mobile');
  const write = (relative, value) => {
    const target = path.join(root, relative);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, typeof value === 'object' ? JSON.stringify(value) : value);
  };
  const read = relative => readFileSync(path.join(root, relative), 'utf8');
  if (realApp) cpSync(path.join(community, 'app'), path.join(workspace, 'app'), { recursive: true });
  else write('../app/ui/index.js', "export const pageRegistry = {};\nexport { createAuthAdapter } from './auth/authAdapter.js';\n");
  write('../app/ui/auth/capacitor/index.js', 'export const createAuthAdapter = () => {};');
  write('../workflows/reference.txt', 'workspace workflow');
  write('.local/framework/web_shell/package.json', '{}');
  write('.local/framework/chat-ui/package.json', '{}');
  const spec = { schema_version: 'mozaiks.android_delivery.v1', package_id: 'org.example.trailnotes', display_name: 'Trail Notes', version_name: '2.3.4', version_code: 23, backend_origin: 'https://backend.example.invalid', build_type: 'debug' };
  const config = { appId: spec.package_id, appName: spec.display_name, webDir: '.local/web' };
  write('capacitor.config.json', config);
  const inventory = (base, paths) => paths.map(relative => {
    const raw = readFileSync(path.join(base, relative));
    return { path: relative, sha256: sha(raw), size_bytes: raw.length };
  });
  const manifest = { schema_version: 'mozaiks.android_delivery_manifest.v1', spec, callback_uri: `${spec.package_id}:/auth/callback`, browser_auth: { mode: 'canonical_facade' }, source_digest: digest, spec_digest: digest, pack: { id: 'mobile', version: '0.1.0', digest }, framework: { commit: 'b'.repeat(40), resource_digest: digest, files: inventory(path.join(root, '.local/framework'), ['web_shell/package.json', 'chat-ui/package.json']) }, files: [] };
  const save = () => {
    manifest.files = inventory(workspace, [...filesUnder(path.join(workspace, 'app'), 'app'), ...filesUnder(path.join(workspace, 'workflows'), 'workflows'), 'mobile/capacitor.config.json']);
    write('delivery.manifest.json', manifest);
  };
  save();
  const generated = () => {
    write('android/app/src/main/AndroidManifest.xml', xml);
    write('android/app/build.gradle', `android { defaultConfig { applicationId "${spec.package_id}"\nversionCode 1\nversionName "1.0" } }`);
    write('android/app/src/main/assets/capacitor.config.json', config);
    for (const [name, content] of [['index.html', '<html>Trail Notes</html>'], ['assets/app.js', 'export const ready = true;']]) {
      write(`.local/web/${name}`, content);
      write(`android/app/src/main/assets/public/${name}`, content);
    }
  };
  return { root, workspace, write, read, manifest, config, save, generated };
}

test('stages real Common Ground bytes and preserves its browser barrel and workflow source', t => {
  const f = fixture(t, { realApp: true });
  const original = f.read('../app/ui/index.js');
  const result = prepareNativeWorkspace(f.root);
  assert.equal(f.read('.local/workspace/app/ui/browser-index.js'), original);
  assert.equal(f.read('.local/workspace/app/ui/index.js'), "export * from './browser-index.js';\nexport { createAuthAdapter } from './auth/capacitor/index.js';\n");
  assert.equal(f.read('../app/ui/index.js'), original);
  assert.equal(f.read('.local/workspace/workflows/reference.txt'), f.read('../workflows/reference.txt'));
  assert.equal(result.callbackUri, 'org.example.trailnotes:/auth/callback');
  assert.throws(() => prepareNativeWorkspace(f.root), /already exists/);
});

test('native explicit auth export overrides a browser facade without hiding other exports', async t => {
  const f = fixture(t);
  f.write('../app/ui/auth/authAdapter.js', 'export const createAuthAdapter = () => "browser";');
  f.write('../app/package.json', '{"type":"module"}');
  f.save();
  const result = prepareNativeWorkspace(f.root);
  const module = await import(pathToFileURL(path.join(result.app, 'ui/index.js')));
  assert.deepEqual(module.pageRegistry, {});
  assert.equal(module.createAuthAdapter(), undefined);
});

test('additional or changed app bytes fail before staging', t => {
  const f = fixture(t);
  f.write('../app/ui/extra.js', 'export const surprise = true;');
  assert.throws(() => readDelivery(f.root), /inventory differs/);
  f.save();
  f.write('../app/ui/index.js', 'changed');
  assert.throws(() => readDelivery(f.root), /integrity check failed/);
  assert.equal(existsSync(path.join(f.root, '.local/workspace')), false);
});

test('Android callback uses scheme only, binds actual Gradle identity/version, and repeats idempotently', t => {
  const f = fixture(t); f.generated();
  configureAndroid(f.root);
  const first = f.read('android/app/src/main/AndroidManifest.xml');
  assert.match(first, /<data android:scheme="org.example.trailnotes" \/>/);
  assert.doesNotMatch(first, /android:(host|path|pathPrefix|pathPattern)=/);
  assert.equal(new URL(f.manifest.callback_uri).pathname, '/auth/callback');
  assert.match(f.read('android/app/build.gradle'), /versionCode 23\nversionName "2.3.4"/);
  configureAndroid(f.root);
  assert.equal(f.read('android/app/src/main/AndroidManifest.xml'), first);
  assert.equal((first.match(/mozaiks-native-auth/g) || []).length, 1);
});

test('loopback configuration stays in debug and rejects remote-page loading', t => {
  const f = fixture(t); f.generated();
  const main = f.read('android/app/src/main/assets/capacitor.config.json');
  configureAndroid(f.root, { acceptance: true });
  configureAndroid(f.root, { acceptance: true });
  assert.equal(f.read('android/app/src/main/assets/capacitor.config.json'), main);
  assert.equal(JSON.parse(f.read('android/app/src/debug/assets/capacitor.config.json')).android.allowMixedContent, true);
  assert.match(f.read('android/app/src/debug/AndroidManifest.xml'), /usesCleartextTraffic="true"/);
  f.write('android/app/src/main/assets/capacitor.config.json', { ...f.config, server: { url: 'https://elsewhere.invalid' } });
  assert.throws(() => configureAndroid(f.root, { acceptance: true }), /requires bundled assets/);
});

for (const malformed of ['<manifest/>', '<activity>', '<activity></activity><activity></activity>', '<!-- mozaiks-native-auth --><manifest/>']) {
  test(`rejects malformed Android activity: ${malformed}`, t => {
    const f = fixture(t); f.generated();
    f.write('android/app/src/main/AndroidManifest.xml', malformed);
    assert.throws(() => configureAndroid(f.root), /Expected one/);
    assert.equal(f.read('android/app/src/main/AndroidManifest.xml'), malformed);
  });
}

test('wrong generated Android identity fails despite a correct copied Capacitor config', t => {
  const f = fixture(t); f.generated();
  f.write('android/app/build.gradle', 'applicationId "org.example.other"\nversionCode 1\nversionName "1.0"');
  assert.throws(() => configureAndroid(f.root), /package identity differs/);
});

test('bundled asset verification fails for altered or missing files and remote loading', t => {
  const f = fixture(t); f.generated();
  const assets = verifyBundledAssets(f.root, f.manifest);
  assert.equal(assets.length, 2);
  assert.equal(assets[0].sha256, sha('export const ready = true;'));
  f.write('android/app/src/main/assets/public/assets/app.js', 'changed');
  assert.throws(() => verifyBundledAssets(f.root, f.manifest), /asset differs/);
  rmSync(path.join(f.root, 'android/app/src/main/assets/public/assets/app.js'));
  assert.throws(() => verifyBundledAssets(f.root, f.manifest), /ENOENT/);
  f.write('android/app/src/main/assets/capacitor.config.json', { ...f.config, server: { url: 'https://elsewhere.invalid' } });
  assert.throws(() => verifyBundledAssets(f.root, f.manifest), /configuration differs/);
});

test('build environment ignores inherited credentials and Vite overrides; acceptance requires explicit loopback', t => {
  const f = fixture(t);
  const env = { PATH: 'tools', VITE_API_URL: 'https://attacker.invalid', VITE_CLIENT_SECRET: 'secret', AZURE_TOKEN: 'secret', VITE_OIDC_REDIRECT_URI: 'org.other:/callback' };
  const actual = buildEnvironment(f.root, f.manifest, { env });
  assert.equal(actual.PATH, 'tools');
  assert.equal(actual.AZURE_TOKEN, undefined);
  assert.equal(actual.VITE_CLIENT_SECRET, undefined);
  assert.equal(actual.VITE_API_URL, f.manifest.spec.backend_origin);
  assert.equal(actual.VITE_OIDC_REDIRECT_URI, f.manifest.callback_uri);
  assert.throws(() => buildEnvironment(f.root, f.manifest, { env, acceptance: true }), /loopback/);
  assert.equal(buildEnvironment(f.root, f.manifest, { env: { VITE_API_URL: 'http://127.0.0.1:18443' }, acceptance: true }).VITE_WS_URL, 'ws://127.0.0.1:18443');
});

test('subprocess failure replaces stale success and records no credentials', t => {
  const f = fixture(t);
  f.write('build-result.json', { status: 'succeeded' });
  assert.throws(() => buildAndroid(f.root, { run() { throw new Error('secret token text'); } }), /secret token/);
  const receipt = JSON.parse(f.read('build-result.json'));
  assert.equal(receipt.status, 'failed');
  assert.equal(receipt.stage, 'verify');
  assert.doesNotMatch(f.read('build-result.json'), /secret token/);
});

test('framework drift or additions fail before any subprocess', t => {
  for (const kind of ['changed', 'additional']) {
    const f = fixture(t);
    f.write(`.local/framework/web_shell/${kind === 'changed' ? 'package.json' : 'rogue.js'}`, 'changed');
    let calls = 0;
    assert.throws(() => buildAndroid(f.root, { run() { calls++; } }), /integrity|inventory/);
    assert.equal(calls, 0);
    assert.equal(JSON.parse(f.read('build-result.json')).status, 'failed');
  }
});

for (const extra of ['.npmrc', 'android/app/build.gradle', '.local/workspace/app/ui/index.js', '.local/framework/.env']) {
  test(`rejects undeclared build influence before any subprocess: ${extra}`, t => {
    const f = fixture(t);
    f.write(extra, 'unlisted input');
    let calls = 0;
    assert.throws(() => buildAndroid(f.root, { run() { calls++; } }), /Unexpected mobile|inventory differs/);
    assert.equal(calls, 0);
    assert.equal(JSON.parse(f.read('build-result.json')).status, 'failed');
  });
}

test('successful orchestration uses argument arrays and binds an actual APK hash to manifest provenance', t => {
  const f = fixture(t);
  const calls = [];
  const result = buildAndroid(f.root, { run(command, args, options) {
    calls.push({ command, args, options });
    if (args.includes('android') && args.includes('sync')) f.generated();
    if (args.includes('assembleDebug')) f.write('android/app/build/outputs/apk/debug/app-debug.apk', 'fake test-only APK bytes');
  } });
  assert.equal(result.status, 'succeeded');
  assert.equal(result.device_acceptance, 'not_run');
  assert.equal(result.source_digest, f.manifest.source_digest);
  assert.equal(result.pack_digest, f.manifest.pack.digest);
  assert.equal(result.framework_commit, f.manifest.framework.commit);
  assert.equal(result.apk.sha256, sha('fake test-only APK bytes'));
  assert.equal(result.apk.size_bytes, 24);
  assert.equal(calls.length, 8);
  assert.ok(calls.every(call => Array.isArray(call.args) && call.options.cwd.includes('workspace with spaces')));
  assert.ok(calls.some(call => call.args.includes(path.join(f.root, '.local/web'))));
  assert.deepEqual(JSON.parse(f.read('build-result.json')), result);
});
