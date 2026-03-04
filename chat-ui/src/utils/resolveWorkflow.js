// ==============================================================================
// FILE: chat-ui/src/utils/resolveWorkflow.js
// DESCRIPTION: Single source of truth for "which workflow should we use?"
//
// Priority chain (first non-null wins):
//   1. explicit   — caller-provided name (URL path, resume target, user selection)
//   2. entryPoint — backend-declared entry_point: true in orchestrator.yaml
//   3. singleton  — auto-select when exactly one workflow exists
//   4. null       — no resolution (caller should enter ask-mode or show picker)
// ==============================================================================

import workflowConfig from '../config/workflowConfig';
import { getLoadedWorkflows } from '@chat-workflows/index';

/**
 * Resolve the active workflow name using a deterministic priority chain.
 *
 * @param {string|null|undefined} explicit - Explicit workflow name (URL, resume target, etc.)
 * @returns {string|null} Resolved workflow name, or null if indeterminate.
 */
export default function resolveWorkflow(explicit = null) {
  // 1. Explicit override always wins
  if (explicit) return explicit;

  // 2. Backend-declared entry point
  const entryPoint = workflowConfig.getEntryPointWorkflow();
  if (entryPoint) return entryPoint;

  // 3. Singleton auto-select — if there's exactly one workflow, use it
  const loaded = getLoadedWorkflows();
  if (Array.isArray(loaded) && loaded.length === 1) {
    const name = loaded[0]?.name || loaded[0];
    if (name) return typeof name === 'string' ? name : String(name);
  }

  // Also check backend-fetched configs as a fallback for the singleton case
  const available = workflowConfig.getAvailableWorkflows();
  // Filter out lowercase duplicates (workflowConfig stores both cased + lower aliases)
  const unique = [...new Set(available)];
  if (unique.length === 1) return unique[0];

  // 4. No resolution — caller decides (ask-mode, picker, etc.)
  return null;
}
