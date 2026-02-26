/**
 * Workflow registry
 *
 * This is the @chat-workflows entry point (aliased in vite.config.js).
 *
 * To add a workflow:
 *   1. Create a folder: workflows/my_workflow/
 *   2. Add index.js (see hello_world for the shape)
 *   3. Add your artifact component
 *   4. Import and register it here
 *
 * The `name` field in each workflow must match the workflow name your
 * backend uses in orchestrator.yaml.
 */

import helloWorld from './hello_world';

// ─── Register your workflows here ───────────────────────────────────────────
const workflows = {
  [helloWorld.name]: helloWorld,

  // my_workflow: myWorkflow,
  // content_calendar: contentCalendarWorkflow,
};

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns an array of workflow objects (each has a `.name` field).
 * ChatPage uses `getLoadedWorkflows()[0].name` to pick the default workflow
 * when no explicit workflow is provided via URL or config.
 */
export function getLoadedWorkflows() {
  return Object.values(workflows);
}

export function getWorkflow(name) {
  return workflows[name] || null;
}

export function initializeWorkflows() {
  console.log('✅ Workflows registered:', getLoadedWorkflows().join(', ') || 'none');
}

export default workflows;
