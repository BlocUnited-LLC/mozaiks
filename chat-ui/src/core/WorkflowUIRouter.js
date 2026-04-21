// ==============================================================================
// FILE: chat-ui/src/core/WorkflowUIRouter.js
// DESCRIPTION: Dynamic router for workflow-specific UI components
// PURPOSE: Dynamically discover and route UI tool events to any workflow
// ==============================================================================

import React from 'react';
import { getComponent } from '../registry/componentRegistry';

/**
 * 🎯 WORKFLOW UI ROUTER - TRULY MODULAR
 * 
 * This core system dynamically discovers and routes UI tool events to the 
 * correct workflow-specific components without hardcoding any workflows.
 * 
 * DYNAMIC ARCHITECTURE:
 * 1. Receives UI tool event with workflow_name and component_type
 * 2. Resolves a registered component from the workflow registry
 * 3. Renders the specific component for that workflow
 * 4. Handles responses back to the agent
 * 
 * NO HARDCODED WORKFLOWS - Completely modular and discoverable!
 */
const WorkflowUIRouter = ({ 
  payload, 
  onResponse, 
  onCancel,
  submitInputRequest,
  ui_tool_id,
  eventId
}) => {
  // 🎯 ALL HOOKS MUST BE AT THE TOP - React Hooks Rules Compliance
  const [Component, setComponent] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [isLoading, setIsLoading] = React.useState(true);

  // Extract routing information from payload (with safe defaults for validation below)
  // CRITICAL: 
  // - payload.workflow_name = source workflow that generated this UI (e.g., "Generator")
  // - payload.workflow = metadata object about the generated user workflow (e.g., {name: "Marketing Automation", ...})
  // Use workflow_name for component loading, workflow.name for display only
  const sourceWorkflowName = payload?.workflow_name || 'Unknown';
  const generatedWorkflowName = payload?.workflow?.name || null;
  const componentType = payload?.component_type || 'UnknownComponent';
  
  /**
   * Resolve workflow component deterministically from the registry.
   */
  const loadWorkflowComponent = React.useCallback(async (workflow, component) => {
    setIsLoading(true);
    setError(null);
    console.log('🛰️ WorkflowUIRouter: Loading component', { workflow, component, ui_tool_id, eventId });

    try {
      // Deterministic resolution order:
      // 1. workflow-scoped component name
      // 2. workflow-scoped ui_tool_id (legacy payload fallback)
      // 3. plain component name
      // 4. plain ui_tool_id
      const candidates = [
        workflow && component ? `${workflow}:${component}` : null,
        workflow && ui_tool_id ? `${workflow}:${ui_tool_id}` : null,
        component || null,
        ui_tool_id || null,
      ].filter(Boolean);

      for (const candidate of candidates) {
        const matched = getComponent(candidate);
        if (matched) {
          console.log('✅ WorkflowUIRouter: Using registered component', {
            resolved: candidate,
            requested_component: component,
            requested_tool: ui_tool_id,
            workflow,
          });
          setComponent(() => matched);
          return;
        }
      }

      // Fallback to core components (UserInputRequest support)
      try {
        const coreModule = await import('./ui/index.js');
        const coreComponents = {
          UserInputRequest: coreModule.UserInputRequest,
          user_input: coreModule.UserInputRequest,
        };
        const coreComponent = coreComponents[component] || coreComponents[ui_tool_id];
        if (coreComponent) {
          console.log(`✅ WorkflowUIRouter: Using core component ${component || ui_tool_id}`);
          setComponent(() => coreComponent);
          return;
        }
      } catch (coreError) {
        console.warn('⚠️ WorkflowUIRouter: Failed to load core fallback components', coreError);
      }

      setError({
        type: 'component_not_found',
        workflow,
        component,
        message: `No registered component matched ${workflow}:${component || ui_tool_id}`,
      });
    } finally {
      setIsLoading(false);
    }
  }, [eventId, ui_tool_id]);

  React.useEffect(() => {
    loadWorkflowComponent(sourceWorkflowName, componentType);
  }, [sourceWorkflowName, componentType, eventId, loadWorkflowComponent]); // Include eventId to reload on new events

  // 🛡️ DEFENSIVE PAYLOAD VALIDATION - After all hooks, before rendering
  if (!payload || typeof payload !== 'object') {
    console.error('🚨 [WorkflowUIRouter] Invalid or missing payload', { 
      payload, 
      payloadType: typeof payload, 
      ui_tool_id,
      eventId 
    });
    return (
      <div className="bg-[rgba(var(--color-error-rgb),0.2)] border border-[var(--color-error)] rounded p-4">
        <h3 className="text-[var(--color-error)] font-semibold mb-2">Invalid Artifact Data</h3>
        <p className="text-[var(--color-error)] text-sm mb-2">The artifact payload is missing or corrupted.</p>
        <p className="text-xs text-gray-400">Event ID: {eventId || 'unknown'}</p>
        <button 
          onClick={() => onCancel?.({ status: 'error', error: 'Invalid payload' })}
          className="mt-3 px-3 py-1 bg-[var(--color-error)] hover:bg-[var(--color-error)] rounded text-sm"
        >
          Close
        </button>
      </div>
    );
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="workflow-ui-loading p-4 bg-gray-800 border border-gray-600 rounded">
        <div className="flex items-center space-x-2">
          <div className="animate-spin h-4 w-4 border-2 border-[var(--color-primary)] border-t-transparent rounded-full"></div>
          <span className="text-gray-300">Loading {sourceWorkflowName}:{componentType}...</span>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
  <div className="workflow-ui-error bg-[rgba(var(--color-error-rgb),0.2)] border border-[var(--color-error)] rounded p-4">
        <h3 className="text-[var(--color-error)] font-semibold mb-2">UI Component Error</h3>
        <p className="text-[var(--color-error)] text-sm mb-2">
          Could not load component <code>{error.component}</code> from workflow <code>{error.workflow}</code>
        </p>
        <p className="text-[var(--color-error)] text-xs mb-3">{error.message}</p>
        
  <div className="text-[var(--color-warning)] text-slate-200 text-xs mb-3">
          <p><strong>Expected structure:</strong></p>
          <code className="block bg-gray-800 p-2 rounded text-xs">
            workflows/{error.workflow}/ui/index.js<br/>
            ↳ export {'{'} {error.component} {'}'}; (auto-registered as {error.workflow}:{error.component})
          </code>
        </div>
        
        <button 
          onClick={() => onCancel?.({ status: 'error', error: 'Component not found' })}
          className="px-3 py-1 bg-[var(--color-error)] hover:bg-[var(--color-error)] rounded text-sm"
        >
          Close
        </button>
      </div>
    );
  }

  // Success state - render the dynamically loaded component
  console.log('🛰️ WorkflowUIRouter: Rendering state:', {
    Component: Component ? 'loaded' : 'null',
    ComponentType: typeof Component,
    payload: payload ? 'present' : 'null',
    payloadType: typeof payload,
    payloadKeys: payload ? Object.keys(payload) : []
  });

  // CRITICAL: Verify payload structure before passing to component
  if (payload && typeof payload === 'object') {
    console.log('🔍 [WorkflowUIRouter] Payload structure check:', {
      hasWorkflow: 'workflow' in payload,
      workflowType: typeof payload.workflow,
      payloadSample: JSON.stringify(payload, null, 2).substring(0, 500)
    });
  }

  // Render with error boundary
  try {
    return (
      <div className="workflow-ui-container">
        
        {/* Render the dynamically loaded workflow component */}
        {/* CRITICAL: Use eventId as key to force remount on new artifact events (prevents state collision on revisions) */}
        {Component && typeof Component === 'function' ? (
          <Component
            key={eventId || `${sourceWorkflowName}-${componentType}`}
            payload={payload || {}}
            onResponse={onResponse}
            onCancel={onCancel}
            submitInputRequest={submitInputRequest}
            ui_tool_id={ui_tool_id}
            eventId={eventId}
            sourceWorkflowName={sourceWorkflowName}
            generatedWorkflowName={generatedWorkflowName}
            // Shared workflow identifier prop for UI components
            workflowName={generatedWorkflowName || sourceWorkflowName}
            componentId={componentType}
          />
        ) : (
          <div className="text-[var(--color-warning)] text-slate-200 p-4">
            <p>Component not ready: {Component ? typeof Component : 'null'}</p>
          </div>
        )}
      </div>
    );
  } catch (renderError) {
    console.error('🚨 [WorkflowUIRouter] Render error:', renderError);
    console.error('🚨 [WorkflowUIRouter] Payload that caused error:', payload);
    return (
  <div className="bg-[rgba(var(--color-error-rgb),0.2)] border border-[var(--color-error)] rounded p-4">
        <h3 className="text-[var(--color-error)] font-semibold mb-2">Component Render Error</h3>
        <p className="text-[var(--color-error)] text-sm mb-2">{renderError.message}</p>
        <p className="text-xs text-gray-400">Check console for details</p>
      </div>
    );
  }
};

export default WorkflowUIRouter;

/**
 * 🎯 WORKFLOW INTEGRATION GUIDE - DETERMINISTIC SYSTEM
 * 
 * To add UI components for a new workflow (NO HARDCODING NEEDED):
 * 
 * 1. CREATE WORKFLOW UI DIRECTORY:
 *    workflows/YourWorkflow/ui/
 *    ├── YourComponent.js
 *    ├── AnotherComponent.js
 *    └── index.js
 * 
 * 2. CREATE UI INDEX FILE:
 *    // workflows/YourWorkflow/ui/index.js
 *    import YourComponent from './YourComponent';
 *    import AnotherComponent from './AnotherComponent';
 *    
 *    export {
 *      YourComponent,
 *      AnotherComponent
 *    };
 * 
 * 3. DECLARE UI TOOL/SURFACE IN tools.yaml:
 *    ui:
 *      component: YourComponent
 *      mode: inline|artifact
 * 
 * 4. COMPONENT RECEIVES STANDARD PROPS:
 *    const YourComponent = ({ payload, onResponse, onCancel, ui_tool_id, eventId }) => {
 *      // Your component logic
 *    };
 * 
 * ✨ Deterministic resolution: workflow-scoped key first (`Workflow:Component`)
 * ✨ No legacy per-workflow dynamic import paths required.
 * ✨ Fully modular: each workflow is self-contained behind `ui/index.js`.
 */
