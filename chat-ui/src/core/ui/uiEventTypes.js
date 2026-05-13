// ==============================================================================
// FILE: chat-ui/src/core/ui/uiEventTypes.js
// DESCRIPTION: Typed event contract for the ui.* WebSocket event namespace.
//
// These constants are the frontend side of the L2 event contract defined on the
// Python side in mozaiksai/core/transport/ui_events.py.  Keep both files in
// sync whenever the contract changes.
// ==============================================================================

// ---------------------------------------------------------------------------
// Outbound event types (Python → frontend)
// ---------------------------------------------------------------------------

/** Render a named UI component inside the chat stream or artifact panel. */
export const UI_RENDER = 'ui.render';

/** Patch an already-rendered component's payload without re-mounting it. */
export const UI_UPDATE = 'ui.update';

/** Tear down a rendered component. */
export const UI_DISMISS = 'ui.dismiss';

// ---------------------------------------------------------------------------
// Inbound event types (frontend → backend) — unchanged from the legacy protocol
// ---------------------------------------------------------------------------

/** User interaction response, correlated to a ui.render event by tool_call_id. */
export const TOOL_CALL_RESPONSE = 'tool_call_response';

// ---------------------------------------------------------------------------
// Display modes — must match UIDisplayMode in ui_events.py
// ---------------------------------------------------------------------------

export const UI_DISPLAY_MODES = /** @type {const} */ ({
  INLINE: 'inline',
  ARTIFACT: 'artifact',
});

// ---------------------------------------------------------------------------
// Canonical primitive names
// Extend this list as L1 primitives are added.
// ---------------------------------------------------------------------------

export const UI_PRIMITIVES = /** @type {const} */ ({
  // Core interaction primitives
  USER_INPUT_REQUEST: 'UserInputRequest',
  APPROVAL_CARD: 'ApprovalCard',
  CHOICE_PICKER: 'ChoicePicker',
  FORM_CARD: 'FormCard',
  CONFIRMATION_SUMMARY: 'ConfirmationSummary',

  // Display primitives
  DATA_TABLE: 'DataTable',
  TIMELINE: 'Timeline',
  FILE_LIST: 'FileList',
  CODE_BLOCK: 'CodeBlock',
  PROGRESS_TRACKER: 'ProgressTracker',
  ALERT_BANNER: 'AlertBanner',
  ACTION_BUTTON: 'ActionButton',
});

// ---------------------------------------------------------------------------
// Helper: parse a ui.render WebSocket event into a normalised render spec
// ---------------------------------------------------------------------------

/**
 * Parse the raw WebSocket data object for a ``ui.render`` event into a
 * normalised spec the frontend can pass to WorkflowUIRouter / notifyUIUpdate.
 *
 * @param {object} data  The ``data`` field of the WebSocket envelope.
 * @param {string} [fallbackWorkflow]  Workflow name from session context.
 * @returns {{
 *   toolCallId: string|null,
 *   component: string,
 *   displayMode: string,
 *   awaitingResponse: boolean,
 *   workflow: string,
 *   agent: string|null,
 *   payload: object,
 *   interactionType: string,
 *   toolName: string,
 * }}
 */
export function parseUIRenderEvent(data, fallbackWorkflow = '') {
  const inner = (data && data.data) ? data.data : (data || {});
  return {
    toolCallId: inner.tool_call_id || null,
    component: inner.component || inner.component_type || '',
    displayMode: inner.display_mode || inner.display || UI_DISPLAY_MODES.INLINE,
    awaitingResponse: inner.awaiting_response !== false,
    workflow: inner.workflow || inner.workflow_name || fallbackWorkflow,
    agent: inner.agent || inner.agent_name || null,
    payload: inner.payload || {},
    interactionType: inner.interaction_type || 'ui_tool',
    toolName: inner.tool_name || inner.component || '',
  };
}
