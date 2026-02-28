// Authentication adapter interface
export class AuthAdapter {
  async getCurrentUser() {
    throw new Error('getCurrentUser must be implemented');
  }

  async login(credentials) {
    throw new Error('login must be implemented');
  }

  async logout() {
    throw new Error('logout must be implemented');
  }

  async refreshToken() {
    throw new Error('refreshToken must be implemented');
  }

  onAuthStateChange(callback) {
    throw new Error('onAuthStateChange must be implemented');
  }
  
  /**
   * Get the current access token for API calls.
   * This is used by api.js to add Authorization headers.
   */
  getAccessToken() {
    return null;
  }
}

// External Authentication Adapter (for embedded mode)
// Use this when ChatUI is embedded in MozaiksCore or another host app
// that provides authentication context
export class ExternalAuthAdapter extends AuthAdapter {
  constructor(externalAuthProvider) {
    super();
    this.externalAuth = externalAuthProvider;
  }

  async getCurrentUser() {
    return this.externalAuth.getCurrentUser();
  }

  async login(credentials) {
    return this.externalAuth.login(credentials);
  }

  async logout() {
    return this.externalAuth.logout();
  }

  async refreshToken() {
    return this.externalAuth.refreshToken();
  }
  
  getAccessToken() {
    // Delegate to external auth provider
    if (typeof this.externalAuth.getAccessToken === 'function') {
      return this.externalAuth.getAccessToken();
    }
    return null;
  }

  onAuthStateChange(callback) {
    return this.externalAuth.onAuthStateChange(callback);
  }
}

// Token-based Authentication Adapter
export class TokenAuthAdapter extends AuthAdapter {
  constructor(apiBaseUrl, tokenKey = 'chatui_token') {
    super();
    this.apiBaseUrl = apiBaseUrl;
    this.tokenKey = tokenKey;
    this.currentUser = null;
    this.authStateCallbacks = [];
  }

  async getCurrentUser() {
    if (this.currentUser) return this.currentUser;

    const token = this.getAccessToken();
    if (!token) return null;

    try {
      const response = await fetch(`${this.apiBaseUrl}/api/auth/me`, {
        headers: { Authorization: `Bearer ${token}` }
      });

      if (response.ok) {
        this.currentUser = await response.json();
        return this.currentUser;
      }
    } catch (error) {
      console.error('Auth error:', error);
    }

    return null;
  }

  async login(credentials) {
    try {
      const response = await fetch(`${this.apiBaseUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials)
      });

      if (response.ok) {
        const data = await response.json();
        localStorage.setItem(this.tokenKey, data.token);
        this.currentUser = data.user;
        this.notifyAuthStateChange(this.currentUser);
        return { success: true, user: this.currentUser };
      }
    } catch (error) {
      console.error('Login error:', error);
    }

    return { success: false, error: 'Login failed' };
  }

  async logout() {
    localStorage.removeItem(this.tokenKey);
    this.currentUser = null;
    this.notifyAuthStateChange(null);
    return { success: true };
  }

  async refreshToken() {
    // Implementation depends on your refresh token strategy
    return { success: true };
  }
  
  getAccessToken() {
    // Get token from localStorage
    return localStorage.getItem(this.tokenKey);
  }

  onAuthStateChange(callback) {
    this.authStateCallbacks.push(callback);
    // Immediately call with current state
    callback(this.currentUser);
  }

  notifyAuthStateChange(user) {
    this.authStateCallbacks.forEach(callback => callback(user));
  }
}
