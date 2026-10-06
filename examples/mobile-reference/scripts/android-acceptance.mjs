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
const { expect: playwrightExpect } = requireShell('@playwright/test');
// Device UI assertions have their own timeout; page.setDefaultTimeout covers
// browser actions only. Wait on rendered state through slow emulator startup.
const expect = playwrightExpect.configure({ timeout: 45_000 });
const appId = 'org.mozaiks.examples.commonground';
const callback = `${appId}:/auth/callback`;
const origin = 'https://localhost';
const evidence = path.resolve(process.env.MOBILE_ACCEPTANCE_EVIDENCE || path.join(root, '.local/evidence/mobile-native'));
const apk = path.resolve(process.env.MOBILE_ACCEPTANCE_APK || path.join(root, '.local/mobile-reference/workspace/mobile/android/app/build/outputs/apk/debug/app-debug.apk'));
mkdirSync(evidence, { recursive: true });

const proof = { status: 'running', stage: 'configuration', app_id: appId, callback, webview_origin: origin, checks: {} };
const save = () => writeFileSync(path.join(evidence, 'android-proof.json'), `${JSON.stringify(proof, null, 2)}\n`);
function stage(name) {
  proof.stage = name;
  save();
  console.log(JSON.stringify({ stage: name }));
}

function safeFailureMessage(error, privateValues) {
  let message = String(error?.message || error).split(/\r?\n/, 1)[0].replace(/\u001b\[[0-9;]*m/g, '');
  for (const value of privateValues) if (value) message = message.split(value).join('[redacted]');
  return message
    .replace(/\b[A-Za-z][A-Za-z\d+.-]*:[^\s"'<>]+/g, '[uri]')
    .replace(/(?:\/[^\s"'<>?]*)?\?[^\s"'<>]+/g, '[query]')
    .replace(/\b(?:Bearer|Basic)\s+\S+/gi, '[credential]')
    .replace(/\b(?:code|state|nonce|password|client_secret|access_token|id_token|refresh_token)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '[credential]')
    .replace(/\b[A-Za-z\d_-]{8,}\.[A-Za-z\d_-]{8,}\.[A-Za-z\d_-]{8,}\b/g, '[token]')
    .replace(/\b[A-Za-z\d+/_=-]{24,}(?:\.[A-Za-z\d+/_=-]+)*/g, '[opaque]')
    .slice(0, 240);
}

let device;
let browser;
let page;
let postId;
const privateValues = [];
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
  privateValues.push(member.password);
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
  let latestAuthorizationState;
  proof.external_browser_requests = observed;
  browser.on('request', request => {
    const url = new URL(request.url());
    if (`${url.origin}/realms/common-ground` !== environment.auth_issuer) return;
    if (url.pathname.endsWith('/protocol/openid-connect/auth')) {
      observed.authorizations += 1;
      latestAuthorizationState = url.searchParams.get('state');
      privateValues.push(latestAuthorizationState);
    }
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
        if (url.origin === new URL(environment.auth_issuer).origin && url.pathname === '/realms/common-ground/protocol/openid-connect/auth'
            && latestAuthorizationState && url.searchParams.get('state') === latestAuthorizationState
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

  stage('reload-native-webview');
  await page.reload();
  stage('wait-for-reloaded-post');
  const post = page.getByTestId(`post-${postId}`);
  // A reload boots the shell and loads the feed again. Expect's timeout is
  // independent of page.setDefaultTimeout; the emulator's first boot takes
  // longer than its default five seconds. Wait for this actual persisted post.
  await expect(post.getByText(body, { exact: true })).toBeVisible({ timeout: 45_000 });
  proof.checks.post_survives_webview_reload = true;
  stage('delete-post');
  await post.getByRole('button', { name: 'Delete your post', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete post', exact: true }).click();
  await expect(post).toHaveCount(0);
  const deleted = await action('get_post', { post_id: postId });
  assert.equal(deleted.status, 200);
  assert.equal(deleted.body.post, null);
  proof.checks.authenticated_delete = true;

  stage('open-profile-menu');
  // The reference realm maps the full name, without a separate given_name claim.
  await page.getByTitle('Alice Gardener', { exact: true }).click();
  const beforeLogoutDocument = await page.evaluate(() => performance.timeOrigin);
  stage('external-browser-logout');
  await page.getByRole('button', { name: 'Sign Out', exact: true }).click();
  await expect.poll(() => observed.logouts, { timeout: 45_000 }).toBeGreaterThan(0);
  // Local session clearing precedes the browser trip. Require Android to
  // resume our activity after the real provider logout request before acting
  // on the WebView again. Never persist dumpsys, whose intents can carry codes.
  await expect.poll(async () => {
    const activities = (await device.shell('dumpsys activity activities')).toString();
    return activities.split('\n').some(line => /(?:topResumedActivity|mResumedActivity)/.test(line) && line.includes(`${appId}/.MainActivity`));
  }, { timeout: 45_000 }).toBe(true);
  proof.checks.native_logout_returned_to_app = true;
  stage('wait-for-signed-out-webview');
  // The shared adapter navigates the WebView after validating the native return.
  // Resuming Android's activity can precede this final document navigation.
  await page.waitForFunction(previous => performance.timeOrigin !== previous && Boolean(window.mozaiksAuth), beforeLogoutDocument);
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  assert.equal(await page.evaluate(() => window.mozaiksAuth.getAccessToken()), null);
  proof.checks.signed_out_document_ready = true;
  stage('logged-out-create-rejected');
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
  // No raw stack, trace or browser log: these can contain callback URLs and
  // filled credentials. The first line is redacted before it leaves memory.
  proof.failure_type = error?.name || 'Error';
  proof.failure_message = safeFailureMessage(error, privateValues);
  if (page && new URL(page.url()).origin === origin) {
    proof.failure_ui = await page.evaluate(async targetPost => ({
      has_auth_adapter: Boolean(window.mozaiksAuth),
      authenticated: Boolean(await window.mozaiksAuth?.getAccessToken?.()),
      has_post: [...document.querySelectorAll('[data-testid]')].some(element => element.dataset.testid === `post-${targetPost}`),
      has_textarea: Boolean(document.querySelector('textarea')),
      has_sign_in: [...document.querySelectorAll('button')].some(element => element.textContent.trim() === 'Sign in'),
    }), postId).catch(() => null);
    // Capture only the app WebView, never the external browser with filled
    // credentials. Mask inputs and error/code surfaces as an additional guard.
    proof.failure_screenshot = await page.screenshot({
      path: path.join(evidence, 'failure-webview.png'), timeout: 5_000,
      mask: [page.locator('input, textarea, [role="alert"], pre, code')],
    }).then(() => true, () => false);
  }
  console.error(JSON.stringify({ status: proof.status, stage: proof.stage, failure_type: proof.failure_type, failure_message: proof.failure_message }));
  process.exitCode = 1;
} finally {
  proof.elapsed_seconds = Math.round((Date.now() - started) / 100) / 10;
  save();
  await browser?.close().catch(() => {});
  await device?.close().catch(() => {});
}
