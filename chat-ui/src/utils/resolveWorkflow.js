// ==============================================================================
// FILE: chat-ui/src/utils/resolveWorkflow.js
// DESCRIPTION: Single source of truth for "which workflow should we use?"
//
// Priority chain (first non-null wins):
//   1. explicit   — caller-provided name (URL path, resume target, user selection)
//   2. entryPoint — backend-declared entry_point projected from platform/config/ai.json
//   3. singleton  — auto-select when exactly one workflow exists
//   4. null       — no resolution (caller should enter ask-mode or show picker)
// ==============================================================================

import workflowConfig from '../config/workflowConfig';
const NON_RUNNABLE_IDS = new Set(['extended_orchestration']);

/**
 * Resolve the active workflow name using a deterministic priority chain.
 *
 * @param {string|null|undefined} explicit - Explicit workflow name (URL, resume target, etc.)
 * @returns {string|null} Resolved workflow name, or null if indeterminate.
 */
export default function resolveWorkflow(explicit = null) {
  // 1. Explicit override always wins
  const canonicalExplicit = workflowConfig.resolveKnownWorkflowName(explicit);
  if (canonicalExplicit) return canonicalExplicit;

  if (explicit) {
    const trimmed = String(explicit).trim();
    const lowered = trimmed.toLowerCase();
    if (
      trimmed
      && lowered !== 'workflow'
      && lowered !== 'workflows'
      && !NON_RUNNABLE_IDS.has(lowered)
    ) {
      return trimmed;
    }
  }

  // 2. Backend-declared entry point
  const entryPoint = workflowConfig.getEntryPointWorkflow();
  if (entryPoint) return entryPoint;

  // 3. Singleton auto-select from backend-fetched workflows only.
  const available = workflowConfig.getAvailableWorkflows();
  if (available.length === 1) return available[0];

  // 4. No resolution — caller decides (ask-mode, picker, etc.)
  return null;
}
