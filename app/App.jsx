import {
  MozaiksApp,
  WebSocketApiAdapter,
  componentRegistry,
} from '@mozaiks/chat-ui';

// Platform extension — registered via @platform/extensions alias in vite.config.js.
// Resolved at build time from PLATFORM_PATH to the active platform's ui/index.js.
// Default OSS apps use platform/extensions.js (a no-op stub).
// Users never touch this file — switch platforms by changing PLATFORM_PATH in .env.
import { register } from '@platform/extensions';

// Register all platform pages and components into the shell's component registry.
// Routes are composed by the backend and served by /api/shell-config.
register(componentRegistry.registerComponent.bind(componentRegistry));

// ── API adapter ────────────────────────────────────────────────────────────
// apiUrl and wsUrl come from the platform's app.json or env vars.
// Vite proxies /api and /ws to apiUrl during dev (see vite.config.js).
const apiAdapter = new WebSocketApiAdapter({
  baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  wsUrl:   import.meta.env.VITE_WS_URL  || 'ws://localhost:8000',
});

// ── Auth adapter ───────────────────────────────────────────────────────────
// Development mock — replaced by Keycloak adapter in production.
// Auth configuration is declared in platform/app.json (authRequired, auth.provider).
const mockAuthAdapter = {
  isAuthenticated:    () => true,
  getCurrentUser:     () => Promise.resolve({ id: 'demo-user', firstName: 'Demo', lastName: 'User', email: 'demo@example.com' }),
  getToken:           () => Promise.resolve('demo-token'),
  login:              () => Promise.resolve(),
  logout:             () => Promise.resolve(),
  onAuthStateChange:  (callback) => {
    callback({ id: 'demo-user', firstName: 'Demo', lastName: 'User', email: 'demo@example.com' });
    return () => {};
  },
};

export default function App() {
  return (
    <MozaiksApp
      apiAdapter={apiAdapter}
      authAdapter={mockAuthAdapter}
    />
  );
}
