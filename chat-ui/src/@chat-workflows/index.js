// ==============================================================================
// FILE: chat-ui/src/@chat-workflows/index.js
// DESCRIPTION: Auto-discovers workflow UI components from any platform layer.
//
//   Two platform roots are scanned:
//     platform/workflows/*/ui/index.js             — OSS example platform (JokeFactory etc.)
//     mozaiks-platform/app/workflows/*/ui/index.js — generated/product workflow UI
//
//   Drop a ui/index.js barrel into any supported workflow folder and all named
//   exports are automatically registered in the component registry.
//   No changes to coreComponents.js or any other file needed.
// ==============================================================================

// OSS example platform — platform/workflows/<WorkflowName>/ui/index.js
const ossModules = import.meta.glob(
  '../../../platform/workflows/*/ui/index.{js,jsx}',
  { eager: true }
);

// mozaiks-platform — per-workflow ui/index.js barrels for generated/product workflows
const mozaiksPlatformWorkflowModules = import.meta.glob(
  '../../../mozaiks-platform/app/workflows/*/ui/index.{js,jsx}',
  { eager: true }
);

// Build WORKFLOW_REGISTRY from all discovered modules.
// Key  → source label (workflow name)
// Value → { components } map from the index.js exports
const WORKFLOW_REGISTRY = {};

for (const [modulePath, mod] of Object.entries(ossModules)) {
  const match = modulePath.match(/platform[\\/]workflows[\\/]([^/\\]+)[\\/]ui[\\/]index/);
  if (!match) continue;
  const workflowName = match[1];
  const components = mod.default ?? mod;
  if (components && typeof components === 'object') {
    WORKFLOW_REGISTRY[workflowName] = { components };
  }
}

for (const [modulePath, mod] of Object.entries(mozaiksPlatformWorkflowModules)) {
  const match = modulePath.match(/mozaiks-platform[\\/]app[\\/]workflows[\\/]([^/\\]+)[\\/]ui[\\/]index/);
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
