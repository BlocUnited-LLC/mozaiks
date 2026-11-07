import fs from 'node:fs';
import path from 'node:path';

/**
 * Module-owned UI barrels: <app>/modules/<module_id>/ui/index.js (or .jsx).
 *
 * A module ships its own React components beside its backend, so an installed
 * capability library registers its pages without edits to the app's own
 * registration barrel. Discovery mirrors workflow UI barrels: a folder scan.
 */
export function resolveModuleUiBarrels(appDir) {
  const modulesDir = path.join(appDir, 'modules');
  if (!fs.existsSync(modulesDir)) return {};
  const barrels = {};
  for (const entry of fs.readdirSync(modulesDir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!entry.isDirectory() || entry.name.startsWith('.') || entry.name.startsWith('_')) continue;
    const folder = path.join(modulesDir, entry.name);
    if (!fs.existsSync(path.join(folder, 'module.yaml'))) continue;
    const found = ['index.js', 'index.jsx'].map((name) => path.join(folder, 'ui', name)).filter(fs.existsSync);
    if (found.length > 1) throw new Error(`Module ${entry.name} declares multiple UI barrels`);
    if (found.length) barrels[entry.name] = path.resolve(found[0]);
  }
  return barrels;
}

export function moduleUiPlugin({ appDir }) {
  const publicId = 'virtual:mozaiks-module-ui';
  const resolvedId = `\0${publicId}`;
  return {
    name: 'mozaiks-module-ui',
    resolveId(id) { return id === publicId ? resolvedId : undefined; },
    load(id) {
      if (id !== resolvedId) return undefined;
      const modulesDir = path.join(appDir, 'modules');
      if (fs.existsSync(modulesDir)) this.addWatchFile(modulesDir);
      const imports = [];
      const entries = [];
      Object.entries(resolveModuleUiBarrels(appDir)).forEach(([moduleId, filename], index) => {
        this.addWatchFile(filename);
        imports.push(`import * as moduleUi${index} from ${JSON.stringify(filename.replaceAll('\\', '/'))};`);
        entries.push(`${JSON.stringify(moduleId)}: moduleUi${index}`);
      });
      return `${imports.join('\n')}\nexport default {${entries.join(',\n')}};\n`;
    },
  };
}
