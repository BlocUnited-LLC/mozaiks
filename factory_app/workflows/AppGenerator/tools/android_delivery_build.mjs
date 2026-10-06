import { createHash } from 'node:crypto';
import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const readJson = filename => JSON.parse(readFileSync(filename, 'utf8'));
const writeJson = (filename, value) => writeFileSync(filename, `${JSON.stringify(value, null, 2)}\n`);
const fail = message => { throw new Error(message); };

function regularPath(root, relative, { directory = false } = {}) {
  if (!relative || relative.includes('\\') || relative.split('/').some(part => !part || part === '.' || part === '..') || path.isAbsolute(relative)) {
    fail('Invalid delivery file path');
  }
  let current = root;
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    if (lstatSync(current).isSymbolicLink()) fail('Delivery links are not supported');
  }
  const metadata = lstatSync(current);
  if (!(directory ? metadata.isDirectory() : metadata.isFile())) fail('Delivery path has an unexpected type');
  return current;
}

export function filesUnder(directory, prefix = '') {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) fail('Delivery links are not supported');
    if (entry.isDirectory()) return filesUnder(path.join(directory, entry.name), relative);
    if (!entry.isFile()) fail('Delivery contains a non-file');
    return [relative];
  }).sort();
}

function verifyFiles(root, entries) {
  if (!Array.isArray(entries) || !entries.length) fail('Delivery file inventory is missing');
  const seen = new Set();
  for (const entry of entries) {
    if (seen.has(entry.path) || !/^[a-f0-9]{64}$/.test(entry.sha256)) fail('Invalid delivery file inventory');
    seen.add(entry.path);
    const raw = readFileSync(regularPath(root, entry.path));
    if (raw.length !== entry.size_bytes || sha256(raw) !== entry.sha256) fail('Delivery file integrity check failed');
  }
}

function verifyInventory(root, entries, roots) {
  const expected = entries.map(entry => entry.path).filter(name => roots.some(prefix => name.startsWith(`${prefix}/`))).sort();
  const actual = roots.flatMap(name => existsSync(path.join(root, name)) ? filesUnder(regularPath(root, name, { directory: true }), name) : []).sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) fail('Delivery file inventory differs');
}

function verifyFreshMobileInputs(mobileRoot, manifest) {
  const allowed = new Set(['delivery.manifest.json', 'build-result.json', '.local']);
  for (const entry of manifest.files) {
    if (entry.path.startsWith('mobile/')) allowed.add(entry.path.slice('mobile/'.length));
  }
  for (const entry of readdirSync(mobileRoot, { withFileTypes: true })) {
    if (!allowed.has(entry.name) || entry.isSymbolicLink()) fail('Unexpected mobile build input; use a fresh delivery');
    if (entry.name !== '.local' && !entry.isFile()) fail('Unexpected mobile build input type');
  }
  const local = path.join(mobileRoot, '.local');
  if (existsSync(local)) {
    if (!lstatSync(local).isDirectory()) fail('Mobile build cache must be a directory');
    for (const entry of readdirSync(local, { withFileTypes: true })) {
      if (entry.name !== 'framework' || !entry.isDirectory() || entry.isSymbolicLink()) fail('Unexpected mobile build cache; use a fresh delivery');
    }
  }
}

export function readDelivery(mobileRoot) {
  const manifest = readJson(regularPath(mobileRoot, 'delivery.manifest.json'));
  const spec = manifest.spec;
  if (manifest.schema_version !== 'mozaiks.android_delivery_manifest.v1' || spec?.build_type !== 'debug'
      || !/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/.test(spec.package_id)
      || !/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(spec.version_name)
      || !Number.isSafeInteger(spec.version_code) || spec.version_code < 1 || spec.version_code > 2100000000
      || !manifest.callback_uri?.startsWith(`${spec.package_id}:/`) || manifest.callback_uri.startsWith(`${spec.package_id}://`)
      || !['shared_default', 'canonical_facade'].includes(manifest.browser_auth?.mode)
      || !/^[a-f0-9]{40}$/.test(manifest.framework?.commit)
      || [manifest.source_digest, manifest.spec_digest, manifest.pack?.digest, manifest.framework?.resource_digest].some(value => !/^sha256:[a-f0-9]{64}$/.test(value))) {
    fail('Invalid Android delivery manifest');
  }
  const backend = new URL(spec.backend_origin);
  if (backend.protocol !== 'https:' || backend.origin !== spec.backend_origin) fail('Android backend must be an HTTPS origin');
  const config = readJson(regularPath(mobileRoot, 'capacitor.config.json'));
  if (config.server?.url || config.appId !== spec.package_id || config.appName !== spec.display_name || config.webDir !== '.local/web') {
    fail('Capacitor configuration differs from the delivery');
  }
  verifyFiles(path.dirname(mobileRoot), manifest.files);
  verifyInventory(path.dirname(mobileRoot), manifest.files, ['app', 'workflows']);
  return manifest;
}

export function prepareNativeWorkspace(mobileRoot, manifest = readDelivery(mobileRoot)) {
  const source = path.dirname(mobileRoot);
  const workspace = path.join(mobileRoot, '.local/workspace');
  if (existsSync(workspace)) fail('Native workspace already exists; use a fresh delivery');
  for (const name of ['app', 'workflows']) {
    if (!existsSync(path.join(source, name))) continue;
    regularPath(source, name, { directory: true });
    filesUnder(path.join(source, name));
  }
  const sourceUi = path.join(source, 'app/ui');
  if (existsSync(path.join(sourceUi, 'browser-index.js'))) fail('The native browser barrel path is reserved');
  mkdirSync(workspace, { recursive: true });
  cpSync(path.join(source, 'app'), path.join(workspace, 'app'), { recursive: true });
  if (existsSync(path.join(source, 'workflows'))) cpSync(path.join(source, 'workflows'), path.join(workspace, 'workflows'), { recursive: true });
  const ui = path.join(workspace, 'app/ui');
  mkdirSync(ui, { recursive: true });
  const barrel = path.join(ui, 'index.js');
  let exports = '';
  if (existsSync(barrel)) {
    renameSync(barrel, path.join(ui, 'browser-index.js'));
    exports = "export * from './browser-index.js';\n";
  }
  writeFileSync(barrel, `${exports}export { createAuthAdapter } from './auth/capacitor/index.js';\n`);
  return { workspace, app: path.join(workspace, 'app'), callbackUri: manifest.callback_uri };
}

export function configureAndroid(mobileRoot, { acceptance = false, manifest = readDelivery(mobileRoot) } = {}) {
  const manifestPath = path.join(mobileRoot, 'android/app/src/main/AndroidManifest.xml');
  const marker = '<!-- mozaiks-native-auth -->';
  const xml = readFileSync(manifestPath, 'utf8');
  if ((xml.match(/<\/activity>/g) || []).length !== 1) fail('Expected one generated Android activity');
  const filter = `${marker}\n            <intent-filter>\n                <action android:name="android.intent.action.VIEW" />\n                <category android:name="android.intent.category.DEFAULT" />\n                <category android:name="android.intent.category.BROWSABLE" />\n                <data android:scheme="${manifest.spec.package_id}" />\n            </intent-filter>\n        `;
  if (xml.includes(marker) && !xml.includes(filter)) fail('Android callback filter differs from the delivery');
  if (!xml.includes(marker)) writeFileSync(manifestPath, xml.replace('</activity>', `${filter}</activity>`));
  const gradlePath = path.join(mobileRoot, 'android/app/build.gradle');
  let gradle = readFileSync(gradlePath, 'utf8');
  const ids = [...gradle.matchAll(/\bapplicationId\s+["']([^"']+)["']/g)];
  if (ids.length !== 1 || ids[0][1] !== manifest.spec.package_id) fail('Generated Android package identity differs');
  if ((gradle.match(/\bversionCode\s+\d+/g) || []).length !== 1 || (gradle.match(/\bversionName\s+["'][^"']+["']/g) || []).length !== 1) fail('Unexpected generated Android version declaration');
  gradle = gradle.replace(/\bversionCode\s+\d+/, `versionCode ${manifest.spec.version_code}`).replace(/\bversionName\s+["'][^"']+["']/, `versionName "${manifest.spec.version_name}"`);
  writeFileSync(gradlePath, gradle);
  const config = readJson(path.join(mobileRoot, 'android/app/src/main/assets/capacitor.config.json'));
  if (config.server?.url) fail('Android delivery requires bundled assets');
  if (acceptance) {
    const debug = path.join(mobileRoot, 'android/app/src/debug');
    mkdirSync(path.join(debug, 'assets'), { recursive: true });
    writeJson(path.join(debug, 'assets/capacitor.config.json'), { ...config, android: { ...config.android, allowMixedContent: true } });
    writeFileSync(path.join(debug, 'AndroidManifest.xml'), '<manifest xmlns:android="http://schemas.android.com/apk/res/android" xmlns:tools="http://schemas.android.com/tools">\n  <application android:usesCleartextTraffic="true" tools:replace="android:usesCleartextTraffic" />\n</manifest>\n');
  }
  return { callbackUri: manifest.callback_uri, package_id: ids[0][1], version_name: manifest.spec.version_name, version_code: manifest.spec.version_code };
}

export function verifyBundledAssets(mobileRoot, manifest) {
  const config = readJson(path.join(mobileRoot, 'android/app/src/main/assets/capacitor.config.json'));
  if (config.server?.url || config.appId !== manifest.spec.package_id || config.appName !== manifest.spec.display_name) fail('Copied Capacitor configuration differs');
  const sourceRoot = path.join(mobileRoot, '.local/web');
  const nativeRoot = path.join(mobileRoot, 'android/app/src/main/assets/public');
  const files = filesUnder(sourceRoot);
  if (!files.includes('index.html')) fail('Web build index is missing');
  return files.map(relative => {
    const raw = readFileSync(regularPath(sourceRoot, relative));
    const digest = sha256(raw);
    if (sha256(readFileSync(regularPath(nativeRoot, relative))) !== digest) fail('Bundled web asset differs');
    return { path: relative, sha256: digest, size_bytes: raw.length };
  });
}

export function buildEnvironment(mobileRoot, manifest, { acceptance = false, env = process.env } = {}) {
  // Build tools receive system paths and public build settings, never arbitrary Vite variables.
  const allowed = /^(PATH|Path|HOME|USERPROFILE|SYSTEMROOT|SystemRoot|COMSPEC|ComSpec|PATHEXT|TEMP|TMP|TMPDIR|JAVA_HOME|ANDROID_HOME|ANDROID_SDK_ROOT|LOCALAPPDATA|APPDATA|LANG|LC_ALL|CI)$/;
  const result = Object.fromEntries(Object.entries(env).filter(([key]) => allowed.test(key)));
  let backend = manifest.spec.backend_origin;
  if (acceptance) {
    const url = new URL(env.VITE_API_URL);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.origin !== env.VITE_API_URL) fail('Acceptance backend must be an explicit loopback HTTP origin');
    backend = url.origin;
    for (const key of ['VITE_OIDC_AUTHORITY', 'VITE_OIDC_DISCOVERY_URL', 'VITE_OIDC_CLIENT_ID', 'VITE_OIDC_SCOPE', 'VITE_AUTH_ENABLED']) {
      if (env[key] !== undefined) result[key] = env[key];
    }
  }
  return { ...result, PLATFORM_PATH: path.join(mobileRoot, '.local/workspace/app'), MOZAIKS_APP_WORKSPACE_PATH: path.join(mobileRoot, '.local/workspace'), MOZAIKS_CHAT_UI_PATH: path.join(mobileRoot, '.local/framework/chat-ui'), MOZAIKS_HOST: 'platform', VITE_MOZAIKS_HOST: 'platform', VITE_API_URL: backend, VITE_CORE_URL: backend, VITE_WS_URL: backend.replace(/^http/, 'ws'), VITE_OIDC_REDIRECT_URI: manifest.callback_uri };
}

function runCommand(command, args, options) {
  const result = spawnSync(command, args, { ...options, stdio: 'inherit', shell: false });
  if (result.error || result.status !== 0) fail('Android build subprocess failed');
}

function npmCommand(args) {
  const npmCli = path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js');
  if (existsSync(npmCli)) return [process.execPath, [npmCli, ...args]];
  if (process.platform === 'win32') fail('Node installation must include npm-cli.js');
  return ['npm', args];
}

export function buildAndroid(mobileRoot, { acceptance = false, env = process.env, run = runCommand } = {}) {
  mobileRoot = path.resolve(mobileRoot);
  const receiptPath = path.join(mobileRoot, 'build-result.json');
  if (existsSync(receiptPath) && !lstatSync(receiptPath).isFile()) fail('Build receipt must be a regular file');
  if (existsSync(receiptPath) && lstatSync(receiptPath).isSymbolicLink()) fail('Build receipt must not be a link');
  let receipt = { schema_version: 'mozaiks.android_build_result.v1', status: 'failed', stage: 'preflight', device_acceptance: 'not_run' };
  writeJson(receiptPath, receipt);
  try {
    const manifest = readDelivery(mobileRoot);
    verifyFreshMobileInputs(mobileRoot, manifest);
    receipt = { ...receipt, source_digest: manifest.source_digest, spec_digest: manifest.spec_digest, pack_digest: manifest.pack.digest, framework_commit: manifest.framework.commit, framework_resource_digest: manifest.framework.resource_digest };
    const buildEnv = buildEnvironment(mobileRoot, manifest, { acceptance, env });
    const framework = path.join(mobileRoot, '.local/framework');
    if (!existsSync(framework)) run(env.MOZAIKS_PYTHON || 'python', ['-m', 'factory_app.workflows.AppGenerator.tools.android_delivery', 'stage-framework', mobileRoot], { cwd: mobileRoot, env });
    verifyFiles(framework, manifest.framework.files);
    if (JSON.stringify(filesUnder(framework)) !== JSON.stringify(manifest.framework.files.map(entry => entry.path).sort())) fail('Framework file inventory differs');
    receipt.stage = 'verify';
    writeJson(receiptPath, receipt);
    run(env.MOZAIKS_PYTHON || 'python', ['-m', 'factory_app.workflows.AppGenerator.tools.android_delivery', 'verify', mobileRoot], { cwd: mobileRoot, env });
    prepareNativeWorkspace(mobileRoot, manifest);
    const command = (executable, args, cwd, stage) => {
      receipt.stage = stage;
      writeJson(receiptPath, receipt);
      run(executable, args, { cwd, env: buildEnv });
    };
    for (const directory of ['chat-ui', 'web_shell']) {
      const [executable, args] = npmCommand(['ci']);
      command(executable, args, path.join(framework, directory), 'install');
    }
    const [npm, installArgs] = npmCommand(['ci', '--ignore-scripts']);
    command(npm, installArgs, mobileRoot, 'install');
    const [buildNpm, buildArgs] = npmCommand(['run', 'build', '--', '--outDir', path.join(mobileRoot, '.local/web')]);
    command(buildNpm, buildArgs, path.join(framework, 'web_shell'), 'web');
    const capacitor = path.join(mobileRoot, 'node_modules/@capacitor/cli/bin/capacitor');
    command(process.execPath, [capacitor, 'add', 'android'], mobileRoot, 'android');
    command(process.execPath, [capacitor, 'sync', 'android'], mobileRoot, 'android');
    const identity = configureAndroid(mobileRoot, { acceptance, manifest });
    const assets = verifyBundledAssets(mobileRoot, manifest);
    const java = buildEnv.JAVA_HOME ? path.join(buildEnv.JAVA_HOME, 'bin', process.platform === 'win32' ? 'java.exe' : 'java') : 'java';
    command(java, ['-classpath', path.join(mobileRoot, 'android/gradle/wrapper/gradle-wrapper.jar'), 'org.gradle.wrapper.GradleWrapperMain', '--no-daemon', 'assembleDebug'], path.join(mobileRoot, 'android'), 'compile');
    const apkPath = 'android/app/build/outputs/apk/debug/app-debug.apk';
    const apk = readFileSync(regularPath(mobileRoot, apkPath));
    if (!apk.length) fail('Android compilation produced an empty APK');
    receipt = { ...receipt, status: 'succeeded', stage: 'complete', acceptance_build: acceptance, android: identity, assets, apk: { path: apkPath, sha256: sha256(apk), size_bytes: apk.length } };
    writeJson(receiptPath, receipt);
    return receipt;
  } catch (error) {
    writeJson(receiptPath, { ...receipt, status: 'failed' });
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length > 1 || (args.length && args[0] !== '--acceptance')) fail('Usage: node build.mjs [--acceptance]');
    console.log(JSON.stringify(buildAndroid(path.dirname(fileURLToPath(import.meta.url)), { acceptance: args[0] === '--acceptance' })));
  } catch {
    console.error('Android packaging failed; check the build stage in build-result.json.');
    process.exitCode = 1;
  }
}
