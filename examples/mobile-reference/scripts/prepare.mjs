import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const referenceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const callbackUri = 'org.mozaiks.examples.commonground:/auth/callback';

export function prepareWorkspace(root = referenceRoot) {
  const source = path.resolve(root, '../canonical-apps/community');
  const workspace = path.join(root, '.local/workspace');
  if (existsSync(workspace)) throw new Error('Native workspace already exists; use a fresh disposable checkout.');
  mkdirSync(workspace, { recursive: true });
  cpSync(path.join(source, 'app'), path.join(workspace, 'app'), { recursive: true });
  if (existsSync(path.join(source, 'workflows'))) {
    cpSync(path.join(source, 'workflows'), path.join(workspace, 'workflows'), { recursive: true });
  }
  const barrel = path.join(workspace, 'app/ui/index.js');
  const nativeImport = path.relative(path.dirname(barrel), path.join(root, 'native/index.js')).replaceAll('\\', '/');
  writeFileSync(barrel, `${readFileSync(barrel, 'utf8')}\nexport { createAuthAdapter } from ${JSON.stringify(nativeImport)};\n`);
  return { workspace, app: path.join(workspace, 'app'), callbackUri };
}

export function prepareAndroid(root = referenceRoot, { acceptance = false } = {}) {
  const manifestPath = path.join(root, 'android/app/src/main/AndroidManifest.xml');
  const marker = '<!-- mozaiks-native-auth -->';
  const manifest = readFileSync(manifestPath, 'utf8');
  if ((manifest.match(/<\/activity>/g) || []).length !== 1) throw new Error('Expected the generated single-activity reference.');
  if (!manifest.includes(marker)) {
    const filter = `${marker}
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="org.mozaiks.examples.commonground" />
            </intent-filter>
        `;
    writeFileSync(manifestPath, manifest.replace('</activity>', `${filter}</activity>`));
  }
  if (acceptance) {
    // Disposable debug source-set overrides keep loopback HTTP out of release builds.
    const debugRoot = path.join(root, 'android/app/src/debug');
    mkdirSync(path.join(debugRoot, 'assets'), { recursive: true });
    const config = JSON.parse(readFileSync(path.join(root, 'android/app/src/main/assets/capacitor.config.json'), 'utf8'));
    if (config.server?.url) throw new Error('Native acceptance requires bundled assets');
    config.android = { ...config.android, allowMixedContent: true };
    writeFileSync(path.join(debugRoot, 'assets/capacitor.config.json'), `${JSON.stringify(config, null, 2)}\n`);
    writeFileSync(path.join(debugRoot, 'AndroidManifest.xml'), '<manifest xmlns:android="http://schemas.android.com/apk/res/android" xmlns:tools="http://schemas.android.com/tools">\n  <application android:usesCleartextTraffic="true" tools:replace="android:usesCleartextTraffic" />\n</manifest>\n');
  }
  return { callbackUri, acceptance, manifestPath };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [stage, option] = process.argv.slice(2);
  if (stage === 'workspace' && !option) console.log(JSON.stringify(prepareWorkspace()));
  else if (stage === 'android' && (!option || option === '--acceptance')) {
    console.log(JSON.stringify(prepareAndroid(referenceRoot, { acceptance: option === '--acceptance' })));
  } else throw new Error('Usage: node scripts/prepare.mjs workspace | android [--acceptance]');
}
