// Simple configuration for agentic chat platform
class ChatUIConfig {
  constructor() {
    this.config = this.loadConfig();
  }

  loadConfig() {
    const defaultAuthMode = 'token';
    const defaultHttpProtocol = typeof window !== 'undefined' ? window.location.protocol : 'http:';
    const defaultHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
    const defaultWsProtocol = defaultHttpProtocol === 'https:' ? 'wss:' : 'ws:';
    const defaultApiBaseUrl = `${defaultHttpProtocol}//${defaultHost}:8000`;
    const defaultWsBaseUrl = `${defaultWsProtocol}//${defaultHost}:8000`;
    
    const defaultConfig = {
      // API Configuration
      api: {
        baseUrl: process.env.REACT_APP_API_BASE_URL || defaultApiBaseUrl,
        wsUrl: process.env.REACT_APP_WS_URL || defaultWsBaseUrl,
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
        // Workflow resolution handled by resolveWorkflow() utility
        // (entry_point in orchestrator.yaml → singleton auto-select → null)
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
