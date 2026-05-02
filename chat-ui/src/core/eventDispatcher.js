// ==============================================================================
// FILE: chat-ui/src/core/eventDispatcher.js
// DESCRIPTION: Handles UI tool events using the new dynamic WorkflowUIRouter
// PURPOSE: Clean event dispatching without old registry system
// ==============================================================================

import React from 'react';
import WorkflowUIRouter from './WorkflowUIRouter';

/**
 * 🎯 EVENT DISPATCHER - CLEAN VERSION
 * 
 * Receives events from backend and uses the new WorkflowUIRouter
 * to dynamically load and render the appropriate UI component.
 * 
 * NO MORE GLOBAL REGISTRY - Uses dynamic component discovery!
 */

class EventDispatcher {
  constructor() {
    this.activeEvents = new Map(); // Track active UI events
    this.eventHistory = []; // Keep history for debugging
    this.eventHandlers = new Map(); // Custom event handlers
  }

  /**
 * Handle a workflow UI tool_call event from the backend
 * @param {Object} event - Canonical tool_call render event
 * @param {Function} onResponse - Callback to send response back to backend
 * @returns {React.Element|null} - Rendered component or null
 */
  handleEvent(event, onResponse = null) {
    try {
      const {
        tool_name,
        payload = {},
        tool_call_id,
        workflow_name,
        component_type,
      } = event;
      const toolName = tool_name || payload.tool_name || component_type;
      const toolCallId = tool_call_id || payload.tool_call_id || event?.corr || null;

      if (!toolName) {
        console.error('❌ EventDispatcher: Missing tool_name in event', event);
        return null;
      }

      console.log(`🎯 EventDispatcher: Routing event to WorkflowUIRouter for '${toolName}'`);

  // Extract workflow and component info from the payload
  // Backend should send: { workflow_name: "<Workflow>", component_type: "<ComponentName>", ... }
      const workflowName = workflow_name || payload.workflow_name || payload.workflow || 'Unknown';
      const componentType = component_type || payload.component_type || toolName;

      // Track this active event
      if (toolCallId) {
        this.activeEvents.set(toolCallId, {
          tool_name: toolName,
          payload,
          workflowName,
          componentType,
          startTime: Date.now(),
          status: 'active'
        });
      }

      // Add to event history
      this.eventHistory.push({
        tool_name: toolName,
        tool_call_id: toolCallId,
        workflowName,
        componentType,
        timestamp: new Date().toISOString(),
        status: 'routed'
      });

      // Create response handler
      const responseHandler = (response) => {
        console.log(`📤 EventDispatcher: Response for tool '${toolName}'`, response);
        
        // Update active event status
        if (toolCallId && this.activeEvents.has(toolCallId)) {
          const activeEvent = this.activeEvents.get(toolCallId);
          activeEvent.status = 'completed';
          activeEvent.endTime = Date.now();
          activeEvent.response = response;
        }

        // Call the original response handler
        if (onResponse) {
          onResponse(response);
        }
      };

      // Create cancel handler
      const cancelHandler = (reason) => {
        console.log(`❌ EventDispatcher: Cancelled tool '${toolName}'`, reason);
        
        // Update active event status
        if (toolCallId && this.activeEvents.has(toolCallId)) {
          const activeEvent = this.activeEvents.get(toolCallId);
          activeEvent.status = 'cancelled';
          activeEvent.endTime = Date.now();
          activeEvent.cancelReason = reason;
        }
      };

      // Use the new WorkflowUIRouter to handle the event
      return React.createElement(WorkflowUIRouter, {
        payload: {
          ...payload,
          workflow_name: workflowName,
          component_type: componentType,
        },
        onResponse: responseHandler,
        onCancel: cancelHandler,
        toolName,
        toolCallId,
      });

    } catch (error) {
      console.error('❌ EventDispatcher: Error handling event', error);
      return this.renderErrorComponent(event?.tool_name || event?.component_type, error.message);
    }
  }

  /**
   * Render an error component when tool loading fails
   * @param {string} toolName - The tool that failed to load
   * @param {string} errorMessage - Error description
   * @returns {React.Element} - Error component
   */
  renderErrorComponent(toolName, errorMessage) {
    return React.createElement('div', {
      className: 'ui-tool-error',
      style: {
        padding: '16px',
        border: '1px solid #ef4444',
        borderRadius: '8px',
        backgroundColor: '#fef2f2',
        color: '#dc2626'
      }
    }, [
      React.createElement('h4', { key: 'title' }, `UI Tool Error: ${toolName}`),
      React.createElement('p', { key: 'message' }, errorMessage),
      React.createElement('small', { key: 'help' }, 'Check console for more details.')
    ]);
  }

  /**
   * Register a custom event handler for specific event types
   * @param {string} eventType - Type of event to handle
   * @param {Function} handler - Handler function
   */
  registerEventHandler(eventType, handler) {
    this.eventHandlers.set(eventType, handler);
    console.log(`📝 EventDispatcher: Registered handler for event type '${eventType}'`);
  }

  /**
   * Get active events (useful for debugging)
   * @returns {Object} - Map of active events
   */
  getActiveEvents() {
    return Object.fromEntries(this.activeEvents);
  }

  /**
   * Get event history
   * @returns {Array} - Array of handled events
   */
  getEventHistory() {
    return [...this.eventHistory];
  }

  /**
   * Clear completed events from active tracking
   */
  cleanupCompletedEvents() {
    let cleaned = 0;
    for (const [toolCallId, event] of this.activeEvents) {
      if (event.status === 'completed' || event.status === 'cancelled') {
        this.activeEvents.delete(toolCallId);
        cleaned++;
      }
    }
    if (cleaned > 0) {
      console.log(`🧹 EventDispatcher: Cleaned up ${cleaned} completed events`);
    }
  }

  /**
   * Get dispatcher statistics
   * @returns {Object} - Dispatcher stats
   */
  getStats() {
    return {
      activeEvents: this.activeEvents.size,
      totalEventsHandled: this.eventHistory.length,
      customHandlers: this.eventHandlers.size
    };
  }
}

// Create singleton instance
const eventDispatcher = new EventDispatcher();

// Export both the instance and the main handler for convenience
export default eventDispatcher;

export const handleEvent = (event, onResponse) => 
  eventDispatcher.handleEvent(event, onResponse);

export const registerEventHandler = (eventType, handler) =>
  eventDispatcher.registerEventHandler(eventType, handler);

export const getActiveEvents = () =>
  eventDispatcher.getActiveEvents();

export const getEventHistory = () =>
  eventDispatcher.getEventHistory();
