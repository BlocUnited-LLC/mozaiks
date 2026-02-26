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
 * 2. Dynamically imports the workflow's components  
 * 3. Renders the specific component for that workflow
 * 4. Handles responses back to the agent
 * 
 * NO HARDCODED WORKFLOWS - Completely modular and discoverable!
 */

// Cache for loaded workflow component modules
const componentCache = new Map();

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
   * Dynamically load workflow component - NO HARDCODING
   */
  const loadWorkflowComponent = React.useCallback(async (workflow, component) => {
    try {
      setIsLoading(true);
      setError(null);
      console.log('🛰️ WorkflowUIRouter: Loading component', { workflow, component });
      if (workflow === 'core' || workflow === 'shell') {
        const registryComponent = getComponent(component) || getComponent(ui_tool_id);
        if (registryComponent) {
          console.log(`✅ WorkflowUIRouter: Using registered component ${component || ui_tool_id}`);
          setComponent(() => registryComponent);
          setIsLoading(false);
          return;
        }
      }
      // Derive chat-specific cache key (include cache_seed AND eventId to prevent collision on revisions)
      let chatId = null;
      try { chatId = localStorage.getItem('mozaiks.current_chat_id'); } catch {}
      let cacheSeed = null;
      if (chatId) {
        try { const storedSeed = localStorage.getItem(`mozaiks.current_chat_id.cache_seed.${chatId}`); if (storedSeed) cacheSeed = storedSeed; } catch {}
      }
      // CRITICAL: Include eventId in cache key to prevent collision when revisions arrive with new eventId
      const cacheKey = `${chatId || 'nochat'}:${cacheSeed || 'noseed'}:${workflow}:${component}:${eventId || 'no-event'}`;
      
      // Check cache first
      if (componentCache.has(cacheKey)) {
        console.log('🛰️ WorkflowUIRouter: Cache hit', { cacheKey });
        setComponent(() => componentCache.get(cacheKey));
        setIsLoading(false);
        return;
      }

      // Dynamically import the workflow's component index
      // Uses @chat-workflows alias (resolves to MOZAIKS_FRONTEND_WORKFLOWS_PATH or local stub)
      const workflowModule = await import(`@chat-workflows/${workflow}/components/index.js`);
      
      // Get the specific component from the workflow module
      const WorkflowComponent = workflowModule.default[component] || workflowModule[component];
      
      if (!WorkflowComponent) {
        throw new Error(`Component '${component}' not found in workflow '${workflow}'`);
      }
      
      // Cache the component
  componentCache.set(cacheKey, WorkflowComponent);
      setComponent(() => WorkflowComponent);
      
      console.log(`✅ WorkflowUIRouter: Loaded ${workflow}:${component}`);
      
    } catch (loadError) {
      console.warn(`⚠️ WorkflowUIRouter: Failed to load ${workflow}:${component}, trying core components`, loadError);
      
      // Fallback to core components (F5: UserInputRequest support)
      try {
        const coreModule = await import('./ui/index.js');
        const coreComponents = {
          'UserInputRequest': coreModule.UserInputRequest,
          'user_input': coreModule.UserInputRequest, // Map user_input to UserInputRequest
        };
        
        const coreComponent = coreComponents[component] || coreComponents[ui_tool_id];
        if (coreComponent) {
          console.log(`✅ WorkflowUIRouter: Using core component ${component || ui_tool_id}`);
          setComponent(() => coreComponent);
          setIsLoading(false);
          return;
        }
      } catch (coreError) {
        console.warn(`⚠️ WorkflowUIRouter: Failed to load core components`, coreError);
      }

      // Final fallback: registered core chat-ui components
      try {
        const registryComponent = getComponent(component) || getComponent(ui_tool_id);
        if (registryComponent) {
          console.log(`✅ WorkflowUIRouter: Using registered component ${component || ui_tool_id}`);
          setComponent(() => registryComponent);
          setIsLoading(false);
          return;
        }
      } catch (registryError) {
        console.warn(`⚠️ WorkflowUIRouter: Failed to load registered components`, registryError);
      }
      
      // No fallback found
      setError({
        type: 'component_not_found',
        workflow,
        component,
        message: loadError.message
      });
    } finally {
      setIsLoading(false);
    }
  }, [eventId, ui_tool_id]); // Include both eventId and ui_tool_id in dependencies to trigger reload on new artifact events

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
            workflows/{error.workflow}/components/index.js<br/>
            ↳ export {'{'}  {error.component} {'}'};
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
            // Compatibility prop for older components
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
 * 🎯 WORKFLOW INTEGRATION GUIDE - NEW DYNAMIC SYSTEM
 * 
 * To add UI components for a new workflow (NO HARDCODING NEEDED):
 * 
 * 1. CREATE WORKFLOW COMPONENTS DIRECTORY:
 *    workflows/YourWorkflow/components/
 *    ├── YourComponent.js
 *    ├── AnotherComponent.js
 *    └── index.js
 * 
 * 2. CREATE COMPONENTS INDEX FILE:
 *    // workflows/YourWorkflow/components/index.js
 *    import YourComponent from './YourComponent';
 *    import AnotherComponent from './AnotherComponent';
 *    
 *    export {
 *      YourComponent,
 *      AnotherComponent
 *    };
 * 
 * 3. CREATE WORKFLOW UI TOOL (Backend):
 *    // workflows/YourWorkflow/tools/your_tool.py
 *    class YourUITool(WorkflowUITool):
 *      def __init__(self):
 *        super().__init__("YourWorkflow", "your_tool", "YourComponent")
 * 
 * 4. COMPONENT RECEIVES STANDARD PROPS:
 *    const YourComponent = ({ payload, onResponse, onCancel, ui_tool_id, eventId }) => {
 *      // Your component logic
 *    };
 * 
 * ✨ MAGIC: The router automatically discovers and loads your components!
 * ✨ NO REGISTRATION: No need to modify any core files!
 * ✨ FULLY MODULAR: Each workflow is completely self-contained!
 */
