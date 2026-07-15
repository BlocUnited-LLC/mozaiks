import { createElement } from 'react';
import WorkflowUIRouter from './WorkflowUIRouter';

class EventDispatcher {
  constructor() {
    this.activeEvents = new Map();
  }

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
        console.error('EventDispatcher: Missing tool_name in event', event);
        return null;
      }

      const workflowName = workflow_name || payload.workflow_name || payload.workflow || 'Unknown';
      const componentType = component_type || payload.component_type || toolName;

      if (toolCallId) {
        this.activeEvents.set(toolCallId, {
          tool_name: toolName,
          payload,
          workflowName,
          componentType,
          startTime: Date.now(),
          status: 'active',
        });
      }

      const responseHandler = async (response) => {
        if (toolCallId && this.activeEvents.has(toolCallId)) {
          const activeEvent = this.activeEvents.get(toolCallId);
          activeEvent.status = 'completed';
          activeEvent.endTime = Date.now();
          activeEvent.response = response;
        }
        if (onResponse) {
          return await onResponse(response);
        }
        return undefined;
      };

      const cancelHandler = (reason) => {
        if (toolCallId && this.activeEvents.has(toolCallId)) {
          const activeEvent = this.activeEvents.get(toolCallId);
          activeEvent.status = 'cancelled';
          activeEvent.endTime = Date.now();
          activeEvent.cancelReason = reason;
        }
      };

      return createElement(WorkflowUIRouter, {
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
      console.error('EventDispatcher: Error handling event', error);
      return this._renderErrorComponent(event?.tool_name || event?.component_type, error.message);
    }
  }

  _renderErrorComponent(toolName, errorMessage) {
    return createElement('div', {
      className: 'ui-tool-error bg-destructive/20 text-destructive border border-destructive/50 rounded-lg p-4',
    }, [
      createElement('h4', { key: 'title' }, `UI Tool Error: ${toolName}`),
      createElement('p', { key: 'message' }, errorMessage),
      createElement('small', { key: 'help' }, 'Check console for more details.'),
    ]);
  }
}

const eventDispatcher = new EventDispatcher();

export default eventDispatcher;

export const handleEvent = (event, onResponse) =>
  eventDispatcher.handleEvent(event, onResponse);
