export function register() {}

export function createAuthAdapter() {
  return {
    isAuthenticated: () => true,
    getCurrentUser: () =>
      Promise.resolve({
        id: 'demo-user',
        name: 'Demo User',
        email: 'demo@example.com',
        roles: ['admin', 'user'],
      }),
    getToken: () => Promise.resolve('demo-token'),
    login: () => Promise.resolve(),
    logout: () => Promise.resolve(),
    onAuthStateChange: (callback) => {
      callback({
        id: 'demo-user',
        name: 'Demo User',
        email: 'demo@example.com',
        roles: ['admin', 'user'],
      });
      return () => {};
    },
    getAccessToken: () => 'demo-token',
    handleCallback: () => Promise.resolve(),
  };
}
