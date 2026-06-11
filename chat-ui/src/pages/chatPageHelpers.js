const DEBUG_LOG_ALL_AGENT_OUTPUT = true;

const shouldDebugAllAgents = () => {
  try {
    const value = localStorage.getItem('mozaiks.debug_all_agents');
    if (value != null) {
      return value === '1' || value === 'true';
    }
  } catch {
    return DEBUG_LOG_ALL_AGENT_OUTPUT;
  }
  return DEBUG_LOG_ALL_AGENT_OUTPUT;
};

export const logAgentOutput = (phase, agentName, content, meta = {}) => {
  if (!shouldDebugAllAgents()) return;
  try {
    const preview = typeof content === 'string' ? content.slice(0, 400) : JSON.stringify(content);
    console.log(`🛰️ [${phase}]`, { agent: agentName || 'Unknown', content: preview, ...meta });
  } catch {
    console.log(`🛰️ [${phase}]`, { agent: agentName || 'Unknown', content, ...meta });
  }
};

export const debugFlag = (key) => {
  try {
    return ['1', 'true', 'on', 'yes'].includes((localStorage.getItem(key) || '').toLowerCase());
  } catch {
    return false;
  }
};

export const getAccessToken = () => {
  if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
    return window.mozaiksAuth.getAccessToken();
  }
  if (typeof localStorage !== 'undefined') {
    return localStorage.getItem('chatui_token') || localStorage.getItem('access_token');
  }
  return null;
};

export const getUserIdFromToken = () => {
  const token = getAccessToken();
  if (!token || typeof token !== 'string') {
    return null;
  }
  const parts = token.split('.');
  if (parts.length < 2) {
    return null;
  }
  try {
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
    if (typeof window === 'undefined' || typeof window.atob !== 'function') {
      return null;
    }
    const json = window.atob(padded);
    const payload = JSON.parse(json);
    return payload?.sub || payload?.user_id || payload?.preferred_username || null;
  } catch {
    return null;
  }
};