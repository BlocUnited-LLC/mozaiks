// Real emulator acceptance. The app and external Chrome both reach the owned
// loopback services through adb reverse; no token or callback is manufactured.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const requireShell = createRequire(path.join(root, 'web_shell/package.json'));
const { _android } = requireShell('playwright');
const { expect } = requireShell('@playwright/test');
const appId = 'org.mozaiks.examples.commonground';
const callback = `${appId}:/auth/callback`;
const origin = 'https://localhost';
const evidence = path.resolve(process.env.MOBILE_ACCEPTANCE_EVIDENCE || path.join(root, '.local/evidence/mobile-native'));
const apk = path.resolve(process.env.MOBILE_ACCEPTANCE_APK || path.join(root, 'examples/mobile-reference/android/app/build/outputs/apk/debug/app-debug.apk'));
mkdirSync(evidence, { recursive: true });

const proof = { status: 'running', stage: 'configuration', app_id: appId, callback, webview_origin: origin, checks: {} };
const save = () => writeFileSync(path.join(evidence, 'android-proof.json'), `${JSON.stringify(proof, null, 2)}\n`);
function stage(name) {
  proof.stage = name;
  save();
  console.log(JSON.stringify({ stage: name }));
}

let device;
let browser;
let page;
let postId;
const started = Date.now();
try {
  const configPath = path.resolve(process.env.COMMUNITY_LIVE_CONFIG);
  const environment = JSON.parse(readFileSync(configPath, 'utf8'));
  const runtime = JSON.parse(readFileSync(path.join(path.dirname(configPath), 'runtime-env.json'), 'utf8'));
  assert.equal(environment.status, 'ready');
  assert.equal(environment.native_callback, callback);
  assert.equal(environment.client_origin, origin);
  assert.equal(runtime.AUTH_ENABLED, 'true');
  assert.equal(runtime.AUTH_PROVIDER, 'jwt');
  assert.equal(runtime.ADDITIONAL_CORS_ORIGINS, origin);
  assert.match(environment.api_url, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.match(environment.auth_issuer, /^http:\/\/127\.0\.0\.1:\d+\/realms\/common-ground$/);
  const member = environment.users.find(user => user.username === 'alice');
  assert.ok(member?.password);
  Object.assign(proof, {
    source_sha: environment.source_sha,
    apk_sha256: createHash('sha256').update(readFileSync(apk)).digest('hex'),
    issuer: environment.auth_issuer,
    client_id: environment.client_id,
    audience: environment.audience,
    auth_enabled: true,
    images: environment.images,
  });

  stage('connect-device');
  const devices = await _android.devices();
  assert.equal(devices.length, 1, 'Exactly one disposable Android emulator is required');
  [device] = devices;
  device.setDefaultTimeout(45_000);
  proof.device = { serial: device.serial(), model: device.model() };
  stage('install-apk');
  await device.installApk(apk);
  // Starting Chrome before the app supplies a CDP connection to the actual
  // Custom Tab opened by Capacitor Browser, with no synthetic navigation.
  stage('launch-external-browser');
  browser = await device.launchBrowser({ pkg: 'com.android.chrome' });
  browser.setDefaultTimeout(45_000);
  const observed = { authorizations: 0, logouts: 0 };
  proof.external_browser_requests = observed;
  browser.on('request', request => {
    const url = new URL(request.url());
    if (`${url.origin}/realms/common-ground` !== environment.auth_issuer) return;
    if (url.pathname.endsWith('/protocol/openid-connect/auth')) observed.authorizations += 1;
    if (url.pathname.endsWith('/protocol/openid-connect/logout')) observed.logouts += 1;
  });
  stage('launch-native-webview');
  await device.shell(`am start -n ${appId}/.MainActivity`);
  page = await (await device.webView({ pkg: appId })).page();
  page.setDefaultTimeout(45_000);
  await expect.poll(() => page.evaluate(() => location.origin), { timeout: 45_000 }).toBe(origin);
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  proof.checks.bundled_webview = true;
  await page.screenshot({ path: path.join(evidence, '01-native-sign-in.png') });

  async function action(name, params, authenticated = true) {
    // Keep the bearer inside the real WebView. This also exercises device
    // routing and CORS, which a host-side API request would bypass.
    return page.evaluate(async ({ api, name, params, authenticated }) => {
      const headers = { 'Content-Type': 'application/json' };
      if (authenticated) headers.Authorization = `Bearer ${await window.mozaiksAuth.getAccessToken()}`;
      const response = await fetch(`${api}/api/modules/user_posts/${name}`, {
        method: 'POST', headers, body: JSON.stringify({ params }),
      });
      return { status: response.status, body: await response.json() };
    }, { api: environment.api_url, name, params, authenticated });
  }

  async function identityPage() {
    let found;
    await expect.poll(async () => {
      found = undefined;
      for (const candidate of browser.pages().reverse()) {
        const url = new URL(candidate.url());
        if (url.origin === new URL(environment.auth_issuer).origin && url.pathname.startsWith('/realms/common-ground/')
            && await candidate.getByLabel('Username or email', { exact: true }).isVisible().catch(() => false)) {
          found = candidate;
          break;
        }
      }
      return Boolean(found);
    }, { timeout: 45_000 }).toBe(true);
    return found;
  }

  stage('anonymous-create-rejected');
  const anonymous = await action('create_post', { body: 'Anonymous native acceptance probe' }, false);
  assert.equal(anonymous.status, 403);
  proof.checks.anonymous_create_status = anonymous.status;

  stage('external-browser-sign-in');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  const identity = await identityPage();
  await expect(identity.getByLabel('Username or email', { exact: true })).toBeVisible();
  await identity.screenshot({ path: path.join(evidence, '02-external-keycloak.png') });
  await identity.getByLabel('Username or email', { exact: true }).fill(member.username);
  await identity.getByLabel('Password', { exact: true }).fill(member.password);
  await identity.getByRole('button', { name: 'Sign In', exact: true }).click();
  await expect.poll(() => page.evaluate(async () => Boolean((await window.mozaiksAuth?.getCurrentUser())?.id && await window.mozaiksAuth?.getAccessToken())), { timeout: 45_000 }).toBe(true);
  await expect(page.getByLabel('What would you like to share?', { exact: true })).toBeVisible();
  assert.ok(observed.authorizations > 0);
  assert.equal(new URL(page.url()).origin, origin);
  proof.checks.external_browser_sign_in = true;
  proof.checks.native_callback_returned_to_app = true;

  stage('create-and-read-post');
  const body = `A community post from the Android app — ${Date.now()}`;
  await page.getByLabel('What would you like to share?', { exact: true }).fill(body);
  const createdPromise = page.waitForResponse(response => response.url().endsWith('/user_posts/create_post') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Share post', exact: true }).click();
  const created = await createdPromise;
  assert.equal(created.status(), 200);
  const result = await created.json();
  assert.equal(result.success, true);
  postId = result.post.post_id;
  await expect(page.getByTestId(`post-${postId}`).getByText(body, { exact: true })).toBeVisible();
  const read = await action('get_post', { post_id: postId });
  assert.equal(read.status, 200);
  assert.equal(read.body.post.body, body);
  proof.checks.authenticated_create_and_read = true;
  proof.post_id = postId;
  await page.screenshot({ path: path.join(evidence, '03-native-community-post.png') });

  stage('reload-and-delete-post');
  await page.reload();
  const post = page.getByTestId(`post-${postId}`);
  await expect(post.getByText(body, { exact: true })).toBeVisible();
  proof.checks.post_survives_webview_reload = true;
  await post.getByRole('button', { name: 'Delete your post', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete post', exact: true }).click();
  await expect(post).toHaveCount(0);
  const deleted = await action('get_post', { post_id: postId });
  assert.equal(deleted.status, 200);
  assert.equal(deleted.body.post, null);
  proof.checks.authenticated_delete = true;

  stage('external-browser-logout');
  await page.getByTitle('Alice Gardener', { exact: true }).click();
  await page.getByRole('button', { name: 'Sign Out', exact: true }).click();
  await expect.poll(() => page.evaluate(async () => !(await window.mozaiksAuth?.getAccessToken())), { timeout: 45_000 }).toBe(true);
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  await expect.poll(() => observed.logouts, { timeout: 45_000 }).toBeGreaterThan(0);
  // Local session clearing precedes the browser trip. Require Android to
  // resume our activity after the real provider logout request before acting
  // on the WebView again. Never persist dumpsys, whose intents can carry codes.
  await expect.poll(async () => {
    const activities = (await device.shell('dumpsys activity activities')).toString();
    return activities.split('\n').some(line => /(?:topResumedActivity|mResumedActivity)/.test(line) && line.includes(`${appId}/.MainActivity`));
  }, { timeout: 45_000 }).toBe(true);
  proof.checks.native_logout_returned_to_app = true;
  const afterLogout = await action('create_post', { body: 'Logged out native acceptance probe' }, false);
  assert.equal(afterLogout.status, 403);
  proof.checks.logged_out_create_status = afterLogout.status;
  await page.screenshot({ path: path.join(evidence, '04-native-signed-out.png') });

  stage('provider-session-cleared');
  const before = observed.authorizations;
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect.poll(() => observed.authorizations, { timeout: 45_000 }).toBeGreaterThan(before);
  const nextIdentity = await identityPage();
  await expect(nextIdentity.getByLabel('Username or email', { exact: true })).toBeVisible();
  await expect(nextIdentity.getByLabel('Password', { exact: true })).toBeVisible();
  proof.checks.provider_logout_requires_credentials_again = true;
  proof.checks.external_browser_logout = true;
  proof.status = 'passed';
  stage('complete');
} catch (error) {
  proof.status = 'failed';
  // Playwright errors can include callback URLs or filled values. Persist only
  // the stage and error class, never a raw stack, trace, browser log or token.
  proof.failure_type = error?.name || 'Error';
  console.error(JSON.stringify({ status: proof.status, stage: proof.stage, failure_type: proof.failure_type }));
  process.exitCode = 1;
} finally {
  proof.elapsed_seconds = Math.round((Date.now() - started) / 100) / 10;
  save();
  await browser?.close().catch(() => {});
  await device?.close().catch(() => {});
}
