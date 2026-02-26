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
   * Handle a UI tool event from the backend
   * @param {Object} event - Event object with ui_tool_id and payload
   * @param {Function} onResponse - Callback to send response back to backend
   * @param {Function} submitInputRequest - WebSocket input submission function (F5)
   * @returns {React.Element|null} - Rendered component or null
   */
  handleEvent(event, onResponse = null, submitInputRequest = null) {
    try {
      const { ui_tool_id, payload = {}, eventId, workflow_name } = event;

      if (!ui_tool_id) {
        console.error('❌ EventDispatcher: Missing ui_tool_id in event', event);
        return null;
      }

      console.log(`🎯 EventDispatcher: Routing event to WorkflowUIRouter for '${ui_tool_id}'`);

  // Extract workflow and component info from the payload
  // Backend should send: { workflow_name: "<Workflow>", component_type: "<ComponentName>", ... }
      const workflowName = workflow_name || payload.workflow_name || payload.workflow || 'Unknown';
      const componentType = payload.component_type || ui_tool_id;

      // Track this active event
      if (eventId) {
        this.activeEvents.set(eventId, {
          ui_tool_id,
          payload,
          workflowName,
          componentType,
          startTime: Date.now(),
          status: 'active'
        });
      }

      // Add to event history
      this.eventHistory.push({
        ui_tool_id,
        eventId,
        workflowName,
        componentType,
        timestamp: new Date().toISOString(),
        status: 'routed'
      });

      // Create response handler
      const responseHandler = (response) => {
        console.log(`📤 EventDispatcher: Response for tool '${ui_tool_id}'`, response);
        
        // Update active event status
        if (eventId && this.activeEvents.has(eventId)) {
          const activeEvent = this.activeEvents.get(eventId);
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
        console.log(`❌ EventDispatcher: Cancelled tool '${ui_tool_id}'`, reason);
        
        // Update active event status
        if (eventId && this.activeEvents.has(eventId)) {
          const activeEvent = this.activeEvents.get(eventId);
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
          component_type: componentType
        },
        onResponse: responseHandler,
        onCancel: cancelHandler,
        submitInputRequest,
        ui_tool_id,
        eventId
      });

    } catch (error) {
      console.error('❌ EventDispatcher: Error handling event', error);
      return this.renderErrorComponent(event?.ui_tool_id, error.message);
    }
  }

  /**
   * Render an error component when tool loading fails
   * @param {string} ui_tool_id - The tool that failed to load
   * @param {string} errorMessage - Error description
   * @returns {React.Element} - Error component
   */
  renderErrorComponent(ui_tool_id, errorMessage) {
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
      React.createElement('h4', { key: 'title' }, `UI Tool Error: ${ui_tool_id}`),
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
    for (const [eventId, event] of this.activeEvents) {
      if (event.status === 'completed' || event.status === 'cancelled') {
        this.activeEvents.delete(eventId);
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

export const handleEvent = (event, onResponse, submitInputRequest) => 
  eventDispatcher.handleEvent(event, onResponse, submitInputRequest);

export const registerEventHandler = (eventType, handler) =>
  eventDispatcher.registerEventHandler(eventType, handler);

export const getActiveEvents = () =>
  eventDispatcher.getActiveEvents();

export const getEventHistory = () =>
  eventDispatcher.getEventHistory();
