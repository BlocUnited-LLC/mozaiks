/**
 * Workflow auto-registry — you do not need to edit this file.
 *
 * Every subfolder that contains an index.js with a default export
 * that has a `name` field is automatically picked up and registered.
 *
 * To add a workflow: create a new folder under workflows/ and add
 * an index.js that follows the hello_world shape.
 */

const modules = import.meta.glob('./*/index.js', { eager: true });

const workflows = {};
for (const [, mod] of Object.entries(modules)) {
  const w = mod.default;
  if (w?.name) workflows[w.name] = w;
}

export function getLoadedWorkflows() { return Object.values(workflows); }
export function getWorkflow(name)    { return workflows[name] || null; }
export function initializeWorkflows() {
  const names = getLoadedWorkflows().map(w => w.name);
  if (names.length) console.log('✅ [workflows] Registered:', names.join(', '));
  else              console.warn('⚠️ [workflows] No workflow folders found in workflows/');
}

export default workflows;

