import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';

const projectRoot = process.cwd();
const iosDir = path.join(projectRoot, 'ios');
const androidDir = path.join(projectRoot, 'android');
const manifestPath = path.resolve(projectRoot, '../../platform/app.json');
const mobileAppJsonPath = path.join(projectRoot, 'app.json');
const iosInfoPlistPath = path.join(projectRoot, 'ios', 'MozaiksMobile', 'Info.plist');
const iosProjectPath = path.join(projectRoot, 'ios', 'MozaiksMobile.xcodeproj', 'project.pbxproj');
const androidBuildGradlePath = path.join(projectRoot, 'android', 'app', 'build.gradle');
const androidStringsPath = path.join(projectRoot, 'android', 'app', 'src', 'main', 'res', 'values', 'strings.xml');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: process.platform === 'win32',
    ...options,
  });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function sanitizeIdentifierPart(value) {
  return String(value || 'app')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '.')
    .replace(/^\.+|\.+$/g, '')
    .replace(/\.{2,}/g, '.');
}

function deriveReverseDomainId(appId) {
  return `com.mozaiks.${sanitizeIdentifierPart(appId) || 'app'}`;
}

function toPascalCase(value) {
  return String(value || 'mozaiks-app')
    .replace(/[^a-zA-Z0-9]+/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join('') || 'MozaiksMobile';
}

function replaceAllExact(content, search, replacement) {
  return content.split(search).join(replacement);
}

if (!existsSync(iosDir) || !existsSync(androidDir)) {
  console.error('Missing ios/ or android/ project directories. Regenerate the native template first.');
  process.exit(1);
}

if (!existsSync(manifestPath)) {
  console.error('Missing shared app manifest at platform/app.json.');
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
const mobileConfig = manifest.platforms?.mobile ?? {};

if (mobileConfig.enabled === false) {
  console.error('Mobile platform is disabled in platform/app.json. Set platforms.mobile.enabled=true to prepare native projects.');
  process.exit(1);
}

const mobileAppJson = {
  name: toPascalCase(manifest.appId || 'mozaiks-app'),
  displayName: mobileConfig.displayName || manifest.appName || 'Mozaiks',
};

const derivedId = deriveReverseDomainId(manifest.appId);
const iosBundleId = mobileConfig.ios?.bundleId || derivedId;
const iosTestBundleId = `${iosBundleId}.tests`;
const androidApplicationId = mobileConfig.android?.applicationId || derivedId;
const androidNamespace = mobileConfig.android?.namespace || androidApplicationId;
const versionName = mobileConfig.version?.name || '1.0.0';
const versionCode = Number.isFinite(mobileConfig.version?.code) ? mobileConfig.version.code : 1;
const redirectScheme = mobileConfig.auth?.redirectScheme
  || String(manifest.appId || 'mozaiks').replace(/[^a-zA-Z0-9]/g, '').toLowerCase()
  || 'mozaiks';

writeFileSync(mobileAppJsonPath, `${JSON.stringify(mobileAppJson, null, 2)}\n`);

let iosInfoPlist = readFileSync(iosInfoPlistPath, 'utf8')
  .replace(/<key>CFBundleDisplayName<\/key>\s*<string>.*?<\/string>/s, `<key>CFBundleDisplayName</key>\n\t<string>${mobileAppJson.displayName}</string>`);

const urlTypesBlock = `\t<key>CFBundleURLTypes</key>\n\t<array>\n\t\t<dict>\n\t\t\t<key>CFBundleURLName</key>\n\t\t\t<string>${iosBundleId}</string>\n\t\t\t<key>CFBundleURLSchemes</key>\n\t\t\t<array>\n\t\t\t\t<string>${redirectScheme}</string>\n\t\t\t</array>\n\t\t</dict>\n\t</array>`;

if (/<key>CFBundleURLTypes<\/key>/s.test(iosInfoPlist)) {
  iosInfoPlist = iosInfoPlist.replace(/<key>CFBundleURLTypes<\/key>\s*<array>[\s\S]*?<\/array>/, urlTypesBlock);
} else {
  iosInfoPlist = iosInfoPlist.replace(/<key>CFBundlePackageType<\/key>\s*<string>APPL<\/string>/, `<key>CFBundlePackageType</key>\n\t<string>APPL</string>\n${urlTypesBlock}`);
}
writeFileSync(iosInfoPlistPath, iosInfoPlist);

let iosProject = readFileSync(iosProjectPath, 'utf8');
iosProject = iosProject.replace(
  /(PRODUCT_BUNDLE_IDENTIFIER = ).*?(;\r?\n\s+PRODUCT_NAME = "\$\(TARGET_NAME\)";)/g,
  `$1"${iosTestBundleId}"$2`,
);
iosProject = iosProject.replace(
  /(PRODUCT_BUNDLE_IDENTIFIER = ).*?(;\r?\n\s+PRODUCT_NAME = MozaiksMobile;)/g,
  `$1"${iosBundleId}"$2`,
);
iosProject = iosProject.replace(/MARKETING_VERSION = .*?;/g, `MARKETING_VERSION = ${versionName};`);
iosProject = iosProject.replace(/CURRENT_PROJECT_VERSION = .*?;/g, `CURRENT_PROJECT_VERSION = ${versionCode};`);
writeFileSync(iosProjectPath, iosProject);

let androidBuildGradle = readFileSync(androidBuildGradlePath, 'utf8');
androidBuildGradle = androidBuildGradle.replace(/namespace ".*?"/, `namespace "${androidNamespace}"`);
androidBuildGradle = androidBuildGradle.replace(/applicationId ".*?"/, `applicationId "${androidApplicationId}"`);
androidBuildGradle = androidBuildGradle.replace(/versionCode \d+/, `versionCode ${versionCode}`);
androidBuildGradle = androidBuildGradle.replace(/versionName ".*?"/, `versionName "${versionName}"`);
if (/manifestPlaceholders = \[appAuthRedirectScheme: ".*?"\]/.test(androidBuildGradle)) {
  androidBuildGradle = androidBuildGradle.replace(
    /manifestPlaceholders = \[appAuthRedirectScheme: ".*?"\]/,
    `manifestPlaceholders = [appAuthRedirectScheme: "${redirectScheme}"]`,
  );
} else {
  androidBuildGradle = androidBuildGradle.replace(
    /(\s*versionName ".*?"\r?\n)(\s*)}/,
    `$1$2manifestPlaceholders = [appAuthRedirectScheme: "${redirectScheme}"]\n$2}`,
  );
}
writeFileSync(androidBuildGradlePath, androidBuildGradle);

const androidStrings = readFileSync(androidStringsPath, 'utf8').replace(/<string name="app_name">.*?<\/string>/, `<string name="app_name">${mobileAppJson.displayName}</string>`);
writeFileSync(androidStringsPath, androidStrings);

console.log('Native project directories found.');
console.log(`Synced mobile app manifest: ${mobileAppJson.displayName}`);
console.log(`Synced iOS bundle ID: ${iosBundleId}`);
console.log(`Synced Android app ID: ${androidApplicationId}`);
console.log(`Synced native auth redirect scheme: ${redirectScheme}`);

if (process.platform !== 'darwin') {
  console.log('Skipping CocoaPods install because this is not macOS. Android project is ready.');
  process.exit(0);
}

console.log('Installing Ruby gems with Bundler...');
run('bundle', ['install']);

console.log('Installing CocoaPods dependencies...');
run('bundle', ['exec', 'pod', 'install'], { cwd: iosDir });

console.log('Native iOS dependencies prepared.');
