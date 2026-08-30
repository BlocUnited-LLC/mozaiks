import { deriveArtifactId } from '../../core/actions/actionUtils';

const INLINE_DISPLAY = 'inline';

function asPlainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function firstFiniteNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return 0;
}

function compactStringList(value, limit = 4) {
  return Array.isArray(value)
    ? value.map((item) => String(item || '').trim()).filter(Boolean).slice(0, limit)
    : [];
}

function normalizeToolCallId(toolName, componentType, fallbackId = null) {
  if (fallbackId) return fallbackId;
  const stableKey = `${toolName || componentType || 'inline-ui'}`
    .replace(/[^a-zA-Z0-9_.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
  return `inline-ui-${stableKey || Date.now()}`;
}

export function buildInlineToolCallMessageFromEvent(data, currentWorkflowName) {
  if (!data || typeof data !== 'object') {
    return null;
  }

  const detail = asPlainObject(data.data);
  const payload = asPlainObject(detail.payload || data.payload);
  const display = String(
    data.display
    || data.display_type
    || data.mode
    || detail.display
    || detail.display_type
    || detail.mode
    || payload.display
    || payload.mode
    || INLINE_DISPLAY
  ).trim().toLowerCase();

  if (display !== INLINE_DISPLAY) {
    return null;
  }

  const toolName = data.tool_name || detail.tool_name || payload.tool_name || data.component_type || detail.component_type || null;
  const componentType = data.component_type || detail.component_type || payload.component_type || toolName;
  if (!toolName && !componentType) {
    return null;
  }

  const toolCallId = normalizeToolCallId(
    toolName,
    componentType,
    data.tool_call_id || data.corr || detail.tool_call_id || detail.corr || payload.tool_call_id || null,
  );
  const workflowName = data.workflow_name || detail.workflow_name || payload.workflow_name || currentWorkflowName || null;
  const agentName = data.agent || data.agent_name || detail.agent || detail.agent_name || payload.agent || payload.agent_name || 'Agent';
  const awaitingResponse = data.awaiting_response ?? detail.awaiting_response ?? payload.awaiting_response ?? false;
  const interactionType = data.interaction_type || detail.interaction_type || payload.interaction_type || (awaitingResponse ? 'ui_tool' : 'ui_surface');
  const content = String(payload.agent_message || payload.description || '').trim();

  return {
    id: `tool-call-${toolCallId}`,
    sender: 'agent',
    agentName,
    content,
    isStreaming: false,
    toolCall: {
      tool_name: toolName || componentType,
      payload: {
        ...payload,
        tool_name: toolName || componentType,
        component_type: componentType || toolName,
        workflow_name: workflowName,
        display,
        mode: display,
        awaiting_response: awaitingResponse,
        interaction_type: interactionType,
      },
      tool_call_id: toolCallId,
      workflow_name: workflowName,
      display,
      component_type: componentType || toolName,
    },
    metadata: {
      toolCallId,
      tool_call_id: toolCallId,
      tool_name: toolName || componentType,
      component_type: componentType || toolName,
      workflow_name: workflowName,
      display,
      interaction_type: interactionType,
      awaiting_response: awaitingResponse,
    },
  };
}

export function upsertInlineToolCallMessage(messageList, inlineMessage) {
  if (!inlineMessage) {
    return Array.isArray(messageList) ? messageList : [];
  }

  const messagesList = Array.isArray(messageList) ? [...messageList] : [];
  const messageKey = inlineMessage.metadata?.toolCallId || inlineMessage.toolCall?.tool_call_id;
  const existingIndex = messagesList.findIndex((msg) => (
    messageKey
    && msg?.toolCall
    && (
      msg?.metadata?.toolCallId === messageKey
      || msg?.metadata?.tool_call_id === messageKey
      || msg?.toolCall?.tool_call_id === messageKey
    )
  ));

  if (existingIndex >= 0) {
    const existing = messagesList[existingIndex];
    messagesList[existingIndex] = {
      ...existing,
      ...inlineMessage,
      id: existing.id || inlineMessage.id,
      metadata: {
        ...(existing.metadata || {}),
        ...inlineMessage.metadata,
      },
    };
    return messagesList;
  }

  return [...messagesList, inlineMessage];
}

export function shouldShowToolProgress(data) {
  return (
    data.ui_visibility === 'user'
    || data.metadata?.ui_visibility === 'user'
    || data.display_variant
    || data.metadata?.display_variant
    || data.component_type
    || data.metadata?.component_type
  );
}

export function buildComposerArtifactContext(artifactContext) {
  if (!artifactContext || typeof artifactContext !== 'object') {
    return null;
  }

  const payload = asPlainObject(artifactContext.payload);
  const catalog = asPlainObject(payload.catalog);
  const coverage = asPlainObject(catalog.coverage || payload.coverage);
  const artifactVersionIds = asPlainObject(payload.artifact_version_ids);
  const artifactId = artifactContext.artifact_id
    || payload.artifact_id
    || deriveArtifactId(payload, artifactContext.id || payload.tool_call_id || artifactContext.type || null);
  const componentType = payload.component_type || payload.tool_name || artifactContext.type || null;

  const compactContext = {
    id: artifactContext.id || payload.tool_call_id || null,
    type: artifactContext.type || componentType,
    artifact_id: artifactId,
    chat_id: artifactContext.chat_id || null,
    workflow_name: artifactContext.workflow_name || payload.workflow_name || null,
    display: artifactContext.display || payload.display || payload.mode || null,
  };

  if (coverage || Object.keys(artifactVersionIds).length > 0) {
    compactContext.summary = {
      component_type: componentType,
      artifact_version_ids: artifactVersionIds,
      coverage: {
        files: firstFiniteNumber(coverage.file_count, payload.total_files_scanned),
        symbols: firstFiniteNumber(coverage.symbol_count),
        graph_nodes: firstFiniteNumber(coverage.node_count),
        graph_edges: firstFiniteNumber(coverage.edge_count),
      },
      suggested_adjustments_count: Array.isArray(payload.suggested_adjustments)
        ? payload.suggested_adjustments.length
        : 0,
      warnings: compactStringList(payload.warnings || catalog.warnings, 4),
    };
  }

  return compactContext;
}
