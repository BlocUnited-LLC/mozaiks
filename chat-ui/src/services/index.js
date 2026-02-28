import config from '../config';
import { TokenAuthAdapter } from '../adapters/auth';
import { WebSocketApiAdapter, RestApiAdapter } from '../adapters/api';

class ChatUIServices {
  constructor() {
    this.authAdapter = null;
    this.apiAdapter = null;
    this.initialized = false;
  }

  initialize(options = {}) {
    if (this.initialized) return;

    // Initialize auth adapter
    this.authAdapter = this.createAuthAdapter(options.authAdapter);
    
    // Initialize API adapter
    this.apiAdapter = this.createApiAdapter(options.apiAdapter);

    this.initialized = true;
    console.log('ChatUI Services initialized');
  }

  createAuthAdapter(customAdapter) {
    if (customAdapter) return customAdapter;

    return new TokenAuthAdapter(config.get('api.baseUrl'));
  }

  createApiAdapter(customAdapter) {
    if (customAdapter) return customAdapter;
    const apiConfig = config.get('api');
    const hasWs = apiConfig.wsUrl;
    const hasHttp = apiConfig.baseUrl;

    // Prefer WebSocket adapter when wsUrl is configured
    if (hasWs) {
      return new WebSocketApiAdapter(apiConfig);
    }
    // Fallback to REST adapter when HTTP baseUrl is configured
    if (hasHttp) {
      return new RestApiAdapter(apiConfig);
    }
    // Default fallback
    return new RestApiAdapter(apiConfig);
  }

  getAuthAdapter() {
    return this.authAdapter;
  }

  getApiAdapter() {
    return this.apiAdapter;
  }

  // Convenience methods
  async getCurrentUser() {
    return this.authAdapter?.getCurrentUser();
  }

  createWebSocketConnection(appId, userId, callbacks, workflowName, chatId) {
    return this.apiAdapter?.createWebSocketConnection(appId, userId, callbacks, workflowName, chatId);
  }
}

// Singleton instance
const services = new ChatUIServices();

export default services;
