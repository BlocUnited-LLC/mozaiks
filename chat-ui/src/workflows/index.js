// ==============================================================================
// FILE: chat-ui/src/workflows/index.js
// DESCRIPTION: Workflow registry — import and register all workflow UI packages.
//              Add a new workflow by importing its components and adding an entry
//              to WORKFLOW_REGISTRY below.
// ==============================================================================

import HelloWorldComponents from './HelloWorld/components';

/**
 * Registry of all available workflows.
 * Key   → workflow_name as returned by the backend /api/workflows endpoint
 * Value → { components } map (component name → React component)
 */
const WORKFLOW_REGISTRY = {
  HelloWorld: {
    components: HelloWorldComponents,
  },
};

/**
 * Return all registered workflow names.
 * @returns {string[]}
 */
export const getLoadedWorkflows = () => Object.keys(WORKFLOW_REGISTRY);

/**
 * Return the registration entry for a single workflow.
 * @param {string} workflowName
 * @returns {{ components: Object } | null}
 */
export const getWorkflow = (workflowName) =>
  WORKFLOW_REGISTRY[workflowName] ?? null;

/**
 * Register workflow components into the ChatUI ComponentRegistry.
 * Called once at app startup.
 * @param {Function} registerComponent - from @mozaiks/chat-ui/registry
 */
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
