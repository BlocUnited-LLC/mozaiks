// ==============================================================================
// FILE: chat-ui/src/core/WorkflowUIRouter.js
// DESCRIPTION: Dynamic router for workflow-specific UI components
// PURPOSE: Dynamically discover and route UI tool events to any workflow
// ==============================================================================

import React from 'react';
import { getComponent } from '../registry/componentRegistry';

function WorkflowUIRenderError({ error, onCancel, toolCallId }) {
  return (
    <div className="bg-[rgba(var(--color-error-rgb),0.2)] border border-[var(--color-error)] rounded p-4">
      <h3 className="text-[var(--color-error)] font-semibold mb-2">Component Render Error</h3>
      <p className="text-[var(--color-error)] text-sm mb-2">
        {error?.message || 'The workflow UI component could not render.'}
      </p>
      <p className="text-xs text-gray-400">Event ID: {toolCallId || 'unknown'}</p>
      <button
        onClick={() => onCancel?.({ status: 'error', error: 'Component render failed' })}
        className="mt-3 px-3 py-1 bg-[var(--color-error)] hover:bg-[var(--color-error)] rounded text-sm"
      >
        Close
      </button>
    </div>
  );
}

class WorkflowUIErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('🚨 [WorkflowUIRouter] Render error:', error);
    console.error('🚨 [WorkflowUIRouter] Component stack:', info?.componentStack || '');
    console.error('🚨 [WorkflowUIRouter] Payload that caused error:', this.props.payload);
  }

  componentDidUpdate(previousProps) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <WorkflowUIRenderError
          error={this.state.error}
          onCancel={this.props.onCancel}
          toolCallId={this.props.toolCallId}
        />
      );
    }
    return this.props.children;
  }
}

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
  toolName,
  toolCallId
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

    try {
      // Deterministic resolution order:
      // 1. workflow-scoped component name
      // 2. workflow-scoped tool_name
      // 3. plain component name
      // 4. plain tool_name
      const candidates = [
        workflow && component ? `${workflow}:${component}` : null,
        workflow && toolName ? `${workflow}:${toolName}` : null,
        component || null,
        toolName || null,
      ].filter(Boolean);

      for (const candidate of candidates) {
        const matched = getComponent(candidate);
        if (matched) {
          setComponent(() => matched);
          return;
        }
      }

      // Fallback to core components (explicit UserInputRequest support)
      try {
        const coreModule = await import('./ui/index.js');
        const coreComponents = coreModule.default || coreModule;
        const coreComponent = coreComponents[component] || coreComponents[toolName];
        if (coreComponent) {
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
        message: `No registered component matched ${workflow}:${component || toolName}`,
      });
    } finally {
      setIsLoading(false);
    }
  }, [toolCallId, toolName]);

  React.useEffect(() => {
    loadWorkflowComponent(sourceWorkflowName, componentType);
  }, [sourceWorkflowName, componentType, toolCallId, loadWorkflowComponent]); // Include toolCallId to reload on new events

  // 🛡️ DEFENSIVE PAYLOAD VALIDATION - After all hooks, before rendering
  if (!payload || typeof payload !== 'object') {
    console.error('🚨 [WorkflowUIRouter] Invalid or missing payload', { 
      payload, 
      payloadType: typeof payload, 
      toolName,
      toolCallId 
    });
    return (
      <div className="bg-[rgba(var(--color-error-rgb),0.2)] border border-[var(--color-error)] rounded p-4">
        <h3 className="text-[var(--color-error)] font-semibold mb-2">Invalid Artifact Data</h3>
        <p className="text-[var(--color-error)] text-sm mb-2">The artifact payload is missing or corrupted.</p>
        <p className="text-xs text-gray-400">Event ID: {toolCallId || 'unknown'}</p>
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

  return (
    <div className="workflow-ui-container">
      <WorkflowUIErrorBoundary
        resetKey={toolCallId || `${sourceWorkflowName}-${componentType}`}
        payload={payload}
        onCancel={onCancel}
        toolCallId={toolCallId}
      >
        {/* CRITICAL: Use toolCallId as key to force remount on new artifact events (prevents state collision on revisions) */}
        {Component && typeof Component === 'function' ? (
          <Component
            key={toolCallId || `${sourceWorkflowName}-${componentType}`}
            payload={payload || {}}
            onResponse={onResponse}
            onCancel={onCancel}
            toolName={toolName}
            toolCallId={toolCallId}
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
      </WorkflowUIErrorBoundary>
    </div>
  );
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
 *    const YourComponent = ({ payload, onResponse, onCancel, toolName, toolCallId }) => {
 *      // Your component logic
 *    };
 * 
 * ✨ Deterministic resolution: workflow-scoped key first (`Workflow:Component`)
 * No per-workflow dynamic import paths required.
 * ✨ Fully modular: each workflow is self-contained behind `ui/index.js`.
 */
