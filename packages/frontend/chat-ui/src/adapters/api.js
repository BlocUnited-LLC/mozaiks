// API adapter interface
import workflowConfig from '../config/workflowConfig';
import config from '../config';

/**
 * Get the current access token from storage.
 * In production, this should be provided by the auth adapter.
 * Falls back to localStorage for development/standalone mode.
 */
function getAccessToken() {
  // Try window.mozaiksAuth first (set by embedding app)
  if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
    return window.mozaiksAuth.getAccessToken();
  }
  
  // Fallback to localStorage (development/standalone)
  if (typeof localStorage !== 'undefined') {
    return localStorage.getItem('chatui_token') || localStorage.getItem('access_token');
  }
  
  return null;
}

/**
 * Build headers with Authorization if token available.
 * Always includes Content-Type for JSON requests.
 */
function buildAuthHeaders(contentType = 'application/json') {
  const headers = {};
  
  if (contentType) {
    headers['Content-Type'] = contentType;
  }
  
  const token = getAccessToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  return headers;
}

/**
 * Wrapper for fetch with automatic auth header injection.
 */
async function authFetch(url, options = {}) {
  const token = getAccessToken();
  
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
  }

  getHttpBaseUrl() {
    const fallback = typeof config?.get === 'function' ? config.get('api.baseUrl') : undefined;
    const raw = this.config?.baseUrl
      || this.config?.api?.baseUrl
      || fallback
      || 'http://localhost:8080';
    if (typeof raw === 'string' && raw.endsWith('/')) {
      return raw.slice(0, -1);
    }
    return raw;
  }

  getWsBaseUrl() {
    const fallback = typeof config?.get === 'function' ? config.get('api.wsUrl') : undefined;
    const raw = this.config?.wsUrl
      || this.config?.api?.wsUrl
      || fallback
      || 'ws://localhost:8080';
    if (typeof raw === 'string' && raw.endsWith('/')) {
      return raw.slice(0, -1);
    }
    return raw;
  }

  async sendMessage(_message, _appId, _userId) {
    throw new Error('sendMessage must be implemented');
  }

  async sendMessageToWorkflow(message, appId, userId, workflowname = null, chatId = null, context = null) {
    // Use dynamic default workflow type
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();
    console.log(`Sending message to workflow: ${actualworkflowname}`);
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
    const baseUrl = this.getHttpBaseUrl();
    const searchParams = new URLSearchParams();
    if (limit !== undefined && limit !== null) {
      searchParams.set('limit', String(limit));
    }

    const url = `${baseUrl}/api/general_chats/list/${encodeURIComponent(appId)}/${encodeURIComponent(userId)}?${searchParams.toString()}`;

    try {
      const response = await authFetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error('Failed to list general chats:', error);
      throw error;
    }
  }

  async fetchGeneralChatTranscript(appId, generalChatId, options = {}) {
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
      const response = await authFetch(url);
      if (response.status === 404) {
        return null;
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error('Failed to fetch general chat transcript:', error);
      throw error;
    }
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
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();

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
    // Use dynamic default workflow type
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();
    
    if (!chatId) {
      console.error('Chat ID is required for sending message to workflow');
      return { success: false, error: 'Chat ID is required' };
    }
    
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/chat/${appId}/${chatId}/${userId}/input`, {
        method: 'POST',
        headers: buildAuthHeaders(),
        body: JSON.stringify({ 
          message, 
          workflow_name: actualworkflowname,
          app_id: appId,
          user_id: userId,
          context: context || undefined,
        })
      });

      if (response.ok) {
        const result = await response.json();
        console.log('✅ Message sent to workflow:', result);
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
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();
    
    console.log('🛠️ [WS-CONN] WebSocket workflow resolution:', {
      provided: workflowname,
      fallback: workflowConfig.getDefaultWorkflow(), 
      actual: actualworkflowname,
      availableConfigs: workflowConfig.getAvailableWorkflows(),
      configs: workflowConfig.configs ? Array.from(workflowConfig.configs.entries()) : 'not available'
    });
    
    if (!chatId) {
      console.error('Chat ID is required for WebSocket connection');
      return null;
    }
    
    const wsBase = this.getWsBaseUrl();
    
    // Build WebSocket URL with access_token query param for authentication
    let wsUrl = `${wsBase}/ws/${actualworkflowname}/${appId}/${chatId}/${userId}`;
    const token = getAccessToken();
    if (token) {
      wsUrl += `?access_token=${encodeURIComponent(token)}`;
      console.log(`🔗 Connecting to WebSocket with auth token: ${wsUrl.split('?')[0]}?access_token=***`);
    } else {
      console.log(`🔗 Connecting to WebSocket (no auth token): ${wsUrl}`);
    }
    
    const socket = new WebSocket(wsUrl);
    
    // F7/F8: Sequence tracking and resume capability (strict canonical key)
    let lastSequence = parseInt(localStorage.getItem(`ws_idx_${chatId}`) || '0');
    let resumePending = false;
    
    // Helper to send client.resume
    const sendResume = () => {
      if (socket.readyState === WebSocket.OPEN && !resumePending) {
        resumePending = true;
        console.log(`📡 Sending client.resume with lastClientIndex: ${lastSequence}`);
        socket.send(JSON.stringify({
          type: 'client.resume',
          chat_id: chatId,
          lastClientIndex: lastSequence
        }));
      }
    };

    socket.onopen = () => {
      console.log("WebSocket connection established");
      
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
            try { localStorage.setItem(`ws_idx_${chatId}`, lastSequence.toString()); } catch (_) {}
          } else if (data.seq < lastSequence - 1 && !resumePending) {
            // Sequence gap detected - request resume
            console.warn(`⚠️ Sequence gap detected: received ${data.seq}, expected > ${lastSequence}`);
            sendResume();
            return; // Don't process this message until after resume
          }
        }
        
        // Handle resume boundary
        if (data.type === 'chat.resume_boundary') {
          console.log(`✅ Resume completed: ${data.data?.replayed_events || 0} events replayed`);
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
      console.log("WebSocket connection closed");
      this._chatConnections.delete(chatId);
      if (callbacks.onClose) callbacks.onClose();
    };

    const connection = {
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
      );
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
      });

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
      const response = await authFetch(`${baseUrl}/api/workflows/${encodeURIComponent(workflowname)}/transport`);
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to get workflow transport:', error);
    }
    return null;
  }

  async startChat(appId, workflowname, userId, fetchOpts = {}) {
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();
    const clientRequestId = crypto?.randomUUID ? crypto.randomUUID() : (Date.now()+"-"+Math.random().toString(36).slice(2));
    
    console.log('🛠️ [WS-API] startChat workflow resolution:', {
      provided: workflowname,
      fallback: workflowConfig.getDefaultWorkflow(), 
      actual: actualworkflowname,
      availableConfigs: workflowConfig.getAvailableWorkflows(),
      configs: workflowConfig.configs ? Array.from(workflowConfig.configs.entries()) : 'not available'
    });
    
    try {
      if (this._startingChat) {
        console.log('🛑 startChat skipped (already in progress)');
        return { success: false, error: 'in_progress' };
      }
      this._startingChat = true;
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/api/chats/${encodeURIComponent(appId)}/${encodeURIComponent(actualworkflowname)}/start`, {
        method: 'POST',
        headers: buildAuthHeaders(),
        body: JSON.stringify({ user_id: userId, client_request_id: clientRequestId }),
        ...fetchOpts
      });

      if (response.ok) {
        const result = await response.json();
        console.log('✅ Chat started:', result);
        this._startingChat = false;
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
        this._startingChat = false;
        return { success: false, error: `HTTP ${response.status}`, status: response.status, detail };
      }
    } catch (error) {
      console.error('Failed to start chat:', error);
      this._startingChat = false;
      return { success: false, error: error.message };
    }
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
        headers: buildAuthHeaders(),
        body: JSON.stringify({ message, appId, userId })
      });

      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to send message:', error);
    }

    return { success: false, error: 'Failed to send message' };
  }

  async sendMessageToWorkflow(message, appId, userId, workflowname = null, chatId = null, context = null) {
    // Use dynamic default workflow type
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();
    
    if (!chatId) {
      console.error('Chat ID is required for sending message to workflow');
      return { success: false, error: 'Chat ID is required' };
    }
    
    try {
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/chat/${appId}/${chatId}/${userId}/input`, {
        method: 'POST',
        headers: buildAuthHeaders(),
        body: JSON.stringify({ 
          message, 
          workflow_name: actualworkflowname,
          app_id: appId,
          user_id: userId,
          context: context || undefined,
        })
      });

      if (response.ok) {
        const result = await response.json();
        console.log('✅ Message sent to workflow:', result);
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
      );
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
        }
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
      const response = await authFetch(`${baseUrl}/api/workflows/${encodeURIComponent(workflowname)}/transport`);
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to get workflow transport:', error);
    }
    return null;
  }

  async startChat(appId, workflowname, userId, fetchOpts = {}) {
    const actualworkflowname = workflowname || workflowConfig.getDefaultWorkflow();
    const clientRequestId = crypto?.randomUUID ? crypto.randomUUID() : (Date.now()+"-"+Math.random().toString(36).slice(2));
    
    try {
      if (this._startingChat) {
        console.log('🛑 startChat skipped (already in progress)');
        return { success: false, error: 'in_progress' };
      }
      this._startingChat = true;
      const baseUrl = this.getHttpBaseUrl();
      const response = await authFetch(`${baseUrl}/api/chats/${encodeURIComponent(appId)}/${encodeURIComponent(actualworkflowname)}/start`, {
        method: 'POST',
        headers: buildAuthHeaders(),
        body: JSON.stringify({ user_id: userId, client_request_id: clientRequestId }),
        ...fetchOpts
      });

      if (response.ok) {
        const result = await response.json();
        console.log('✅ Chat started:', result);
        this._startingChat = false;
        return result;
      } else {
        console.error('Failed to start chat:', response.status, response.statusText);
        this._startingChat = false;
        return { success: false, error: `HTTP ${response.status}` };
      }
    } catch (error) {
      console.error('Failed to start chat:', error);
      this._startingChat = false;
      return { success: false, error: error.message };
    }
  }
}

// Default API instance (app-scoped)
export const appApi = new RestApiAdapter(config.get ? config.get('api') : config?.config?.api);
