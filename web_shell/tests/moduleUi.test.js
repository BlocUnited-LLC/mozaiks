import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createServer } from 'vite';
import { moduleUiPlugin, resolveModuleUiBarrels } from '../moduleUi.js';
import { registerModuleUi } from '../moduleUiRegistration.js';

function fixture(t) {
  const appDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mozaiks-module-ui-'));
  t.after(() => fs.rmSync(appDir, { recursive: true, force: true }));
  const write = (relative, text = '') => {
    const file = path.join(appDir, relative);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, text);
  };
  return { appDir, write };
}

function registry(preRegistered = {}) {
  const components = new Map(Object.entries(preRegistered));
  return {
    components,
    hasComponent: (name) => components.has(name),
    registerComponent: (name, component) => { if (!components.has(name)) components.set(name, component); },
  };
}

test('only modules with a manifest contribute a UI barrel', (t) => {
  const f = fixture(t);
  f.write('modules/stock_ledger/module.yaml', 'schema_version: mozaiks.module.v1');
  f.write('modules/stock_ledger/ui/index.js', 'export const StockLedgerPage = () => null;');
  f.write('modules/notes/module.yaml');
  f.write('modules/stray/ui/index.js', 'export const Stray = () => null;');
  f.write('modules/_shared/ui/index.js');

  assert.deepEqual(resolveModuleUiBarrels(f.appDir), {
    stock_ledger: path.join(f.appDir, 'modules', 'stock_ledger', 'ui', 'index.js'),
  });
});

test('a module with two barrels is refused', (t) => {
  const f = fixture(t);
  f.write('modules/stock_ledger/module.yaml');
  f.write('modules/stock_ledger/ui/index.js');
  f.write('modules/stock_ledger/ui/index.jsx');

  assert.throws(() => resolveModuleUiBarrels(f.appDir), /multiple UI barrels/);
});

test('the virtual module imports every barrel by module id', (t) => {
  const f = fixture(t);
  f.write('modules/stock_ledger/module.yaml');
  f.write('modules/stock_ledger/ui/index.jsx');
  const plugin = moduleUiPlugin({ appDir: f.appDir });
  const watched = [];

  const source = plugin.load.call({ addWatchFile: (file) => watched.push(file) }, plugin.resolveId('virtual:mozaiks-module-ui'));

  assert.match(source, /import \* as moduleUi0 from ".*modules\/stock_ledger\/ui\/index\.jsx";/);
  assert.match(source, /export default \{"stock_ledger": moduleUi0\};/);
  assert.ok(watched.some((file) => file.endsWith(path.join('stock_ledger', 'ui', 'index.jsx'))));
  // The dev server turns a watched path into an import; a directory would break the page.
  assert.ok(watched.every((file) => fs.statSync(file).isFile()));
  assert.equal(plugin.resolveId('something-else'), undefined);
});

test('module components register namespaced and plain, and the app keeps its own names', () => {
  const library = () => 'library page';
  const appOverride = () => 'app page';
  const helper = () => 'helper';
  const target = registry({ StockLedgerPage: appOverride });

  registerModuleUi({ stock_ledger: { StockLedgerPage: library, StockHelper: helper, default: library } }, target);

  assert.equal(target.components.get('StockLedgerPage'), appOverride);
  assert.equal(target.components.get('stock_ledger:StockLedgerPage'), library);
  assert.equal(target.components.get('StockHelper'), helper);
  assert.equal(target.components.has('default'), false);
});


test('module exports cannot take the shell authentication page names', () => {
  const login = () => 'module login';
  const callback = () => 'module callback';
  const target = registry();
  registerModuleUi({ stock: { LoginPage: login, AuthCallbackPage: callback } }, target);
  assert.equal(target.components.has('LoginPage'), false);
  assert.equal(target.components.has('AuthCallbackPage'), false);
  assert.equal(target.components.get('stock:LoginPage'), login);
  assert.equal(target.components.get('stock:AuthCallbackPage'), callback);
});

test('modules installed or removed while the dev server runs are rediscovered', async (t) => {
  const f = fixture(t);
  f.write('web/index.html', '<!doctype html>');
  f.write('modules/first/module.yaml');
  const server = await createServer({
    configFile: false, root: path.join(f.appDir, 'web'), logLevel: 'silent', appType: 'custom', optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, ws: false, fs: { strict: false } }, plugins: [moduleUiPlugin({ appDir: f.appDir })],
  });
  t.after(() => server.close());
  const ids = async () => Object.keys((await server.ssrLoadModule('virtual:mozaiks-module-ui')).default).sort();
  const settles = async (expected, change) => {
    change();
    const deadline = Date.now() + 15000;
    for (;;) {
      const found = await ids();
      if (JSON.stringify(found) === JSON.stringify(expected) || Date.now() > deadline) return assert.deepEqual(found, expected);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  };
  await new Promise((resolve) => setTimeout(resolve, 500));
  assert.deepEqual(await ids(), []);
  // A barrel added to a module that had none.
  await settles(['first'], () => f.write('modules/first/ui/index.js', 'export const First = () => null;'));
  // A module installed after the server started: its barrel may arrive before its manifest.
  await settles(['first', 'second'], () => {
    f.write('modules/second/ui/index.js', 'export const Second = () => null;');
    f.write('modules/second/module.yaml');
  });
  await settles(['second'], () => fs.rmSync(path.join(f.appDir, 'modules/first/ui/index.js')));
  await settles([], () => fs.rmSync(path.join(f.appDir, 'modules/second'), { recursive: true }));
});

test('the dev server rediscovers modules an app gains its first of', async (t) => {
  const f = fixture(t);
  f.write('web/index.html', '<!doctype html>');
  const server = await createServer({
    configFile: false, root: path.join(f.appDir, 'web'), logLevel: 'silent', appType: 'custom', optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, ws: false, fs: { strict: false } }, plugins: [moduleUiPlugin({ appDir: f.appDir })],
  });
  t.after(() => server.close());
  const ids = async () => Object.keys((await server.ssrLoadModule('virtual:mozaiks-module-ui')).default);
  await new Promise((resolve) => setTimeout(resolve, 500));
  assert.deepEqual(await ids(), []);
  f.write('modules/stock/module.yaml');
  f.write('modules/stock/ui/index.jsx', 'export const Stock = () => null;');
  const deadline = Date.now() + 15000;
  while ((await ids()).length === 0 && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 100));
  assert.deepEqual(await ids(), ['stock']);
});

test('only manifests and barrels invalidate the virtual module, which watches no folder', (t) => {
  const f = fixture(t);
  f.write('modules/stock/module.yaml');
  f.write('modules/stock/ui/index.js');
  const handlers = {};
  const added = [];
  const invalidated = [];
  const sent = [];
  moduleUiPlugin({ appDir: f.appDir }).configureServer({
    watcher: { add: (paths) => added.push(...paths), on: (event, handler) => { handlers[event] = handler; }, off() {} },
    moduleGraph: { getModuleById: (id) => ({ id }), invalidateModule: (module) => invalidated.push(module.id) },
    ws: { send: (message) => sent.push(message.type) },
  });
  const modules = path.join(f.appDir, 'modules');
  for (const file of ['stock/backend/handler.py', 'stock/ui/Page.jsx', 'stock/ui/deep/index.js', '_shared/ui/index.js', '.x/module.yaml', 'module.yaml']) {
    handlers.add(path.join(modules, file));
  }
  handlers.add(path.join(f.appDir, 'elsewhere/stock/module.yaml'));
  assert.deepEqual(invalidated, []);
  for (const file of ['stock/module.yaml', 'stock/ui/index.js', 'stock/ui/index.jsx']) {
    handlers.add(path.join(modules, file));
    handlers.unlink(path.join(modules, file));
  }
  assert.equal(invalidated.length, 6);
  assert.deepEqual([...new Set(sent)], ['full-reload']);
  assert.ok(added.every((entry) => fs.realpathSync(entry) === fs.realpathSync(modules)));
});
