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

/** Whether a file decides which barrels exist: modules/<module_id>/module.yaml or ui/index.js(x). */
function decidesModuleUi(modulesDir, filename) {
  const parts = path.relative(modulesDir, path.resolve(filename)).split(path.sep);
  return /^[^._]/.test(parts[0]) && ['module.yaml', 'ui/index.js', 'ui/index.jsx'].includes(parts.slice(1).join('/'));
}

export function moduleUiPlugin({ appDir }) {
  const publicId = 'virtual:mozaiks-module-ui';
  const resolvedId = `\0${publicId}`;
  return {
    name: 'mozaiks-module-ui',
    configureServer(server) {
      // The watcher follows the folder, so a module installed later is seen; the virtual
      // module itself still depends on barrel files only.
      const modulesDir = path.resolve(appDir, 'modules');
      const roots = fs.existsSync(modulesDir) ? [modulesDir, fs.realpathSync(modulesDir)] : [modulesDir];
      server.watcher.add([...new Set(roots)]);
      const refreshModules = (filename) => {
        if (!roots.some((root) => decidesModuleUi(root, filename))) return;
        const module = server.moduleGraph.getModuleById(resolvedId);
        if (!module) return;
        server.moduleGraph.invalidateModule(module);
        server.ws.send({ type: 'full-reload', path: '*' });
      };
      server.watcher.on('add', refreshModules);
      server.watcher.on('unlink', refreshModules);
      server.httpServer?.once('close', () => {
        server.watcher.off('add', refreshModules);
        server.watcher.off('unlink', refreshModules);
      });
    },
    resolveId(id) { return id === publicId ? resolvedId : undefined; },
    load(id) {
      if (id !== resolvedId) return undefined;
      // Only existing barrel files are watched: in the dev server a watched path becomes an
      // import of this module, and a directory cannot be imported.
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
