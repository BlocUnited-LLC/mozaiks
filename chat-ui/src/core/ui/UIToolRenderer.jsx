import React from 'react';
import { useChatUI } from '../../context/ChatUIContext';
import PrimitiveRenderer from '../../primitives/PrimitiveRenderer';
import { isCoreArtifact } from '../../primitives/utils';

/**
 * UIToolRenderer
 *
 * The OSS-friendly contract here is: chat-ui renders UI tools only if the host
 * provides a renderer function (via ChatUIProvider `uiToolRenderer`).
 *
 * This avoids coupling @mozaiks/chat-ui to any workflow registry implementation.
 */
const UIToolRenderer = ({ event, onResponse, submitInputRequest, className = '', onArtifactAction, actionStatusMap }) => {
  const { uiToolRenderer } = useChatUI();

  if (!event || !event.ui_tool_id) {
    return null;
  }

  const payload = event.payload || {};
  const isCore = isCoreArtifact(payload) || (typeof event.ui_tool_id === 'string' && event.ui_tool_id.startsWith('core.'));
  const hasArtifactType = payload?.artifact_type || payload?.data?.artifact_type;
  const corePayload = isCore && !hasArtifactType
    ? { ...payload, artifact_type: event.ui_tool_id }
    : payload;

  if (typeof uiToolRenderer === 'function') {
    try {
      const rendered = uiToolRenderer(event, onResponse, submitInputRequest, {
        onArtifactAction,
        actionStatusMap,
      });
      if (rendered !== null && rendered !== undefined) {
        return <div className={className}>{rendered}</div>;
      }
    } catch (err) {
      // Do not hard-fail the whole chat UI if a host renderer throws.
      console.error('UIToolRenderer: host uiToolRenderer threw', err);
      if (isCore) {
        return (
          <div className={className}>
            <PrimitiveRenderer
              payload={corePayload}
              onAction={onArtifactAction}
              actionStatusMap={actionStatusMap}
            />
          </div>
        );
      }
      return (
        <div className={className}>
          <div className="text-xs text-[var(--color-warning)]">
            UI tool renderer error: {event.ui_tool_id}
          </div>
        </div>
      );
    }
  }

  if (isCore) {
    return (
      <div className={className}>
        <PrimitiveRenderer
          payload={corePayload}
          onAction={onArtifactAction}
          actionStatusMap={actionStatusMap}
        />
      </div>
    );
  }

  // Default: render nothing (workflow UI tools are an optional integration).
  return null;
};

export default UIToolRenderer;
