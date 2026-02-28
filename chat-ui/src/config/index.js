// Simple configuration for agentic chat platform
class ChatUIConfig {
  constructor() {
    this.config = this.loadConfig();
  }

  loadConfig() {
    const defaultAuthMode = 'token';
    
    const defaultConfig = {
      // API Configuration
      api: {
        baseUrl: process.env.REACT_APP_API_BASE_URL || 'http://localhost:8080',
        wsUrl: process.env.REACT_APP_WS_URL || 'ws://localhost:8080',
      },

      // Auth Configuration
      auth: {
        mode: defaultAuthMode,
      },

      // UI Configuration
      ui: {
        showHeader: process.env.REACT_APP_SHOW_HEADER !== 'false',
        enableNotifications: process.env.REACT_APP_ENABLE_NOTIFICATIONS !== 'false',
      },

      // Chat Configuration
      chat: {
        // Auth system configured via runtime auth endpoints
        defaultAppId: process.env.REACT_APP_DEFAULT_APP_ID || process.env.REACT_APP_DEFAULT_app_id,
        defaultUserId: process.env.REACT_APP_DEFAULT_USER_ID || '56132',
        // Do not force a placeholder workflow; fall back to backend discovery
        defaultWorkflow: process.env.REACT_APP_DEFAULT_WORKFLOW || '',
      },
    };

    // Override with window.ChatUIConfig if available
    if (typeof window !== 'undefined' && window.ChatUIConfig) {
      return { ...defaultConfig, ...window.ChatUIConfig };
    }

    return defaultConfig;
  }

  get(path) {
    return path.split('.').reduce((current, key) => current?.[key], this.config);
  }

  getConfig() {
    return this.config;
  }
}

// Singleton instance
const configInstance = new ChatUIConfig();

export default configInstance;
