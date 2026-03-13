// ==============================================================================
// FILE: chat-ui/src/@chat-workflows/index.js
// DESCRIPTION: Auto-discovers workflow UI components from platform/workflows/*/ui/index.js
//              No manual registration required — drop a ui/index.js into any
//              platform/workflows/<WorkflowName>/ui/ folder and it appears here.
// ==============================================================================

// Vite glob import — scans platform/workflows/*/ui/index.js at build time.
// Path is relative to this file: ../../../platform/workflows/*/ui/index.js
const uiModules = import.meta.glob(
  '../../../platform/workflows/*/ui/index.{js,jsx}',
  { eager: true }
);

// Build WORKFLOW_REGISTRY from the glob results.
// Key  → workflow name (derived from directory name)
// Value → { components } map from the ui/index.js default or named export
const WORKFLOW_REGISTRY = {};

for (const [modulePath, mod] of Object.entries(uiModules)) {
  // Extract workflow name from path: .../platform/workflows/<Name>/ui/index.js
  const match = modulePath.match(/platform[\\/]workflows[\\/]([^/\\]+)[\\/]ui[\\/]index/);
  if (!match) continue;
  const workflowName = match[1];
  // ui/index.js must export an object of { ComponentName: Component }
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
      registerComponent(componentName, component, {
        description: `${workflowName} workflow component`,
      });
    }
  }
};

const workflowRegistry = { getLoadedWorkflows, getWorkflow, initializeWorkflows };
export default workflowRegistry;

