/**
 * mockAuthAdapter — development stub auth adapter
 *
 * Provides a mock authenticated user for local development without Keycloak.
 * This allows testing the UI (including DashboardPage) without running the auth server.
 *
 * The mock user is an admin by default so you can test all dashboard features.
 */

const MOCK_USER = {
  id: 'dev-user-001',
  user_id: 'dev-user-001',
  name: 'Dev User',
  email: 'dev@mozaiks.local',
  roles: ['user', 'admin'],
  authenticated: true,
};

const mockAuthAdapter = {
  /** Auth config loaded from auth.json (or defaults) */
  authConfig: {
    roles: {
      claimPath: 'realm_access.roles',
      default: 'user',
      admin: 'admin',
      adminEmails: ['dev@mozaiks.local'],
    },
  },

  /** Returns the mock user (sync) */
  getUser() {
    return MOCK_USER;
  },

  /** Returns the mock user (async - used by ChatUIContext) */
  async getCurrentUser() {
    return MOCK_USER;
  },

  /** Always authenticated in mock mode */
  isAuthenticated() {
    return true;
  },

  /** No-op login - already "logged in" */
  async login() {
    console.log('[mockAuth] login() called - already authenticated');
    return true;
  },

  /** Logout just logs a message */
  async logout() {
    console.log('[mockAuth] logout() called');
    window.location.href = '/';
  },

  /** Returns a fake token for API calls */
  async getToken() {
    return 'mock-dev-token';
  },

  /** No-op destroy */
  destroy() {
    console.log('[mockAuth] destroy() called');
  },

  /** Identify as mock adapter */
  isMock: true,
};

export default mockAuthAdapter;

/**
 * Factory function matching createKeycloakAuthAdapter signature
 */
export function createMockAuthAdapter() {
  console.log('[mockAuth] Using mock auth adapter (no Keycloak)');
  return Promise.resolve(mockAuthAdapter);
}
