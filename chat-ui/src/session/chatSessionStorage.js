import platform from '../platform/index.js';

const CURRENT_CHAT_ID_KEY = 'mozaiks.current_chat_id';
const CURRENT_WORKFLOW_NAME_KEY = 'mozaiks.current_workflow_name';
const CONVERSATION_MODE_KEY = 'mozaiks.conversation_mode';

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

export const getStoredActiveChatId = () => readValue(CURRENT_CHAT_ID_KEY);

export const setStoredActiveChatId = (chatId) => {
  if (!chatId) return removeValue(CURRENT_CHAT_ID_KEY);
  return writeValue(CURRENT_CHAT_ID_KEY, String(chatId));
};

export const clearStoredActiveChatId = () => removeValue(CURRENT_CHAT_ID_KEY);

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

export const clearStoredArtifactState = (chatId) => {
  if (!chatId) return false;
  clearStoredArtifactPanelOpen(chatId);
  removeValue(getCurrentArtifactKey(chatId));
  removeValue(getLastArtifactKey(chatId));
  return true;
};

export const getStoredChatSessionSnapshot = () => ({
  activeChatId: getStoredActiveChatId(),
  activeWorkflowName: getStoredActiveWorkflowName(),
  conversationMode: getStoredConversationMode(),
});
