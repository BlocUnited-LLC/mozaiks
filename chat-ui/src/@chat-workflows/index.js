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
  '@chat-workflows-root/*/ui/index.{js,jsx}',
  { eager: true }
);
const workflowModules = primaryWorkflowModules;

// Build WORKFLOW_REGISTRY from the injected workflow root.
// Key   -> workflow folder name
// Value -> { components } map from the ui/index exports
const WORKFLOW_REGISTRY = {};

for (const [modulePath, mod] of Object.entries(workflowModules)) {
  const match = modulePath.match(/[\\/]([^/\\]+)[\\/]ui[\\/]index(?:\.[^.]+)?$/);
  if (!match) continue;
  const workflowName = match[1];
  const components = mod.default ?? mod;
  if (components && typeof components === 'object') {
    WORKFLOW_REGISTRY[workflowName] = { components };
  }
}

export const getLoadedWorkflows = () => Object.keys(WORKFLOW_REGISTRY);

export const getWorkflow = (workflowName) =>
  WORKFLOW_REGISTRY[workflowName] ?? null;

export const initializeWorkflows = (registerComponent) => {
  if (typeof registerComponent !== 'function') return;
  for (const [workflowName, { components }] of Object.entries(WORKFLOW_REGISTRY)) {
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
