import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
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
