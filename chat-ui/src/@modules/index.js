// ==============================================================================
// FILE: chat-ui/src/@modules/index.js
// DESCRIPTION: Auto-discovers platform module UI components from
//              platform/modules/*/ui/index.{js,jsx}
//
//              No manual registration required — create a ui/index.js inside
//              any platform/modules/<name>/ folder and it appears here.
//
//              Each ui/index.js must export a default object:
//                { ComponentName: ReactComponent, ... }
//
//              Components are registered in the chat-ui component registry
//              when initializeModules(registerComponent) is called at startup.
// ==============================================================================

// Vite glob import — scans platform/modules/*/ui/index.{js,jsx} at build time.
// Path is relative to this file: ../../../platform/modules/*/ui/index.js
const moduleUIs = import.meta.glob(
  '../../../platform/modules/*/ui/index.{js,jsx}',
  { eager: true }
);

// Build MODULE_REGISTRY from the glob results.
// Key   → module name (derived from directory name)
// Value → { components } map from ui/index.js default export
const MODULE_REGISTRY = {};

for (const [modulePath, mod] of Object.entries(moduleUIs)) {
  // Extract module name from path: .../platform/modules/<name>/ui/index.js
  const match = modulePath.match(/platform[\\/]modules[\\/]([^/\\]+)[\\/]ui[\\/]index/);
  if (!match) continue;
  const moduleName = match[1];
  // ui/index.js must export an object of { ComponentName: Component }
  const components = mod.default ?? mod;
  if (components && typeof components === 'object') {
    MODULE_REGISTRY[moduleName] = { components };
  }
}

export const getLoadedModules = () => Object.keys(MODULE_REGISTRY);

export const getModule = (moduleName) =>
  MODULE_REGISTRY[moduleName] ?? null;

/**
 * Register all discovered module UI components in the given component registry.
 * Call this once at app startup alongside initializeWorkflows.
 *
 * @param {Function} registerComponent - registerComponent(name, Component, meta)
 */
export const initializeModules = (registerComponent) => {
  if (typeof registerComponent !== 'function') return;
  for (const [moduleName, { components }] of Object.entries(MODULE_REGISTRY)) {
    for (const [componentName, component] of Object.entries(components)) {
      registerComponent(componentName, component, {
        description: `${moduleName} module component`,
        module: moduleName,
      });
    }
  }
};

const moduleRegistry = { getLoadedModules, getModule, initializeModules };
export default moduleRegistry;
