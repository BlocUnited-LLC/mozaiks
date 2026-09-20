// Explicit backend-authorized local identity for UI fixtures, matching shell.auth.
export const localDevelopmentAuth = {
  required: false,
  contract: null,
  frontend: null,
  runtime: {
    enabled: false,
    provider: 'none',
    local_development: true,
    user: {
      id: 'demo-user',
      user_id: 'demo-user',
      name: 'Developer',
      email: 'demo@example.com',
      roles: ['admin', 'user'],
      scopes: [],
      app_id: null,
      tenant_id: null,
      workspace_id: null,
    },
  },
};
