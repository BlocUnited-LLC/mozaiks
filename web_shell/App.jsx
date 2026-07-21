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
const apiBaseUrl = import.meta.env.VITE_API_URL ?? '';
const wsBaseUrl = import.meta.env.VITE_WS_URL || (
  apiBaseUrl ? apiBaseUrl.replace(/^http/, 'ws') : undefined
);

const apiAdapter = new WebSocketApiAdapter({
  // Empty string is intentional: deployed single-origin apps should call
  // /api on the current host. Local Vite dev proxies that relative path.
  baseUrl: apiBaseUrl,
  ...(wsBaseUrl ? { wsUrl: wsBaseUrl } : {}),
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
