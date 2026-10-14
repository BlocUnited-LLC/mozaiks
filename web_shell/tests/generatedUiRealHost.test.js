import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import {
  generatedUiMode, generatedUiProjects, loopbackBaseUrl, realHostSettings,
  routeInventory, validateRealHostProfile,
} from '../playwright/fixtures/generatedUiRealHost.js';

const shellRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runId = '10a613fe-104a-42a0-a123-123456789012';
function profile() {
  return {
    candidate: { app_id: 'route-probe', revision: 'reviewed-1', source_sha: 'a'.repeat(40), bundle_sha256: 'b'.repeat(64) },
    routes: [
      { path: '/tasks', component: 'SchemaPage', schema: 'tasks', kind: 'declarative', heading: 'Tasks' },
      { path: '/board', component: 'TaskBoard', kind: 'custom', heading: 'Task board' },
      { path: '/u/:username', component: 'ProfilePage', kind: 'auth', excluded_reason: 'Separate account acceptance.' },
    ],
    cases: ['/tasks', '/board'].flatMap((route) => generatedUiProjects.map((project) => ({ id: `route:${route}`, route, project }))),
  };
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mozaiks-real-ui-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const profilePath = path.join(root, 'profile.json');
  fs.writeFileSync(profilePath, JSON.stringify(profile()));
  return { root, profilePath, env: {
    MOZAIKS_GENERATED_UI_MODE: 'real_host',
    MOZAIKS_GENERATED_UI_PROFILE: profilePath,
    MOZAIKS_GENERATED_UI_PROFILE_SHA256: createHash('sha256').update(fs.readFileSync(profilePath)).digest('hex'),
    MOZAIKS_GENERATED_UI_RUN_ID: runId,
    MOZAIKS_GENERATED_UI_BASE_URL: 'http://127.0.0.1:8123',
  } };
}

test('mode is explicit and invalid mode or remote origins fail closed', () => {
  assert.equal(generatedUiMode({}), 'mocked_rendering_smoke');
  assert.equal(generatedUiMode({ MOZAIKS_GENERATED_UI_MODE: 'real_host' }), 'real_host');
  for (const mode of ['', 'real-host', 'fixture']) assert.throws(() => generatedUiMode({ MOZAIKS_GENERATED_UI_MODE: mode }));
  for (const url of ['https://example.com', 'http://localhost.example', 'http://user:pass@localhost',
    'file:///tmp', 'http://127.0.0.1/path', 'http://127.0.0.1/?x=1', '', undefined]) {
    assert.throws(() => loopbackBaseUrl(url));
  }
  for (const url of ['http://127.0.0.1:8123', 'http://localhost:8123', 'http://[::1]:8123']) {
    assert.equal(loopbackBaseUrl(url), url);
  }
});

test('mixed route profile retains excluded framework routes in the exact inventory', () => {
  const nullable = profile();
  Object.assign(nullable.routes[1], { schema: null, visit_path: null, excluded_reason: null });
  const value = validateRealHostProfile(nullable);
  assert.deepEqual(routeInventory(value.routes), [
    { path: '/board', component: 'TaskBoard', schema: null },
    { path: '/tasks', component: 'SchemaPage', schema: 'tasks' },
    { path: '/u/:username', component: 'ProfilePage', schema: null },
  ]);
  assert.throws(() => routeInventory([...value.routes, value.routes[0]]));
  assert.throws(() => routeInventory(undefined));
});

test('profile rejects missing identities, routes, required projects and invalid exclusions', () => {
  const invalid = [
    (p) => { delete p.candidate; },
    (p) => { p.candidate.source_sha = 'not-a-source'; },
    (p) => { p.candidate.source_sha = ['a'.repeat(40)]; },
    (p) => { p.candidate.bundle_sha256 = 'bad'; },
    (p) => { p.candidate.extra = true; },
    (p) => { p.routes = []; },
    (p) => { p.cases = []; },
    (p) => { p.cases.pop(); },
    (p) => { p.cases.push(p.cases[0]); },
    (p) => { p.cases[0].project = 'undeclared-project'; },
    (p) => { p.cases[0].route = '/missing'; },
    (p) => { p.cases[0].route = '/u/:username'; },
    (p) => { p.routes.push(p.routes[0]); },
    (p) => { p.routes[0].excluded_reason = 'Do not visit'; },
    (p) => { p.routes[1].excluded_reason = 'Do not visit'; },
    (p) => { p.routes[2].excluded_reason = ''; },
    (p) => { p.routes[1].visit_path = '/tasks'; },
    (p) => { p.routes[1].visit_path = '//elsewhere/board'; },
    (p) => { p.routes[1].visit_path = '/\\elsewhere/board'; },
    (p) => { p.routes[1].kind = 'invented'; },
    (p) => { delete p.routes[1].heading; },
  ];
  for (const change of invalid) {
    const value = profile();
    change(value);
    assert.throws(() => validateRealHostProfile(value));
  }
});

test('parameterized required routes need a matching concrete visit path', () => {
  const value = profile();
  value.routes[1].path = '/tasks/:taskId';
  value.cases.filter((entry) => entry.route === '/board').forEach((entry) => {
    entry.route = '/tasks/:taskId'; entry.id = 'route:/tasks/:taskId';
  });
  assert.throws(() => validateRealHostProfile(value));
  value.routes[1].visit_path = '/tasks/task-1';
  assert.equal(validateRealHostProfile(value), value);
});

test('real-host settings require readable profile, UUID and a local trusted spec', (t) => {
  const f = fixture(t);
  assert.equal(realHostSettings(shellRoot, f.env).runId, runId);
  for (const override of [
    { MOZAIKS_GENERATED_UI_PROFILE: '' },
    { MOZAIKS_GENERATED_UI_PROFILE: path.join(f.root, 'missing.json') },
    { MOZAIKS_GENERATED_UI_PROFILE_SHA256: undefined },
    { MOZAIKS_GENERATED_UI_PROFILE_SHA256: 'incorrect' },
    { MOZAIKS_GENERATED_UI_RUN_ID: 'stale-or-missing' },
    { MOZAIKS_GENERATED_UI_SPEC: 'playwright.generated-ui.config.js' },
    { MOZAIKS_GENERATED_UI_SPEC: 'playwright/generated-ui/../../playwright.generated-ui.config.js' },
    { MOZAIKS_GENERATED_UI_SPEC: path.join(shellRoot, 'playwright/generated-ui/generated-app.real-host.acceptance.spec.js') },
  ]) assert.throws(() => realHostSettings(shellRoot, { ...f.env, ...override }));
  fs.writeFileSync(f.profilePath, '{"truncated":');
  f.env.MOZAIKS_GENERATED_UI_PROFILE_SHA256 = createHash('sha256').update(fs.readFileSync(f.profilePath)).digest('hex');
  assert.throws(() => realHostSettings(shellRoot, f.env));
});

test('profile mutation after review is rejected even when candidate and cases are unchanged', (t) => {
  const f = fixture(t);
  const changed = profile();
  changed.routes[1].heading = 'An unreviewed fallback heading';
  fs.writeFileSync(f.profilePath, JSON.stringify(changed));
  assert.throws(() => realHostSettings(shellRoot, f.env), /reviewed SHA-256/);
});

test('real-host config disables its server and retries while selecting the reviewed spec', (t) => {
  const f = fixture(t);
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import config from './playwright.generated-ui.config.js';
    console.log(JSON.stringify({ server: config.webServer ?? null, retries: config.retries,
      baseURL: config.use.baseURL, storageState: config.use.storageState,
      match: config.testMatch, projects: config.projects.map(p => p.name) }));
  `], { cwd: shellRoot, env: { ...process.env, ...f.env, CI: '1', MOZAIKS_GENERATED_UI_STORAGE_STATE: 'reviewed-auth.json' }, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const config = JSON.parse(result.stdout);
  assert.equal(config.server, null);
  assert.equal(config.retries, 0);
  assert.equal(config.baseURL, f.env.MOZAIKS_GENERATED_UI_BASE_URL);
  assert.equal(config.storageState, 'reviewed-auth.json');
  assert.match(config.match, /generated-app\.real-host\.acceptance\.spec\.js$/);
  assert.deepEqual(config.projects, generatedUiProjects);
});

test('real-host collection contains each required route in both projects and no mocked specs', (t) => {
  const f = fixture(t);
  const result = spawnSync(process.execPath, [
    path.join(shellRoot, 'node_modules/@playwright/test/cli.js'),
    'test', '-c', 'playwright.generated-ui.config.js', '--list', '--reporter=json',
  ], { cwd: shellRoot, env: { ...process.env, ...f.env }, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  const entries = [];
  const walk = (suites) => {
    for (const suite of suites) {
      for (const spec of suite.specs ?? []) {
        for (const entry of spec.tests) entries.push([spec.title, entry.projectName]);
      }
      walk(suite.suites ?? []);
    }
  };
  walk(report.suites);
  assert.deepEqual(entries.sort(), ['/tasks', '/board'].flatMap((route) =>
    generatedUiProjects.map((project) => [`real-host route ${route}`, project])).sort());
});

test('mocked mode retains the existing server and excludes the real-host spec', () => {
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (key.startsWith('MOZAIKS_GENERATED_UI_')) delete env[key];
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import config from './playwright.generated-ui.config.js';
    console.log(JSON.stringify({ command: config.webServer.command, match: config.testMatch }));
  `], { cwd: shellRoot, env, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const config = JSON.parse(result.stdout);
  assert.match(config.command, /npm run build/);
  assert.deepEqual(config.match, [
    '**/generated-app.acceptance.spec.js', '**/generated-app.generic.acceptance.spec.js',
    '**/entitlement-upgrade.spec.js', '**/token-depletion.spec.js',
  ]);
});
