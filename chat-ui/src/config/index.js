import platform from '../platform/index.js';

// Simple configuration for agentic chat platform
class ChatUIConfig {
  constructor() {
    this.config = this.loadConfig();
  }

  loadConfig() {
    const defaultAuthMode = 'token';
    const defaultApiBaseUrl = platform.resolveHttpUrl({ port: '8000' });
    const defaultWsBaseUrl = platform.resolveWsUrl({ port: '8000' });
    
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
        // (backend entry_point derived from platform/config/ai.json → singleton auto-select → null)
      },
    };

    const overrides = platform.getConfigOverrides();
    if (overrides) {
      return { ...defaultConfig, ...overrides };
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
