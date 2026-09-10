import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { resolveWorkflowUiModules, workflowUiPlugin } from '../workflowUi.js';

function fixture(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'mozaiks-workflow-ui-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const primaryRoot = path.join(workspace, 'app', 'workflows');
  const factoryRoot = path.join(workspace, 'installed', 'factory_app', 'workflows');
  const write = (file, text = '') => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text); };
  const workflow = (root, name, ui = true) => {
    write(path.join(root, name, 'orchestrator.yaml'), `name: ${name}`);
    if (ui) write(path.join(root, name, 'ui', 'index.js'), 'export const Review = () => null;');
  };
  const registry = (value) => write(path.join(primaryRoot, 'extended_orchestration', 'extension_registry.json'), JSON.stringify(value));
  return { primaryRoot, factoryRoot, workflow, registry, write };
}

test('standalone workspace does not inherit Factory UI', (t) => {
  const f = fixture(t);
  f.workflow(f.primaryRoot, 'Local'); f.workflow(f.factoryRoot, 'Factory');
  assert.deepEqual(Object.keys(resolveWorkflowUiModules(f)), ['Local']);
});

test('registry inheritance composes local UI and Factory transitions', (t) => {
  const f = fixture(t);
  f.registry({ extends: 'mozaiks.default_workflow_registry' });
  f.workflow(f.primaryRoot, 'Local'); f.workflow(f.factoryRoot, 'Factory');
  f.write(path.join(f.factoryRoot, 'extended_orchestration', 'ui', 'index.js'));
  const modules = resolveWorkflowUiModules(f);
  assert.deepEqual(Object.keys(modules).sort(), ['Factory', 'Local', 'extended_orchestration']);
  assert.equal(modules.Factory, path.join(f.factoryRoot, 'Factory', 'ui', 'index.js'));
  const plugin = workflowUiPlugin(f);
  const source = plugin.load.call({ addWatchFile() {} }, plugin.resolveId('virtual:mozaiks-workflow-ui'));
  assert.ok(source.includes('import * as transitions from'));
  assert.ok(source.includes("export const transitionComponents = Reflect.get(transitions, 'default') ?? transitions"));
});

test('a local workflow replaces the whole inherited workflow including UI absence', (t) => {
  const f = fixture(t);
  f.registry({ extends: 'mozaiks.default_workflow_registry' });
  f.workflow(f.factoryRoot, 'Inherited'); f.workflow(f.primaryRoot, 'inherited', false);
  f.workflow(f.factoryRoot, 'Override'); f.workflow(f.primaryRoot, 'Override');
  assert.deepEqual(resolveWorkflowUiModules(f), { Override: path.join(f.primaryRoot, 'Override', 'ui', 'index.js') });
});

test('removed registry workflows are not registered and unknown inheritance fails', (t) => {
  const f = fixture(t);
  f.workflow(f.factoryRoot, 'Removed');
  f.registry({ extends: 'mozaiks.default_workflow_registry', workflows: [{ id: 'Removed', remove: true }] });
  assert.deepEqual(resolveWorkflowUiModules(f), {});
  f.registry({ extends: 'unknown.registry' });
  assert.throws(() => resolveWorkflowUiModules(f), /Unsupported/);
});

test('ambiguous UI barrels fail at build time', (t) => {
  const f = fixture(t);
  f.workflow(f.primaryRoot, 'Local');
  f.write(path.join(f.primaryRoot, 'Local', 'ui', 'index.jsx'));
  assert.throws(() => resolveWorkflowUiModules(f), /multiple UI barrels/);
});

test('Vite module imports every effective barrel by absolute path', (t) => {
  const f = fixture(t);
  f.workflow(f.primaryRoot, 'Local');
  const plugin = workflowUiPlugin(f);
  const id = plugin.resolveId('virtual:mozaiks-workflow-ui');
  const watched = [];
  const source = plugin.load.call({ addWatchFile: (name) => watched.push(name) }, id);
  assert.ok(source.includes(JSON.stringify(path.join(f.primaryRoot, 'Local', 'ui', 'index.js').replaceAll('\\', '/'))));
  assert.ok(source.includes('"Local": () => import('));
  assert.equal(watched.length, 2);
});
