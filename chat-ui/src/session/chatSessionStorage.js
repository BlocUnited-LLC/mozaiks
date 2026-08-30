import platform from '../platform/index.js';

const CURRENT_CHAT_ID_KEY = 'mozaiks.current_chat_id';
const CURRENT_WORKFLOW_NAME_KEY = 'mozaiks.current_workflow_name';
const CONVERSATION_MODE_KEY = 'mozaiks.conversation_mode';
const ACTIVE_GENERAL_CHAT_ID_KEY = 'mozaiks.active_general_chat_id';
const WORKFLOW_CHAT_ID_PREFIX = 'mozaiks.workflow_chat_id';

const readValue = (key) => {
  try {
    return platform.storage.getItem(key);
  } catch {
    return null;
  }
};

const writeValue = (key, value) => {
  try {
    platform.storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
};

const removeValue = (key) => {
  try {
    platform.storage.removeItem(key);
    return true;
  } catch {
    return false;
  }
};

const readJsonValue = (key) => {
  const raw = readValue(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

const writeJsonValue = (key, value) => writeValue(key, JSON.stringify(value));

const CHAT_PERSISTENCE_LOG_PREFIX = '[MOZAIKS_CHAT_PERSISTENCE]';

const stringifyLogDetails = (details) => {
  try {
    return JSON.stringify(details, (_key, value) => {
      if (typeof value === 'function') {
        return '[Function]';
      }
      if (typeof value === 'symbol') {
        return value.toString();
      }
      return value;
    });
  } catch (error) {
    return JSON.stringify({
      error: 'log_serialization_failed',
      message: error?.message || String(error),
    });
  }
};

export const logChatPersistence = (event, details = {}) => {
  try {
    console.info(`${CHAT_PERSISTENCE_LOG_PREFIX} ${event} ${stringifyLogDetails(details)}`);
  } catch {
    // Logging must never break persistence.
  }
};

const summarizeMessageList = (messageList) => {
  const messages = Array.isArray(messageList) ? messageList : [];
  const componentCounts = new Map();
  let uiMessageCount = 0;
  let toolProgressCount = 0;

  for (const message of messages) {
    const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {};
    const toolCall = message?.toolCall && typeof message.toolCall === 'object' ? message.toolCall : {};
    const componentType = String(
      message?.component_type
      || toolCall.component_type
      || metadata.component_type
      || toolCall.tool_name
      || message?.tool_name
      || '',
    ).trim();

    if (metadata.event_type === 'tool_progress') {
      toolProgressCount += 1;
    }
    if (message?.ui_mode || toolCall.tool_name || componentType) {
      uiMessageCount += 1;
    }
    if (componentType) {
      componentCounts.set(componentType, (componentCounts.get(componentType) || 0) + 1);
    }
  }

  return {
    total: messages.length,
    toolProgressCount,
    uiMessageCount,
    componentTypes: Array.from(componentCounts.entries())
      .sort((left, right) => right[1] - left[1])
      .slice(0, 5)
      .map(([componentType, count]) => ({ componentType, count })),
  };
};

export const summarizeArtifactWorkspaceSnapshot = (snapshot) => {
  if (!snapshot || typeof snapshot !== 'object') {
    return null;
  }

  return {
    chatId: snapshot.chatId || null,
    workflowName: snapshot.workflowName || null,
    isOpen: Boolean(snapshot.isOpen),
    layoutMode: snapshot.layoutMode || null,
    messages: summarizeMessageList(snapshot.messages),
  };
};

export const summarizeWorkflowMessages = summarizeMessageList;

export const summarizeWorkflowTranscriptSnapshot = (snapshot) => {
  if (!snapshot || typeof snapshot !== 'object') {
    return null;
  }

  return {
    chatId: snapshot.chatId || null,
    workflowName: snapshot.workflowName || null,
    messages: summarizeMessageList(snapshot.messages),
  };
};

export const getStoredActiveChatId = () => readValue(CURRENT_CHAT_ID_KEY);

export const setStoredActiveChatId = (chatId) => {
  if (!chatId) return removeValue(CURRENT_CHAT_ID_KEY);
  return writeValue(CURRENT_CHAT_ID_KEY, String(chatId));
};

export const clearStoredActiveChatId = () => removeValue(CURRENT_CHAT_ID_KEY);

export const getStoredActiveGeneralChatId = () => readValue(ACTIVE_GENERAL_CHAT_ID_KEY);

export const setStoredActiveGeneralChatId = (generalChatId) => {
  if (!generalChatId) return removeValue(ACTIVE_GENERAL_CHAT_ID_KEY);
  return writeValue(ACTIVE_GENERAL_CHAT_ID_KEY, String(generalChatId));
};

export const clearStoredActiveGeneralChatId = () => removeValue(ACTIVE_GENERAL_CHAT_ID_KEY);

export const getStoredActiveWorkflowName = () => readValue(CURRENT_WORKFLOW_NAME_KEY);

export const setStoredActiveWorkflowName = (workflowName) => {
  if (!workflowName) return removeValue(CURRENT_WORKFLOW_NAME_KEY);
  return writeValue(CURRENT_WORKFLOW_NAME_KEY, String(workflowName));
};

export const getStoredConversationMode = () => readValue(CONVERSATION_MODE_KEY);

export const setStoredConversationMode = (mode) => {
  if (!mode) return removeValue(CONVERSATION_MODE_KEY);
  return writeValue(CONVERSATION_MODE_KEY, String(mode));
};

const scopePart = (value) => {
  const normalized = String(value || '').trim();
  return normalized ? encodeURIComponent(normalized) : null;
};

export const getStoredWorkflowChatIdKey = ({ appId, userId, workflowName } = {}) => {
  const app = scopePart(appId);
  const user = scopePart(userId);
  const workflow = scopePart(workflowName);
  if (!app || !user || !workflow) return null;
  return `${WORKFLOW_CHAT_ID_PREFIX}.${app}.${user}.${workflow}`;
};

export const getStoredWorkflowChatId = (scope) => {
  const key = getStoredWorkflowChatIdKey(scope);
  return key ? readValue(key) : null;
};

export const setStoredWorkflowChatId = ({ appId, userId, workflowName, chatId } = {}) => {
  const key = getStoredWorkflowChatIdKey({ appId, userId, workflowName });
  if (!key) return false;
  if (!chatId) return removeValue(key);
  return writeValue(key, String(chatId));
};

export const clearStoredWorkflowChatId = (scope) => {
  const key = getStoredWorkflowChatIdKey(scope);
  return key ? removeValue(key) : false;
};

export const getChatCacheSeedKey = (chatId) => `${CURRENT_CHAT_ID_KEY}.cache_seed.${chatId}`;

export const getStoredChatCacheSeed = (chatId) => {
  if (!chatId) return null;
  const raw = readValue(getChatCacheSeedKey(chatId));
  if (raw === null || raw === undefined || raw === '') return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
};

export const setStoredChatCacheSeed = (chatId, cacheSeed) => {
  if (!chatId || cacheSeed === null || cacheSeed === undefined) return false;
  return writeValue(getChatCacheSeedKey(chatId), String(cacheSeed));
};

export const clearStoredChatCacheSeed = (chatId) => {
  if (!chatId) return false;
  return removeValue(getChatCacheSeedKey(chatId));
};

export const getCurrentArtifactKey = (chatId) => `mozaiks.current_artifact.${chatId}`;
export const getLastArtifactKey = (chatId) => `mozaiks.last_artifact.${chatId}`;
export const getArtifactPanelKey = (chatId) => `mozaiks.artifact_panel_open.${chatId}`;
export const getArtifactWorkspaceSnapshotKey = (chatId) => `mozaiks.artifact_workspace_snapshot.${chatId}`;
export const getWorkflowTranscriptSnapshotKey = (chatId) => `mozaiks.workflow_transcript_snapshot.${chatId}`;

export const readStoredCurrentArtifact = (chatId) => {
  if (!chatId) return null;
  return readJsonValue(getCurrentArtifactKey(chatId));
};

export const writeStoredCurrentArtifact = (chatId, artifact) => {
  if (!chatId || !artifact) return false;
  return writeJsonValue(getCurrentArtifactKey(chatId), artifact);
};

export const readStoredLastArtifact = (chatId) => {
  if (!chatId) return null;
  return readJsonValue(getLastArtifactKey(chatId));
};

export const writeStoredLastArtifact = (chatId, artifact) => {
  if (!chatId || !artifact) return false;
  return writeJsonValue(getLastArtifactKey(chatId), artifact);
};

export const getStoredArtifactPanelOpen = (chatId) => {
  if (!chatId) return null;
  const raw = readValue(getArtifactPanelKey(chatId));
  if (raw === null) return null;
  return raw === 'true';
};

export const setStoredArtifactPanelOpen = (chatId, open) => {
  if (!chatId) return false;
  return writeValue(getArtifactPanelKey(chatId), open ? 'true' : 'false');
};

export const clearStoredArtifactPanelOpen = (chatId) => {
  if (!chatId) return false;
  return removeValue(getArtifactPanelKey(chatId));
};

export const readStoredArtifactWorkspaceSnapshot = (chatId) => {
  if (!chatId) return null;
  const snapshot = readJsonValue(getArtifactWorkspaceSnapshotKey(chatId));
  logChatPersistence('read_artifact_workspace_snapshot', {
    chatId: String(chatId),
    found: Boolean(snapshot),
    snapshot: summarizeArtifactWorkspaceSnapshot(snapshot),
  });
  return snapshot;
};

export const writeStoredArtifactWorkspaceSnapshot = (chatId, snapshot) => {
  if (!chatId || !snapshot) return false;
  const written = writeJsonValue(getArtifactWorkspaceSnapshotKey(chatId), snapshot);
  logChatPersistence('write_artifact_workspace_snapshot', {
    chatId: String(chatId),
    wrote: written,
    snapshot: summarizeArtifactWorkspaceSnapshot(snapshot),
  });
  return written;
};

export const clearStoredArtifactWorkspaceSnapshot = (chatId) => {
  if (!chatId) return false;
  const cleared = removeValue(getArtifactWorkspaceSnapshotKey(chatId));
  logChatPersistence('clear_artifact_workspace_snapshot', {
    chatId: String(chatId),
    cleared,
  });
  return cleared;
};

export const readStoredWorkflowTranscriptSnapshot = (chatId) => {
  if (!chatId) return null;
  const snapshot = readJsonValue(getWorkflowTranscriptSnapshotKey(chatId));
  logChatPersistence('read_workflow_transcript_snapshot', {
    chatId: String(chatId),
    found: Boolean(snapshot),
    snapshot: summarizeWorkflowTranscriptSnapshot(snapshot),
  });
  return snapshot;
};

export const writeStoredWorkflowTranscriptSnapshot = (chatId, snapshot) => {
  if (!chatId || !snapshot) return false;
  const written = writeJsonValue(getWorkflowTranscriptSnapshotKey(chatId), snapshot);
  logChatPersistence('write_workflow_transcript_snapshot', {
    chatId: String(chatId),
    wrote: written,
    snapshot: summarizeWorkflowTranscriptSnapshot(snapshot),
  });
  return written;
};

export const clearStoredWorkflowTranscriptSnapshot = (chatId) => {
  if (!chatId) return false;
  const cleared = removeValue(getWorkflowTranscriptSnapshotKey(chatId));
  logChatPersistence('clear_workflow_transcript_snapshot', {
    chatId: String(chatId),
    cleared,
  });
  return cleared;
};

export const clearStoredArtifactState = (chatId) => {
  if (!chatId) return false;
  clearStoredArtifactPanelOpen(chatId);
  removeValue(getCurrentArtifactKey(chatId));
  removeValue(getLastArtifactKey(chatId));
  clearStoredArtifactWorkspaceSnapshot(chatId);
  clearStoredWorkflowTranscriptSnapshot(chatId);
  return true;
};

export const getStoredChatSessionSnapshot = () => ({
  activeChatId: getStoredActiveChatId(),
  activeGeneralChatId: getStoredActiveGeneralChatId(),
  activeWorkflowName: getStoredActiveWorkflowName(),
  conversationMode: getStoredConversationMode(),
});
