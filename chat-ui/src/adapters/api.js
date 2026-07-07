// API adapter interface
import workflowConfig from '../config/workflowConfig';
import resolveWorkflow from '../utils/resolveWorkflow';
import config from '../config';
import platform from '../platform/index.js';

/**
 * Get the current access token from storage.
 * In production, this should be provided by the auth adapter.
 * Falls back to localStorage for development/standalone mode.
 */
function getAccessToken(adapterConfig = null) {
  try {
    if (typeof adapterConfig?.getAccessToken === 'function') {
      return adapterConfig.getAccessToken();
    }
    if (typeof adapterConfig?.auth?.getAccessToken === 'function') {
      return adapterConfig.auth.getAccessToken();
    }
  } catch {
    // Fall through to platform-backed token lookup.
  }

  return platform.getAccessToken();
}

/**
 * Build headers with Authorization if token available.
 * Always includes Content-Type for JSON requests.
 */
function buildAuthHeaders(contentType = 'application/json', adapterConfig = null) {
  const headers = {};
  
  if (contentType) {
    headers['Content-Type'] = contentType;
  }
  
  const token = getAccessToken(adapterConfig);
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  return headers;
}

/**
 * Wrapper for fetch with automatic auth header injection.
 */
async function authFetch(url, options = {}, adapterConfig = null) {
  const token = getAccessToken(adapterConfig);
  
  const headers = {
    ...options.headers,
  };
  
  // Add Authorization header if token present and not already set
  if (token && !headers['Authorization']) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  return fetch(url, {
    ...options,
    headers,
  });
}

export class ApiAdapter {
  constructor(adapterConfig = null) {
    this.config = adapterConfig || {};
    this._generalChatsApiUnavailable = false;
    this._generalChatsTranscriptUnavailable = false;
    this._startChatInFlight = new Map();
  }

  _getStartChatKey(appId, workflowname, userId) {
    return `${String(appId || '')}::${String(workflowname || '')}::${String(userId || '')}`;
  }

  _runStartChatRequest(key, requestFactory) {
    const existing = this._startChatInFlight.get(key);
    if (existing) {
      return existing;
    }

    const request = (async () => {
      try {
        return await requestFactory();
      } finally {
        this._startChatInFlight.delete(key);
      }
    })();

    this._startChatInFlight.set(key, request);
    return request;
  }

  async get(path, options = {}) {
    const baseUrl = this.getHttpBaseUrl();
    const normalizedPath = typeof path === 'string' && path.startsWith('http')
      ? path
      : `${baseUrl}${String(path || '').startsWith('/') ? '' : '/'}${path || ''}`;

    const response = await authFetch(normalizedPath, {
      method: 'GET',
      ...(options || {}),
    }, this.config);

    if (!response.ok) {
      const err = new Error(`HTTP ${response.status}`);
      err.status = response.status;
      try {
        err.body = await response.text();
      } catch (_) {
        err.body = null;
      }
      throw err;
    }

    if (response.status === 204) return null;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return await response.json();
    }

    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch (_) {
      return { raw: text };
    }
  }

  getHttpBaseUrl() {
    const fallback = typeof config?.get === 'function' ? config.get('api.baseUrl') : undefined;
    const raw = this.config?.baseUrl
      || this.config?.api?.baseUrl
      || fallback
      || platform.resolveHttpUrl({ port: '8000' });
    return typeof raw === 'string' && raw.endsWith('/') ? raw.slice(0, -1) : raw;
  }

  getWsBaseUrl() {
    const fallback = typeof config?.get === 'function' ? config.get('api.wsUrl') : undefined;
    const raw = this.config?.wsUrl
      || this.config?.api?.wsUrl
      || fallback
      || platform.resolveWsUrl({ port: '8000' });
    return typeof raw === 'string' && raw.endsWith('/') ? raw.slice(0, -1) : raw;
  }

  async sendMessage(_message, _appId, _userId) {
    throw new Error('sendMessage must be implemented');
  }

  async sendMessageToWorkflow(message, appId, userId, workflowname = null, chatId = null, context = null) {
    const actualworkflowname = resolveWorkflow(workflowname);
    throw new Error('sendMessageToWorkflow must be implemented');
  }

  createWebSocketConnection(_appId, _userId, _callbacks, _workflowname = null, _chatId = null) {
    throw new Error('createWebSocketConnection must be implemented');
  }

  async getMessageHistory(_appId, _userId) {
    throw new Error('getMessageHistory must be implemented');
  }

  async uploadFile(_file, _appId, _userId) {
    throw new Error('uploadFile must be implemented');
  }

  async getWorkflowTransport(_workflowname) {
    throw new Error('getWorkflowTransport must be implemented');
  }

  async startChat(_appId, _workflowname, _userId) {
    throw new Error('startChat must be implemented');
  }

  async listGeneralChats(appId, userId, limit = 50) {
    if (this._generalChatsApiUnavailable) {
      return { sessions: [] };
    }
    const baseUrl = this.getHttpBaseUrl();
    const searchParams = new URLSearchParams();
    if (limit !== undefined && limit !== null) {
      searchParams.set('limit', String(limit));
    }

    const url = `${baseUrl}/api/general_chats/list/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}?${searchParams.toString()}`;

    try {
      const response = await authFetch(url, {}, this.config);
      if (response.status === 404) {
        // Optional feature: treat missing endpoint as "no conversations yet".
        this._generalChatsApiUnavailable = true;
        return { sessions: [] };
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      const sessions = Array.isArray(payload?.sessions)
        ? payload.sessions
        : Array.isArray(payload?.chats)
          ? payload.chats
          : [];
      return { ...payload, sessions };
    } catch (error) {
      console.warn('General chats endpoint unavailable, continuing without list.', error?.message || error);
      if (error?.status === 404 || String(error?.message || '').includes('HTTP 404')) {
        this._generalChatsApiUnavailable = true;
      }
      return { sessions: [] };
    }
  }

  async delete(path, options = {}) {
    const baseUrl = this.getHttpBaseUrl();
    const normalizedPath = typeof path === 'string' && path.startsWith('http')
      ? path
      : `${baseUrl}${String(path || '').startsWith('/') ? '' : '/'}${path || ''}`;

    const response = await authFetch(normalizedPath, {
      method: 'DELETE',
      ...(options || {}),
    }, this.config);

    if (!response.ok) {
      const err = new Error(`HTTP ${response.status}`);
      err.status = response.status;
      try {
        err.body = await response.text();
      } catch (_) {
        err.body = null;
      }
      throw err;
    }

    if (response.status === 204) return null;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return await response.json();
    }

    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch (_) {
      return { raw: text };
    }
  }

  async fetchGeneralChatTranscript(appId, generalChatId, options = {}) {
    if (this._generalChatsTranscriptUnavailable) {
      return null;
    }
    const { afterSequence = -1, limit = 200 } = options;
    const baseUrl = this.getHttpBaseUrl();
    const searchParams = new URLSearchParams();
    if (afterSequence !== undefined && afterSequence !== null) {
      searchParams.set('after_sequence', String(afterSequence));
    }
    if (limit !== undefined && limit !== null) {
      searchParams.set('limit', String(limit));
    }

    const url = `${baseUrl}/api/general_chats/transcript/${encodeURIComponent(appId)}/${encodeURIComponent(generalChatId)}?${searchParams.toString()}`;

    try {
      const response = await authFetch(url, {}, this.config);
      if (response.status === 404) {
        this._generalChatsTranscriptUnavailable = true;
        return null;
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.warn('General chat transcript endpoint unavailable.', error?.message || error);
      if (error?.status === 404 || String(error?.message || '').includes('HTTP 404')) {
        this._generalChatsTranscriptUnavailable = true;
      }
      return null;
    }
  }

  async clearWorkflowSessions(appId, userId, options = {}) {
    const status = options?.status || 'all';
    const workflowName = options?.workflow_name || options?.workflowName || null;
    const params = new URLSearchParams();
    if (status) params.set('status', String(status));
    if (workflowName) params.set('workflow_name', String(workflowName));
    const path = `/api/sessions/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}?${params.toString()}`;
    return this.delete(path);
  }

  async clearGeneralChats(appId, userId, options = {}) {
    const status = options?.status || 'all';
    const params = new URLSearchParams();
    if (status) params.set('status', String(status));
    const path = `/api/general_chats/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}?${params.toString()}`;
    return this.delete(path);
  }
}

// Default WebSocket API Adapter
export class WebSocketApiAdapter extends ApiAdapter {
  constructor(config) {
    super(config);
    this.config = config || {};
    this._chatConnections = new Map();
  }

  async sendMessage() {
    // For WebSocket, sending is handled by the connection
    // This method can be used for HTTP fallback if needed
    return { success: true };
  }

  async sendMessageToWorkflow(message, appId, userId, workflowname = null, chatId = null, context = null) {
    const actualworkflowname = resolveWorkflow(workflowname);

    if (!chatId) {
      console.error('Chat ID is required for sending message to workflow');
      return false;
    }

    const connection = this._chatConnections.get(chatId);
    if (!connection || typeof connection.send !== 'function') {
      console.error('WebSocket connection not available for chat', chatId);
      return false;
    }

    return connection.send({
      type: 'user.input.submit',
      chat_id: chatId,
      text: message,
      context: {
        source: 'chat_interface',
        conversation_mode: 'workflow',
        workflow_name: actualworkflowname,
        app_id: appId,
        user_id: userId,
        ...(context || {}),
      },
    });
  }

  async _sendMessageToWorkflowHttp(message, appId, userId, workflowname = null, chatId = null, context = null) {
    const actualworkflowname = resolveWorkflow(workflowname);
    
    if (!chatId) {
      console.error('Chat ID is required for sending message to workflow');
      return { success: false, error: 'Chat ID is required' };
    }
    
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/chat/${appId}/${chatId}/${userId}/input`, {
        method: 'POST',
        headers: buildAuthHeaders(undefined, this.config),
        body: JSON.stringify({ 
          message, 
          workflow_name: actualworkflowname,
          app_id: appId,
          user_id: userId,
          context: context || undefined,
        })
      }, this.config);

      if (response.ok) {
        const result = await response.json();
        return result;
      } else {
        console.error('Failed to send message:', response.status, response.statusText);
        return { success: false, error: `HTTP ${response.status}` };
      }
    } catch (error) {
      console.error('Failed to send message to workflow:', error);
      return { success: false, error: error.message };
    }
  }

  createWebSocketConnection(appId, userId, callbacks = {}, workflowname = null, chatId = null) {
    const actualworkflowname = resolveWorkflow(workflowname);
    
    
    if (!chatId) {
      console.error('Chat ID is required for WebSocket connection');
      return null;
    }
    
    const wsBase = this.getWsBaseUrl();
    
    // Build WebSocket URL with access_token query param for authentication
    let wsUrl = `${wsBase}/ws/${actualworkflowname}/${appId}/${chatId}/${userId}`;
    const token = getAccessToken(this.config);
    if (token) {
      wsUrl += `?access_token=${encodeURIComponent(token)}`;
    } else {
    }
    
    const socket = new WebSocket(wsUrl);
    
    // F7/F8: Sequence tracking and resume capability (strict canonical key)
    let lastSequence = parseInt(platform.storage.getItem(`ws_idx_${chatId}`) || '0');
    let resumePending = false;
    
    // Helper to send client.resume
    const sendResume = () => {
      if (socket.readyState === WebSocket.OPEN && !resumePending) {
        resumePending = true;
        socket.send(JSON.stringify({
          type: 'client.resume',
          chat_id: chatId,
          lastClientIndex: lastSequence
        }));
      }
    };

    socket.onopen = () => {
      
      // If we have a previous sequence, request resume first
      if (lastSequence > 0) {
        sendResume();
      }
      
      if (callbacks.onOpen) callbacks.onOpen();
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        // F7/F8: Track sequence numbers for resume capability
        if (data.seq && typeof data.seq === 'number') {
          if (data.seq > lastSequence) {
            lastSequence = data.seq;
            try { platform.storage.setItem(`ws_idx_${chatId}`, lastSequence.toString()); } catch (_) {}
          } else if (data.seq < lastSequence - 1 && !resumePending) {
            // Sequence gap detected - request resume
            console.warn(`⚠️ Sequence gap detected: received ${data.seq}, expected > ${lastSequence}`);
            sendResume();
            return; // Don't process this message until after resume
          }
        }
        
        // Handle resume boundary
        if (data.type === 'chat.resume_boundary') {
          resumePending = false;
        }
        
        // Production: Only handle chat.* namespace events
        if (callbacks.onMessage) callbacks.onMessage(data);
        
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error);
      }
    };

    socket.onerror = (error) => {
      console.error("WebSocket error:", error);
      if (callbacks.onError) callbacks.onError(error);
    };

    socket.onclose = () => {
      this._chatConnections.delete(chatId);
      if (callbacks.onClose) callbacks.onClose();
    };

    const connection = {
      chatId,
      socket,
      send: (message) => {
        if (socket.readyState === WebSocket.OPEN) {
          try {
            // Allow caller to pass objects; serialize to JSON
            if (typeof message === 'object') {
              socket.send(JSON.stringify(message));
            } else {
              socket.send(message);
            }
          } catch (e) {
            console.error('Failed to send WS message', e);
            return false;
          }
          return true;
        }
        return false;
      },
      close: () => {
        try {
          socket.close();
        } finally {
          this._chatConnections.delete(chatId);
        }
      }
    };
    this._chatConnections.set(chatId, connection);
    return connection;
  }

  async getMessageHistory(appId, userId) {
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(
        `${baseUrl}/api/chat/history/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}`
      , {}, this.config);
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to fetch message history:', error);
    }
    return [];
  }

  async uploadFile(file, appId, userId, options = {}) {
    const { chatId = null, intent = 'context', bundlePath = null } = options || {};
    if (!chatId) {
      return { success: false, error: 'chatId is required' };
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('appId', appId);
    formData.append('userId', userId);
    formData.append('chatId', chatId);
    if (intent) formData.append('intent', intent);
    if (bundlePath) formData.append('bundle_path', bundlePath);

    try {
      const baseUrl = this.getHttpBaseUrl();
      // Note: Don't set Content-Type for FormData - browser sets it with boundary
      const response = await authFetch(`${baseUrl}/api/chat/upload`, {
        method: 'POST',
        body: formData
      }, this.config);

      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('File upload failed:', error);
    }

    return { success: false, error: 'Upload failed' };
  }

  async getWorkflowTransport(workflowname) {
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/api/workflows/${encodeURIComponent(workflowname)}/transport`, {}, this.config);
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to get workflow transport:', error);
    }
    return null;
  }

  async startChat(appId, workflowname, userId, fetchOpts = {}, contextVariables = null, triggerMeta = null, sessionOptions = null) {
    const actualworkflowname = resolveWorkflow(workflowname);
    const clientRequestId = crypto?.randomUUID ? crypto.randomUUID() : (Date.now()+"-"+Math.random().toString(36).slice(2));
    const requestKey = this._getStartChatKey(appId, actualworkflowname, userId);


    return this._runStartChatRequest(requestKey, async () => {
      try {
        const baseUrl = this.getHttpBaseUrl();
        const body = { user_id: userId, client_request_id: clientRequestId };
        if (contextVariables && typeof contextVariables === 'object' && Object.keys(contextVariables).length > 0) {
          body.context_variables = contextVariables;
        }
        if (triggerMeta && typeof triggerMeta === 'object') {
          body.trigger_meta = triggerMeta;
        }
        if (sessionOptions?.transportPurpose && typeof sessionOptions.transportPurpose === 'string') {
          body.transport_purpose = sessionOptions.transportPurpose.trim();
        }
        if (sessionOptions?.forceNew === true) {
          body.force_new = true;
        }
        const response = await authFetch(`${baseUrl}/api/chats/${encodeURIComponent(appId)}/${encodeURIComponent(actualworkflowname)}/start`, {
          method: 'POST',
          headers: buildAuthHeaders(undefined, this.config),
          body: JSON.stringify(body),
          ...fetchOpts
        }, this.config);

        if (response.ok) {
          const result = await response.json();
          return result;
        } else {
          let detail = null;
          try {
            const errJson = await response.json();
            detail = errJson?.detail ?? errJson;
          } catch (_e) {
            try {
              detail = await response.text();
            } catch (_e2) {
              detail = null;
            }
          }

          console.error('Failed to start chat:', response.status, response.statusText, detail);
          return { success: false, error: `HTTP ${response.status}`, status: response.status, detail };
        }
      } catch (error) {
        console.error('Failed to start chat:', error);
        return { success: false, error: error.message };
      }
    });
  }
}

// REST API Adapter (alternative to WebSocket)
export class RestApiAdapter extends ApiAdapter {
  constructor(config) {
    super(config);
    this.config = config || {};
    this.activeChatSessions = new Map(); // Track active chat sessions to prevent duplicates
  }

  async sendMessage(message, appId, userId) {
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/api/chat/send`, {
        method: 'POST',
        headers: buildAuthHeaders(undefined, this.config),
        body: JSON.stringify({ message, appId, userId })
      }, this.config);

      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to send message:', error);
    }

    return { success: false, error: 'Failed to send message' };
  }

  async sendMessageToWorkflow(message, appId, userId, workflowname = null, chatId = null, context = null) {
    const actualworkflowname = resolveWorkflow(workflowname);
    
    if (!chatId) {
      console.error('Chat ID is required for sending message to workflow');
      return { success: false, error: 'Chat ID is required' };
    }
    
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/chat/${appId}/${chatId}/${userId}/input`, {
        method: 'POST',
        headers: buildAuthHeaders(undefined, this.config),
        body: JSON.stringify({ 
          message, 
          workflow_name: actualworkflowname,
          app_id: appId,
          user_id: userId,
          context: context || undefined,
        })
      }, this.config);

      if (response.ok) {
        const result = await response.json();
        return result;
      } else {
        console.error('Failed to send message:', response.status, response.statusText);
        return { success: false, error: `HTTP ${response.status}` };
      }
    } catch (error) {
      console.error('Failed to send message to workflow:', error);
      return { success: false, error: error.message };
    }
  }

  createWebSocketConnection() {
    // REST API adapter doesn't support WebSocket connections
    console.warn('WebSocket not supported in REST API adapter');
    return null;
  }

  async getMessageHistory(appId, userId) {
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(
        `${baseUrl}/api/chat/messages/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}`
      , {}, this.config);
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to fetch messages:', error);
    }
    return [];
  }

  async uploadFile(file, appId, userId, options = {}) {
    const { chatId = null, intent = 'context', bundlePath = null } = options || {};
    if (!chatId) {
      return { success: false, error: 'chatId is required' };
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('chatId', chatId);
    if (intent) formData.append('intent', intent);
    if (bundlePath) formData.append('bundle_path', bundlePath);

    try {
      const baseUrl = this.getHttpBaseUrl();
      // Note: Don't set Content-Type for FormData - browser sets it with boundary
      const response = await authFetch(
        `${baseUrl}/api/chat/upload/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}`,
        {
          method: 'POST',
          body: formData
        },
        this.config
      );

      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('File upload failed:', error);
    }

    return { success: false, error: 'Upload failed' };
  }

  async getWorkflowTransport(workflowname) {
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/api/workflows/${encodeURIComponent(workflowname)}/transport`, {}, this.config);
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to get workflow transport:', error);
    }
    return null;
  }

  async startChat(appId, workflowname, userId, fetchOpts = {}, contextVariables = null, triggerMeta = null, sessionOptions = null) {
    const actualworkflowname = resolveWorkflow(workflowname);
    const clientRequestId = crypto?.randomUUID ? crypto.randomUUID() : (Date.now()+"-"+Math.random().toString(36).slice(2));
    const requestKey = this._getStartChatKey(appId, actualworkflowname, userId);

    return this._runStartChatRequest(requestKey, async () => {
      try {
        const baseUrl = this.getHttpBaseUrl();
        const body = { user_id: userId, client_request_id: clientRequestId };
        if (contextVariables && typeof contextVariables === 'object' && Object.keys(contextVariables).length > 0) {
          body.context_variables = contextVariables;
        }
        if (triggerMeta && typeof triggerMeta === 'object') {
          body.trigger_meta = triggerMeta;
        }
        if (sessionOptions?.transportPurpose && typeof sessionOptions.transportPurpose === 'string') {
          body.transport_purpose = sessionOptions.transportPurpose.trim();
        }
        if (sessionOptions?.forceNew === true) {
          body.force_new = true;
        }
        const response = await authFetch(`${baseUrl}/api/chats/${encodeURIComponent(appId)}/${encodeURIComponent(actualworkflowname)}/start`, {
          method: 'POST',
          headers: buildAuthHeaders(undefined, this.config),
          body: JSON.stringify(body),
          ...fetchOpts
        }, this.config);

        if (response.ok) {
          const result = await response.json();
          return result;
        } else {
          console.error('Failed to start chat:', response.status, response.statusText);
          return { success: false, error: `HTTP ${response.status}` };
        }
      } catch (error) {
        console.error('Failed to start chat:', error);
        return { success: false, error: error.message };
      }
    });
  }
}

// Default API instance (app-scoped)
export const appApi = new RestApiAdapter(config.get ? config.get('api') : config?.config?.api);
