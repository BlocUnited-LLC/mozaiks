import config from '../config';
import { WebSocketApiAdapter, RestApiAdapter } from '../adapters/api';

/**
 * Simple service layer for chat-ui
 */
class ChatUIServices {
  constructor() {
    this.apiAdapter = null;
    this.initialized = false;
  }

  initialize(options = {}) {
    if (this.initialized) return;

    this.apiAdapter = this.createApiAdapter(options.apiAdapter);
    this.initialized = true;
  }

  createApiAdapter(customAdapter) {
    if (customAdapter) return customAdapter;

    const apiConfig = config.get('api');
    const hasWs = apiConfig.wsUrl;

    // Prefer WebSocket adapter when wsUrl is configured
    if (hasWs) {
      return new WebSocketApiAdapter(apiConfig);
    }

    return new RestApiAdapter(apiConfig);
  }

  getApiAdapter() {
    return this.apiAdapter;
  }

  createWebSocketConnection(appId, userId, callbacks, workflowName, chatId, options = {}) {
    return this.apiAdapter?.createWebSocketConnection(appId, userId, callbacks, workflowName, chatId, options);
  }
}

const services = new ChatUIServices();
export default services;
