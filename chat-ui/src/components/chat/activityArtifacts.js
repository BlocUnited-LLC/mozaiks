import { deriveArtifactId } from '../../core/actions/actionUtils';

const ACTIVITY_COMPLETE_STATUSES = ['complete', 'completed', 'ready', 'success', 'succeeded', 'done'];
const ACTIVITY_FAILED_STATUSES = ['failed', 'error', 'unavailable'];

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

function normalizeActivityPayload(payload) {
  const progress = asPlainObject(payload.progress || payload.activity_progress);
  const activityType = payload.activity_type || progress.activity_type || null;
  if (!activityType) return null;

  const activityAgent = payload.activity_agent || payload.agent || progress.agent || 'System';
  const stage = String(progress.stage || payload.progress_stage || payload.status || 'partial').trim() || 'partial';
  const status = String(progress.status || payload.progress_status || payload.status || stage).trim() || 'partial';
  const isReady = stage === 'ready' || ACTIVITY_COMPLETE_STATUSES.includes(status);
  const percent = Number.isFinite(Number(progress.percent))
    ? Number(progress.percent)
    : (isReady ? 100 : 0);
  const warnings = Array.isArray(progress.warnings)
    ? progress.warnings
    : (Array.isArray(payload.warnings) ? payload.warnings : []);

  return {
    activityType,
    activityAgent,
    stage,
    status,
    activityStatus: isReady ? 'complete' : status,
    percent,
    warnings,
    progress,
    displayVariant: payload.activity_display_variant || payload.display_variant || null,
    componentType: payload.activity_component_type || payload.activity_component || payload.component_type || null,
    content: String(
      payload.activity_message
      || progress.message
      || payload.summary
      || 'Background activity restored for this run.'
    ).trim(),
    snapshotId: payload.activity_snapshot_id || payload.snapshot_id || null,
  };
}

export function isRestorableActivityArtifact(artifact) {
  if (!artifact || typeof artifact !== 'object') return false;
  const payload = asPlainObject(artifact.payload);
  return Boolean(
    payload.activity_type
    || payload.activity_progress
    || payload.progress
    || payload.activity_display_variant
    || payload.activity_component_type
    || payload.display_variant
    || payload.component_type
  );
}

export function buildRestoredActivityMessage({ artifact, chatId, workflowName }) {
  if (!isRestorableActivityArtifact(artifact)) return null;

  const payload = asPlainObject(artifact.payload);
  const normalized = normalizeActivityPayload(payload);
  if (!normalized) return null;

  const activityKey = `${normalized.activityType}:${normalized.activityAgent}`;
  const idSeed = artifact.tool_call_id || normalized.snapshotId || chatId || Date.now();

  return {
    id: `activity-restored-${idSeed}`,
    sender: 'system',
    agentName: normalized.activityAgent,
    content: normalized.content,
    isStreaming: false,
    metadata: {
      event_type: 'activity',
      activity_type: normalized.activityType,
      activity_status: normalized.activityStatus,
      activity_agent: normalized.activityAgent,
      activity_key: activityKey,
      progress_percent: normalized.percent,
      progress_stage: normalized.stage,
      progress_status: normalized.status,
      progress_warnings: normalized.warnings,
      progress: normalized.progress,
      raw_activity_metadata: {
        progress_stage: normalized.stage,
        progress_status: normalized.status,
        progress_warnings: normalized.warnings,
        progress: normalized.progress,
      },
      display_variant: normalized.displayVariant,
      activity_display_variant: normalized.displayVariant,
      component_type: normalized.componentType,
      activity_component_type: normalized.componentType,
      workflow_name: artifact.workflow_name || workflowName || null,
      restored_from_last_artifact: true,
      activity_snapshot_id: normalized.snapshotId,
    },
  };
}

export function upsertActivityMessage(messageList, restoredMessage) {
  if (!restoredMessage) {
    return Array.isArray(messageList) ? messageList : [];
  }

  const messagesList = Array.isArray(messageList) ? [...messageList] : [];
  const restoredMetadata = restoredMessage.metadata || {};
  const existingIndex = messagesList.findIndex((msg) => (
    msg?.metadata?.event_type === 'activity'
    && (
      msg?.metadata?.activity_key === restoredMetadata.activity_key
      || msg?.metadata?.activity_type === restoredMetadata.activity_type
    )
  ));

  if (existingIndex >= 0) {
    const existing = messagesList[existingIndex];
    messagesList[existingIndex] = {
      ...existing,
      ...restoredMessage,
      id: existing.id || restoredMessage.id,
      metadata: {
        ...(existing.metadata || {}),
        ...restoredMessage.metadata,
      },
    };
    return messagesList;
  }

  const firstAgentIndex = messagesList.findIndex((msg) => (
    msg?.sender === 'agent'
    && msg?.metadata?.type !== 'tool_call_agent_message'
  ));
  if (firstAgentIndex >= 0) {
    messagesList.splice(firstAgentIndex, 0, restoredMessage);
    return messagesList;
  }

  return [...messagesList, restoredMessage];
}

export function activityArtifactFromMessage(message, fallbackWorkflowName = null) {
  if (!message || typeof message !== 'object') {
    return null;
  }

  const toolCall = message.toolCall && typeof message.toolCall === 'object'
    ? message.toolCall
    : {};
  const toolName = message.tool_name || toolCall.tool_name || toolCall.component_type;
  const payload = message.payload || toolCall.payload || {};
  const artifact = {
    tool_name: toolName,
    component_type: message.component_type || toolCall.component_type || toolName,
    tool_call_id: message.tool_call_id || toolCall.tool_call_id || message.id || null,
    workflow_name: message.workflow_name || toolCall.workflow_name || fallbackWorkflowName || null,
    payload,
    display: message.display || toolCall.display || payload.display || payload.mode || 'artifact',
  };

  return isRestorableActivityArtifact(artifact) ? artifact : null;
}

export function buildActivityMessageFromEvent(data, currentWorkflowName) {
  const activityType = data.activity_type || data.status || 'background';
  const activityAgent = data.agent || data.agent_name || 'System';
  const activityStatus = data.status || 'working';
  const rawActivityMetadata = asPlainObject(data.metadata);
  const displayVariant = (
    data.activity_display_variant
    || rawActivityMetadata.activity_display_variant
    || data.display_variant
    || rawActivityMetadata.display_variant
    || null
  );
  const componentType = (
    data.activity_component_type
    || rawActivityMetadata.activity_component_type
    || data.component_type
    || rawActivityMetadata.component_type
    || null
  );
  const activityMessage = data.message || `${activityAgent} is working in the background.`;
  const activityKey = `${activityType}:${activityAgent}`;
  const normalizedActivityStatus = String(activityStatus || '').trim().toLowerCase();
  const activityIcon = ACTIVITY_COMPLETE_STATUSES.includes(normalizedActivityStatus)
    ? '✓'
    : ACTIVITY_FAILED_STATUSES.includes(normalizedActivityStatus)
      ? '⚠'
      : '⏳';
  const content = displayVariant ? activityMessage : `${activityIcon} ${activityMessage}`;

  return {
    id: `activity-${Date.now()}`,
    sender: 'system',
    agentName: activityAgent,
    content,
    isStreaming: false,
    metadata: {
      event_type: 'activity',
      activity_type: activityType,
      activity_status: activityStatus,
      activity_agent: activityAgent,
      activity_key: activityKey,
      progress_percent: data.progress_percent,
      progress_stage: rawActivityMetadata.progress_stage || data.progress_stage || null,
      progress_status: rawActivityMetadata.progress_status || data.progress_status || null,
      progress_warnings: rawActivityMetadata.progress_warnings || data.progress_warnings || [],
      progress: rawActivityMetadata.progress || data.progress || null,
      raw_activity_metadata: rawActivityMetadata,
      display_variant: displayVariant,
      activity_display_variant: displayVariant,
      component_type: componentType,
      activity_component_type: componentType,
      workflow_name: data.workflow_name || currentWorkflowName || null,
    },
  };
}

export function shouldShowToolProgress(data) {
  return (
    data.ui_visibility === 'user'
    || data.metadata?.ui_visibility === 'user'
    || data.display_variant
    || data.activity_display_variant
    || data.metadata?.display_variant
    || data.metadata?.activity_display_variant
    || data.component_type
    || data.activity_component_type
    || data.metadata?.component_type
    || data.metadata?.activity_component_type
  );
}

export function buildComposerArtifactContext(artifactContext) {
  if (!artifactContext || typeof artifactContext !== 'object') {
    return null;
  }

  const payload = asPlainObject(artifactContext.payload);
  const catalog = asPlainObject(payload.activity_catalog || payload.catalog);
  const coverage = asPlainObject(catalog.coverage || payload.coverage);
  const progress = asPlainObject(payload.progress || payload.activity_progress);
  const artifactVersionIds = asPlainObject(payload.artifact_version_ids);
  const artifactId = artifactContext.artifact_id
    || payload.artifact_id
    || deriveArtifactId(payload, artifactContext.id || payload.tool_call_id || artifactContext.type || null);
  const componentType = payload.component_type || payload.tool_name || artifactContext.type || null;
  const isRestorableActivity = isRestorableActivityArtifact({
    tool_name: componentType,
    component_type: componentType,
    payload,
  });

  const compactContext = {
    id: artifactContext.id || payload.tool_call_id || null,
    type: artifactContext.type || componentType,
    artifact_id: artifactId,
    chat_id: artifactContext.chat_id || null,
    workflow_name: artifactContext.workflow_name || payload.workflow_name || null,
  };

  if (!isRestorableActivity) {
    return compactContext;
  }

  return {
    ...compactContext,
    activity: {
      type: payload.activity_type || null,
      status: payload.status || progress.status || null,
      app_name: payload.app_name || payload.repo_name || catalog.app_id || null,
      github_repo: payload.github_repo || null,
      snapshot_id: payload.activity_snapshot_id || payload.snapshot_id || catalog.snapshot_id || null,
      context_version_id: payload.current_context_version_id || payload.current_app_context_version_id || null,
      artifact_version_ids: artifactVersionIds,
      component_type: payload.activity_component_type || null,
      display_variant: payload.activity_display_variant || payload.display_variant || null,
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
    },
  };
}
