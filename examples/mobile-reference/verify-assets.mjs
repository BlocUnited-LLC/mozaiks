import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function filesUnder(directory, prefix = '') {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) throw new Error(`Unexpected symbolic link: ${relative}`);
    if (entry.isDirectory()) return filesUnder(path.join(directory, entry.name), relative);
    if (!entry.isFile()) throw new Error(`Unexpected non-file: ${relative}`);
    return [relative];
  }).sort();
}

const sha256 = content => createHash('sha256').update(content).digest('hex');

export function verifyAssets(projectRoot) {
  const config = JSON.parse(readFileSync(path.join(projectRoot, 'capacitor.config.json'), 'utf8'));
  const nativeRoot = path.join(projectRoot, 'android/app/src/main/assets');
  const nativeConfig = JSON.parse(readFileSync(path.join(nativeRoot, 'capacitor.config.json'), 'utf8'));
  if (config.server?.url || nativeConfig.server?.url) {
    throw new Error('This reference must load bundled assets; server.url is not supported.');
  }
  if (config.appId !== nativeConfig.appId || config.appName !== nativeConfig.appName) {
    throw new Error('Copied Capacitor configuration differs from the reference. Run android:sync.');
  }
  const webRoot = path.resolve(projectRoot, config.webDir);
  const files = filesUnder(webRoot);
  if (!files.includes('index.html')) throw new Error('The web build has no index.html.');
  if (!readFileSync(path.join(webRoot, 'index.html'), 'utf8').includes(`<title>${config.appName}</title>`)) {
    throw new Error('The web build title differs from the reference app. Rebuild the selected workspace.');
  }
  const assets = files.map(relative => {
    const source = readFileSync(path.join(webRoot, relative));
    const packaged = readFileSync(path.join(nativeRoot, 'public', relative));
    const digest = sha256(source);
    if (digest !== sha256(packaged)) throw new Error(`Packaged asset differs: ${relative}`);
    return { path: relative, bytes: source.length, sha256: digest };
  });
  return {
    configuredAppId: config.appId,
    capacitorAndroidVersion: JSON.parse(readFileSync(
      path.join(projectRoot, 'node_modules/@capacitor/android/package.json'), 'utf8',
    )).version,
    verified: 'Every source web asset matches the generated Android project by SHA-256.',
    notVerified: ['Android package identity', 'APK compilation', 'device operation', 'native sign-in', 'store release'],
    assets,
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    console.log(JSON.stringify(verifyAssets(path.dirname(fileURLToPath(import.meta.url))), null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
