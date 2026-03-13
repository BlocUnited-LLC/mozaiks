/**
 * mockAuthAdapter — development stub auth adapter
 *
 * Provides a mock authenticated user for local development without Keycloak.
 * This allows testing UI and workflow flows while auth infrastructure is off.
 */

const DEFAULT_USER = {
  id: 'dev-user-001',
  user_id: 'dev-user-001',
  name: 'Dev User',
  email: 'dev@mozaiks.local',
  roles: ['user', 'admin'],
  authenticated: true,
};

function resolveMockUser(appConfig = {}) {
  const devUsers = Array.isArray(appConfig?.dev?.users) ? appConfig.dev.users : [];
  const devUser = devUsers[0] || {};
  const username = devUser.username || 'dev';
  const userId = devUser.user_id || devUser.id || username || DEFAULT_USER.user_id;
  const firstName = devUser.firstName || devUser.first_name || '';
  const lastName = devUser.lastName || devUser.last_name || '';
  const displayName = `${firstName} ${lastName}`.trim() || username || DEFAULT_USER.name;
  const roles = Array.isArray(devUser.roles) && devUser.roles.length > 0
    ? devUser.roles
    : DEFAULT_USER.roles;

  return {
    id: userId,
    user_id: userId,
    name: displayName,
    email: devUser.email || DEFAULT_USER.email,
    roles,
    authenticated: true,
  };
}

function resolveRolesConfig(appConfig = {}, user) {
  const authRoles = appConfig?.auth?.roles || {};
  const adminEmails = Array.isArray(authRoles.adminEmails) && authRoles.adminEmails.length > 0
    ? authRoles.adminEmails
    : (user.email ? [user.email] : ['dev@mozaiks.local']);

  return {
    claimPath: authRoles.claimPath || 'realm_access.roles',
    default: authRoles.default || 'user',
    admin: authRoles.admin || 'admin',
    adminEmails,
  };
}

function buildMockAdapter(appConfig = {}) {
  const user = resolveMockUser(appConfig);
  const token = appConfig?.dev?.mockAccessToken || 'mock-dev-token';
  const rolesConfig = resolveRolesConfig(appConfig, user);

  if (typeof window !== 'undefined') {
    window.mozaiksAuth = {
      token,
      getAccessToken: () => token,
    };
  }

  return {
    authConfig: { roles: rolesConfig },

    getUser() {
      return user;
    },

    async getCurrentUser() {
      return user;
    },

    isAuthenticated() {
      return true;
    },

    async login() {
      console.log('[mockAuth] login() called - already authenticated');
      return true;
    },

    async logout() {
      console.log('[mockAuth] logout() called');
      if (typeof window !== 'undefined') {
        window.location.href = '/';
      }
    },

    async getToken() {
      return token;
    },

    getAccessToken() {
      return token;
    },

    destroy() {
      console.log('[mockAuth] destroy() called');
    },

    isMock: true,
  };
}

const mockAuthAdapter = buildMockAdapter();

export default mockAuthAdapter;

/**
 * Factory function matching createKeycloakAuthAdapter signature.
 */
export function createMockAuthAdapter(appConfig = {}) {
  const adapter = buildMockAdapter(appConfig);
  const user = adapter.getUser();
  console.log(`[mockAuth] Using mock auth adapter as "${user.name}" (${user.email})`);
  return Promise.resolve(adapter);
}

