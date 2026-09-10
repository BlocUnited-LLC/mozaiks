import fs from 'node:fs';
import path from 'node:path';

const defaultRegistry = 'mozaiks.default_workflow_registry';

/** Resolve UI barrels using the same workspace/default inheritance as workflow paths. */
export function resolveWorkflowUiModules({ primaryRoot, factoryRoot }) {
  const registryPath = path.join(primaryRoot, 'extended_orchestration', 'extension_registry.json');
  const registry = fs.existsSync(registryPath)
    ? JSON.parse(fs.readFileSync(registryPath, 'utf8'))
    : {};
  if (registry.extends && registry.extends !== defaultRegistry) {
    throw new Error(`Unsupported workflow registry inheritance: ${registry.extends}`);
  }
  const roots = registry.extends === defaultRegistry && path.resolve(primaryRoot) !== path.resolve(factoryRoot)
    ? [factoryRoot, primaryRoot]
    : [primaryRoot];
  const workflows = new Map();
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const entry of fs.readdirSync(root, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
      const folder = path.join(root, entry.name);
      if (entry.name !== 'extended_orchestration' && !fs.existsSync(path.join(folder, 'orchestrator.yaml'))) continue;
      const barrels = ['index.js', 'index.jsx'].map((name) => path.join(folder, 'ui', name)).filter(fs.existsSync);
      if (barrels.length > 1) throw new Error(`Workflow ${entry.name} declares multiple UI barrels`);
      // A local workflow replaces the inherited workflow, including absence of UI.
      // The orchestration directory instead overlays registry entries; its UI is optional.
      if (barrels.length || entry.name !== 'extended_orchestration') {
        workflows.set(entry.name.toLowerCase(), { name: entry.name, barrel: barrels[0] });
      }
    }
  }
  for (const workflow of registry.workflows || []) {
    if (workflow.remove === true) workflows.delete(String(workflow.id).toLowerCase());
  }
  return Object.fromEntries([...workflows.values()].filter(({ barrel }) => barrel).map(({ name, barrel }) => [name, path.resolve(barrel)]));
}

export function workflowUiPlugin(options) {
  const publicId = 'virtual:mozaiks-workflow-ui';
  const resolvedId = `\0${publicId}`;
  return {
    name: 'mozaiks-workflow-ui',
    resolveId(id) { return id === publicId ? resolvedId : undefined; },
    load(id) {
      if (id !== resolvedId) return undefined;
      const modules = resolveWorkflowUiModules(options);
      const transitionPath = modules.extended_orchestration;
      const transitionImport = transitionPath
        ? `import * as transitions from ${JSON.stringify(transitionPath.replaceAll('\\', '/'))};\nexport const transitionComponents = Reflect.get(transitions, 'default') ?? transitions;\n`
        : 'export const transitionComponents = {};\n';
      const entries = Object.entries(modules).map(([name, filename]) => {
        this.addWatchFile(filename);
        return `${JSON.stringify(name)}: () => import(${JSON.stringify(filename.replaceAll('\\', '/'))})`;
      });
      this.addWatchFile(path.join(options.primaryRoot, 'extended_orchestration', 'extension_registry.json'));
      return `${transitionImport}export default {${entries.join(',\n')}};`;
    },
  };
}
