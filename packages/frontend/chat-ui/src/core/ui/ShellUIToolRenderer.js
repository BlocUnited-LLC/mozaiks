// ==============================================================================
// FILE: chat-ui/src/core/ui/ShellUIToolRenderer.js
// DESCRIPTION: Shell-specific UI tool renderer that routes through eventDispatcher
// PURPOSE: Composes @mozaiks/chat-ui PrimitiveRenderer with shell's WorkflowUIRouter
// ==============================================================================

import React from 'react';
import { handleEvent } from '../eventDispatcher';
import { PrimitiveRenderer, isCoreArtifact } from '../../primitives';

/**
 * Shell UI Tool Renderer
 * 
 * Shell-specific composition that routes UI tool events through the
 * shell's eventDispatcher/WorkflowUIRouter. For core.* artifact types,
 * delegates to @mozaiks/chat-ui's PrimitiveRenderer.
 * 
 * NOT the same as chat-ui's UIToolRenderer (which is the clean library
 * contract that delegates to a host-injected renderer function).
 */
const ShellUIToolRenderer = ({ 
  event, 
  onResponse,
  submitInputRequest,
  className = "",
  onArtifactAction,
  actionStatusMap
}) => {
  // Validate event structure
  if (!event || !event.ui_tool_id) {
  console.warn('⚠️ UIToolRenderer: Invalid event structure', event);
    return (
      <div className={`ui-tool-error ${className}`}>
        <p className="text-[var(--color-error)]">Invalid UI tool event</p>
      </div>
    );
  }

  try {
    // Prefer explicit agentName when available to attribute UI tools correctly
    const resolvedAgentName = event.agentName || event.agent_name || event.payload?.agentName || event.payload?.agent_name || event.agent || null;
    console.log('🧩 UIToolRenderer: Rendering', {
      ui_tool_id: event.ui_tool_id,
      eventId: event.eventId,
      workflow_name: event.workflow_name,
      agentName: resolvedAgentName,
      payloadKeys: event.payload ? Object.keys(event.payload) : []
    });
    const payload = event.payload || {};
    const isCore = isCoreArtifact(payload) || (typeof event.ui_tool_id === 'string' && event.ui_tool_id.startsWith('core.'));
    const hasArtifactType = payload?.artifact_type || payload?.data?.artifact_type;
    const corePayload = isCore && !hasArtifactType
      ? { ...payload, artifact_type: event.ui_tool_id }
      : payload;
    if (isCore) {
      return (
        <div className={`ui-tool-container ${className}`}>
          <PrimitiveRenderer
            payload={corePayload}
            onAction={onArtifactAction}
            actionStatusMap={actionStatusMap}
          />
        </div>
      );
    }
    // Use the event dispatcher to render the component
    const renderedComponent = handleEvent(event, onResponse, submitInputRequest);

    if (!renderedComponent) {
      console.warn('⚠️ UIToolRenderer: handleEvent returned null; component may be missing');
      return (
        <div className={`ui-tool-not-found ${className}`}>
          <p className="text-[var(--color-warning)] text-slate-200">
            UI tool '{event.ui_tool_id}' not found or failed to load
          </p>
          <p className="text-gray-400 text-sm">
            Check if the workflow is properly registered
          </p>
        </div>
      );
    }

    return (
      <div className={`ui-tool-container ${className}`}>
        {renderedComponent}
      </div>
    );

  } catch (error) {
    console.error('❌ UIToolRenderer: Error rendering UI tool', error);
    return (
      <div className={`ui-tool-error ${className}`}>
        <p className="text-[var(--color-error)]">Error rendering UI tool: {event.ui_tool_id}</p>
        <p className="text-gray-400 text-sm">{error.message}</p>
      </div>
    );
  }
};

export default ShellUIToolRenderer;
