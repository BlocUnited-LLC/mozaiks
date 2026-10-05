import assert from 'node:assert/strict';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { prepareAndroid, prepareWorkspace } from './prepare.mjs';

const callbackUri = 'org.mozaiks.examples.commonground:/auth/callback';
const community = fileURLToPath(new URL('../../canonical-apps/community/', import.meta.url));
const manifest = `<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:usesCleartextTraffic="false">
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
  </application>
</manifest>
`;

function fixture(t) {
  const temporary = realpathSync(os.tmpdir());
  const owned = realpathSync(mkdtempSync(path.join(temporary, 'mozaiks-native-prepare-')));
  t.after(() => {
    const resolved = realpathSync(owned);
    assert.equal(resolved, owned);
    assert.equal(path.dirname(resolved), temporary);
    assert.ok(path.basename(resolved).startsWith('mozaiks-native-prepare-'));
    rmSync(resolved, { recursive: true, force: true });
  });
  const root = path.join(owned, 'examples/mobile-reference');
  const source = path.join(owned, 'examples/canonical-apps/community');
  const write = (relative, value) => {
    const target = path.join(root, relative);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, value);
  };
  const read = relative => readFileSync(path.join(root, relative), 'utf8');
  write('native/index.js', 'export function createAuthAdapter() {}\n');
  write('android/app/src/main/AndroidManifest.xml', manifest);
  const config = {
    appId: 'org.mozaiks.examples.commonground', appName: 'Common Ground',
    webDir: '../../web_shell/dist', server: { androidScheme: 'https' },
    android: { backgroundColor: '#ffffff' },
  };
  write('android/app/src/main/assets/capacitor.config.json', JSON.stringify(config, null, 2));
  return { root, source, write, read, config };
}

test('stages the real Common Ground app and preserves registration and original source', t => {
  const f = fixture(t);
  cpSync(path.join(community, 'app'), path.join(f.source, 'app'), { recursive: true });
  mkdirSync(path.join(f.source, 'workflows'), { recursive: true });
  writeFileSync(path.join(f.source, 'workflows/reference.txt'), 'workspace workflow input');
  const originalBarrel = readFileSync(path.join(f.source, 'app/ui/index.js'), 'utf8');
  const originalManifest = readFileSync(path.join(f.source, 'app/app.json'), 'utf8');
  const originalPage = readFileSync(path.join(f.source, 'app/ui/pages/custom/CommunityFeedPage.jsx'), 'utf8');
  const result = prepareWorkspace(f.root);
  assert.equal(result.workspace, path.join(f.root, '.local/workspace'));
  assert.equal(result.app, path.join(result.workspace, 'app'));
  assert.equal(result.callbackUri, callbackUri);
  assert.equal(readFileSync(path.join(result.app, 'app.json'), 'utf8'), originalManifest);
  assert.equal(readFileSync(path.join(result.app, 'ui/pages/custom/CommunityFeedPage.jsx'), 'utf8'), originalPage);
  const stagedBarrel = readFileSync(path.join(result.app, 'ui/index.js'), 'utf8');
  assert.ok(stagedBarrel.startsWith(originalBarrel));
  const declaration = stagedBarrel.slice(originalBarrel.length).match(/export \{ createAuthAdapter \} from ("[^"]+");/);
  assert.ok(declaration);
  assert.equal(path.resolve(result.app, 'ui', JSON.parse(declaration[1])), path.join(f.root, 'native/index.js'));
  assert.equal(readFileSync(path.join(result.workspace, 'workflows/reference.txt'), 'utf8'), 'workspace workflow input');
  assert.equal(readFileSync(path.join(f.source, 'app/ui/index.js'), 'utf8'), originalBarrel);
  assert.equal(readFileSync(path.join(f.source, 'app/app.json'), 'utf8'), originalManifest);
  assert.throws(() => prepareWorkspace(f.root), /already exists/);
  assert.equal(readFileSync(path.join(result.app, 'ui/index.js'), 'utf8'), stagedBarrel);
});

test('a workspace without local workflows stages without inventing workflows', t => {
  const f = fixture(t);
  cpSync(path.join(community, 'app'), path.join(f.source, 'app'), { recursive: true });
  const result = prepareWorkspace(f.root);
  assert.equal(existsSync(path.join(result.workspace, 'workflows')), false);
});

test('Android filter registers only the callback scheme and is idempotent', t => {
  const f = fixture(t);
  const result = prepareAndroid(f.root);
  assert.equal(result.callbackUri, callbackUri);
  assert.equal(new URL(result.callbackUri).hostname, '');
  assert.equal(new URL(result.callbackUri).pathname, '/auth/callback');
  const prepared = f.read('android/app/src/main/AndroidManifest.xml');
  const filter = prepared.match(/<!-- mozaiks-native-auth -->\s*(<intent-filter>[\s\S]*?<\/intent-filter>)/)?.[1];
  assert.ok(filter);
  assert.match(filter, /android\.intent\.action\.VIEW/);
  assert.match(filter, /android\.intent\.category\.DEFAULT/);
  assert.match(filter, /android\.intent\.category\.BROWSABLE/);
  assert.match(filter, /<data android:scheme="org\.mozaiks\.examples\.commonground"\s*\/>/);
  assert.doesNotMatch(filter, /android:(host|path|pathPrefix|pathPattern)=/);
  assert.equal((prepared.match(/<!-- mozaiks-native-auth -->/g) || []).length, 1);
  assert.match(prepared, /android\.intent\.action\.MAIN/);
  assert.equal(existsSync(path.join(f.root, 'android/app/src/debug')), false);
  prepareAndroid(f.root);
  assert.equal(f.read('android/app/src/main/AndroidManifest.xml'), prepared);
});

test('HTTP acceptance overrides are confined to debug and repeat without modifying main configuration', t => {
  const f = fixture(t);
  const mainConfig = f.read('android/app/src/main/assets/capacitor.config.json');
  const result = prepareAndroid(f.root, { acceptance: true });
  assert.equal(result.acceptance, true);
  assert.equal(f.read('android/app/src/main/assets/capacitor.config.json'), mainConfig);
  const debugConfig = f.read('android/app/src/debug/assets/capacitor.config.json');
  assert.deepEqual(JSON.parse(debugConfig), {
    ...f.config, android: { ...f.config.android, allowMixedContent: true },
  });
  const debugManifest = f.read('android/app/src/debug/AndroidManifest.xml');
  assert.match(debugManifest, /android:usesCleartextTraffic="true"/);
  assert.match(debugManifest, /tools:replace="android:usesCleartextTraffic"/);
  assert.doesNotMatch(f.read('android/app/src/main/AndroidManifest.xml'), /android:usesCleartextTraffic="true"/);
  prepareAndroid(f.root, { acceptance: true });
  assert.equal(f.read('android/app/src/debug/assets/capacitor.config.json'), debugConfig);
  assert.equal(f.read('android/app/src/debug/AndroidManifest.xml'), debugManifest);
  assert.equal(f.read('android/app/src/main/assets/capacitor.config.json'), mainConfig);
});

test('acceptance rejects remote-page loading without emitting debug configuration', t => {
  const f = fixture(t);
  const remoteConfig = JSON.stringify({ ...f.config, server: { url: 'https://external.example' } });
  f.write('android/app/src/main/assets/capacitor.config.json', remoteConfig);
  assert.throws(() => prepareAndroid(f.root, { acceptance: true }), /requires bundled assets/);
  assert.equal(f.read('android/app/src/main/assets/capacitor.config.json'), remoteConfig);
  assert.equal(existsSync(path.join(f.root, 'android/app/src/debug/assets/capacitor.config.json')), false);
});

for (const [label, value] of [
  ['missing activity', '<manifest><application /></manifest>'],
  ['unclosed activity', '<manifest><application><activity></application></manifest>'],
  ['multiple activities', '<manifest><application><activity></activity><activity></activity></application></manifest>'],
  ['marker in malformed manifest', '<manifest><!-- mozaiks-native-auth --><application /></manifest>'],
]) {
  test(`rejects ${label} without replacing the manifest`, t => {
    const f = fixture(t);
    f.write('android/app/src/main/AndroidManifest.xml', value);
    assert.throws(() => prepareAndroid(f.root), /single-activity/);
    assert.equal(f.read('android/app/src/main/AndroidManifest.xml'), value);
  });
}
