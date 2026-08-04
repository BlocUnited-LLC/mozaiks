export const SUPPORT_PROFILE_TAB_ID = 'support-tickets';

export const HUMAN_SUPPORT_CUE = /\b(human|person|operator|support|help\s+desk|real\s+person|talk\s+to\s+someone|speak\s+to\s+someone)\b/i;

export function shouldOfferHumanSupport(text) {
  return HUMAN_SUPPORT_CUE.test(String(text || ''));
}
export const SUPPORT_LOG_PREFIX = '⚠️ [mozaiks-support]';

function cleanString(value) {
  const text = String(value || '').trim();
  return text || null;
}

function safeDetails(details) {
  if (!details || typeof details !== 'object') return details;
  const safe = { ...details };
  if (typeof safe.message === 'string') safe.messageLength = safe.message.length;
  delete safe.message;
  if (typeof safe.body === 'string') safe.bodyLength = safe.body.length;
  delete safe.body;
  return safe;
}

export function supportTrace(event, details = {}) {
  try {
    console.info(SUPPORT_LOG_PREFIX, event, safeDetails(details));
  } catch (_) {}
}

export function supportWarn(event, details = {}) {
  try {
    console.warn(SUPPORT_LOG_PREFIX, event, safeDetails(details));
  } catch (_) {}
}

export function buildUserSupportPath({ requestId = null, appId = null } = {}) {
  const params = new URLSearchParams({ tab: SUPPORT_PROFILE_TAB_ID });
  const cleanRequestId = cleanString(requestId);
  const cleanAppId = cleanString(appId);
  if (cleanRequestId) params.set('request_id', cleanRequestId);
  if (cleanAppId) params.set('app_id', cleanAppId);
  return `/me?${params.toString()}`;
}

export function buildSupportRequestPayload({
  message,
  appId = null,
  userId = null,
  pageUrl = null,
  pageTitle = null,
  severity = 'low',
  conversationTranscript = null,
} = {}) {
  const payload = {
    message: cleanString(message) || '',
    severity: cleanString(severity) || 'low',
  };
  const cleanAppId = cleanString(appId);
  const cleanUserId = cleanString(userId);
  const cleanPageUrl = cleanString(pageUrl);
  const cleanPageTitle = cleanString(pageTitle);
  if (cleanAppId) payload.app_id = cleanAppId;
  if (cleanUserId) payload.user_id = cleanUserId;
  if (cleanPageUrl) payload.page_url = cleanPageUrl;
  if (cleanPageTitle) payload.page_title = cleanPageTitle;
  if (Array.isArray(conversationTranscript) && conversationTranscript.length > 0) {
    payload.conversation_transcript = conversationTranscript
      .map((item) => ({
        role: cleanString(item?.role || item?.sender) || 'user',
        content: cleanString(item?.content || item?.message || item?.body) || '',
      }))
      .filter((item) => item.content)
      .slice(-20);
  }
  return payload;
}

export function buildSupportConversationTranscript(messages = [], { includeMessage = null } = {}) {
  const rows = [];
  if (Array.isArray(messages)) {
    messages.forEach((message) => {
      if (!message || message.isThinking || message.toolCall) return;
      const content = cleanString(message.content);
      if (!content) return;
      const sender = cleanString(message.sender || message.role || message.sender_role);
      const role = sender === 'agent' || sender === 'assistant' ? 'assistant' : sender === 'system' ? 'system' : 'user';
      rows.push({ role, content });
    });
  }
  const extra = cleanString(includeMessage);
  if (extra) {
    const last = rows[rows.length - 1];
    if (!(last?.role === 'user' && last?.content === extra)) {
      rows.push({ role: 'user', content: extra });
    }
  }
  return rows.slice(-20);
}

function getHostApiBaseUrl(api, config = {}) {
  if (api && typeof api.getHttpBaseUrl === 'function') {
    const baseUrl = api.getHttpBaseUrl();
    if (typeof baseUrl === 'string') return baseUrl.replace(/\/+$/, '');
  }
  const configured = (
    config?.apiUrl ||
    config?.api_url ||
    config?.appBackendUrl ||
    config?.app_backend_url ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_CORE_URL) ||
    ''
  );
  return typeof configured === 'string' ? configured.replace(/\/+$/, '') : '';
}

async function fetchCurrentProfileScope(api, auth, config) {
  const backendUrl = getHostApiBaseUrl(api, config);
  if (!backendUrl) return null;
  const headers = { 'Content-Type': 'application/json' };
  try {
    const token = await auth?.getToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch (_) {}
  const response = await fetch(`${backendUrl}/api/me`, { headers });
  if (!response.ok) return null;
  const profile = await response.json();
  return {
    appId: cleanString(profile?.app_id || profile?.appId),
    userId: cleanString(profile?.user_id || profile?.userId || profile?.id || profile?.sub),
    username: cleanString(profile?.username),
  };
}

export async function resolveSupportRequestScope({
  api = null,
  auth = null,
  config = {},
  user = null,
  fallbackAppId = null,
  fallbackUserId = null,
} = {}) {
  const localScope = {
    appId: cleanString(user?.app_id || user?.appId || config?.appId || config?.app_id || fallbackAppId),
    userId: cleanString(user?.id || user?.user_id || user?.userId || user?.sub || fallbackUserId),
  };
  try {
    const profileScope = await fetchCurrentProfileScope(api, auth, config);
    const resolved = {
      appId: profileScope?.appId || localScope.appId,
      userId: profileScope?.userId || localScope.userId,
      username: profileScope?.username || null,
    };
    supportTrace('scope:resolved', {
      appId: resolved.appId,
      userId: resolved.userId,
      username: resolved.username,
      source: profileScope ? 'profile' : 'local',
    });
    return resolved;
  } catch (error) {
    supportWarn('scope:resolve_failed', {
      error: error?.message || String(error || ''),
      fallbackAppId: localScope.appId,
      fallbackUserId: localScope.userId,
    });
    return { ...localScope, username: null };
  }
}
