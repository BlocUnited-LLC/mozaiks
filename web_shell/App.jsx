import {
  MozaiksApp,
  WebSocketApiAdapter,
  componentRegistry,
} from '@mozaiks/chat-ui';

// Active app UI barrel — resolved at build time via @platform/extensions.
// The selected app root owns custom route components, including any
// management surfaces it declares in its route manifest.
import * as platformExtensions from '@platform/extensions';

const { register, createAuthAdapter } = platformExtensions;

// Register active app UI extensions once.
// Core substrate components are always loaded from coreComponents.js.
register(componentRegistry.registerComponent.bind(componentRegistry));

// ── API adapter ────────────────────────────────────────────────────────────
// apiUrl and wsUrl come from the platform's app.json or env vars.
// Vite proxies /api and /ws to apiUrl during dev (see vite.config.js).
const apiAdapter = new WebSocketApiAdapter({
  baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  wsUrl:   import.meta.env.VITE_WS_URL  || 'ws://localhost:8000',
});

// ── Auth adapter ───────────────────────────────────────────────────────────
// Platform app exports createAuthAdapter() — uses OIDC when VITE_OIDC_AUTHORITY
// is set, falls back to mock adapter (VITE_MOCK_MODE=true or no authority).
// If the platform extension doesn't export createAuthAdapter, fall back to the
// development mock so the shell always works.
const fallbackMockAdapter = {
  isAuthenticated:   () => true,
  getCurrentUser:    () => Promise.resolve({ id: 'demo-user', name: 'Developer', email: 'demo@example.com', roles: ['admin', 'user'] }),
  getToken:          () => Promise.resolve('demo-token'),
  login:             () => Promise.resolve(),
  logout:            () => Promise.resolve(),
  onAuthStateChange: (callback) => {
    callback({ id: 'demo-user', name: 'Developer', email: 'demo@example.com', roles: ['admin', 'user'] });
    return () => {};
  },
  getAccessToken: () => 'demo-token',
  handleCallback: () => Promise.resolve(),
};

const authAdapter = typeof createAuthAdapter === 'function'
  ? createAuthAdapter()
  : fallbackMockAdapter;

export default function App() {
  return (
    <MozaiksApp
      apiAdapter={apiAdapter}
      authAdapter={authAdapter}
    />
  );
}
