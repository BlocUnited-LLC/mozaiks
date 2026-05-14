// ==============================================================================
// FILE: chat-ui/src/@chat-workflows/index.js
// DESCRIPTION: Registers workflow UI components from a host-injected root.
//
//   The consuming host owns the active workflow bundle and injects that root as
//   the @chat-workflows-root alias at build time.
//
//   chat-ui stays workflow-agnostic: it only knows how to read
//   <workflow>/ui/index.{js,jsx} barrels from the injected root and register
//   their exports in the component registry.
// ==============================================================================

const primaryWorkflowModules = import.meta.glob(
  '@chat-workflows-root/*/ui/index.{js,jsx}'
);
const workflowModules = primaryWorkflowModules;

// Build WORKFLOW_REGISTRY from the injected workflow root.
// Key   -> workflow folder name
// Value -> { components } map from the ui/index exports
const WORKFLOW_REGISTRY = {};

const getWorkflowNameFromPath = (modulePath) => {
  const match = modulePath.match(/[\\/]([^/\\]+)[\\/]ui[\\/]index(?:\.[^.]+)?$/);
  return match ? match[1] : null;
};

const normalizeWorkflowModule = (mod) => {
  const workflowModule = mod.default ?? mod;
  return workflowModule && typeof workflowModule === 'object'
    ? workflowModule
    : null;
};

for (const [modulePath, loadModule] of Object.entries(workflowModules)) {
  const workflowName = getWorkflowNameFromPath(modulePath);
  if (!workflowName) continue;
  WORKFLOW_REGISTRY[workflowName] = {
    loadModule,
    module: null,
    components: null,
    loadPromise: null,
  };
}

export const getLoadedWorkflows = () => Object.keys(WORKFLOW_REGISTRY);

export const getWorkflow = (workflowName) =>
  WORKFLOW_REGISTRY[workflowName]?.module ?? null;

const loadWorkflow = async (workflowName) => {
  const entry = WORKFLOW_REGISTRY[workflowName];
  if (!entry) return null;
  if (entry.module) return entry.module;
  if (!entry.loadPromise) {
    entry.loadPromise = Promise.resolve(entry.loadModule())
      .then((mod) => {
        const workflowModule = normalizeWorkflowModule(mod);
        entry.module = workflowModule;
        entry.components = workflowModule;
        return workflowModule;
      })
      .catch((error) => {
        entry.loadPromise = null;
        throw error;
      });
  }
  await entry.loadPromise;
  return entry.module;
};

export const initializeWorkflows = async (registerComponent) => {
  if (typeof registerComponent !== 'function') return;
  for (const workflowName of Object.keys(WORKFLOW_REGISTRY)) {
    await loadWorkflow(workflowName);
    const { components } = WORKFLOW_REGISTRY[workflowName] ?? {};
    if (!components || typeof components !== 'object') continue;
    for (const [componentName, component] of Object.entries(components)) {
      // Namespaced registration is the deterministic lookup key for workflow UI tools.
      const namespacedComponentName = `${workflowName}:${componentName}`;
      registerComponent(namespacedComponentName, component, {
        description: `${workflowName} workflow component (namespaced)`,
      });
      // Plain registration is retained for route/transition components and developer ergonomics.
      registerComponent(componentName, component, {
        description: `${workflowName} workflow component`,
      });
    }
  }
};

const workflowRegistry = { getLoadedWorkflows, getWorkflow, initializeWorkflows };
export default workflowRegistry;
