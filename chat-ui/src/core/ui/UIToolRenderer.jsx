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
const UIToolRenderer = ({ event, onResponse, className = '', onArtifactAction, actionStatusMap }) => {
  const { uiToolRenderer } = useChatUI();

  if (!event || !event.tool_name) {
    return null;
  }

  if (event.response_available === false) {
    return (
      <section className={className} aria-label="Inactive saved review">
        <p role="status">
          This saved review is not active. If the workflow is unfinished, send a message
          in this chat to resume it and request a fresh review. This saved review cannot accept a decision.
        </p>
        <div ref={(element) => { if (element) element.inert = true; }} aria-disabled="true">
          <UIToolRenderer
            event={{...event, response_available: undefined}}
            onResponse={async () => { throw new Error('This saved review is not active.'); }}
            className="opacity-60"
          />
        </div>
      </section>
    );
  }

  const payload = event.payload || {};
  const isCore = isCoreArtifact(payload) || (typeof event.tool_name === 'string' && event.tool_name.startsWith('core.'));
  const hasArtifactType = payload?.artifact_type || payload?.data?.artifact_type;
  const corePayload = isCore && !hasArtifactType
    ? { ...payload, artifact_type: event.tool_name }
    : payload;

  if (typeof uiToolRenderer === 'function') {
    try {
      const rendered = uiToolRenderer(event, onResponse, {
        onArtifactAction,
        actionStatusMap,
      });
      if (rendered !== null && rendered !== undefined) {
        return <div className={className}>{rendered}</div>;
      }
    } catch (err) {
      // Do not hard-fail the whole chat UI if a host renderer throws.
      console.error('❌ UIToolRenderer: host uiToolRenderer threw', err);
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
            UI tool renderer error: {event.tool_name}
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
